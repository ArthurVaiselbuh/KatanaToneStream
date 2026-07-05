"""Tests for llm_generator — 3-phase generation plus the refinement chat.

Phase 1 (character) is free-form prose; phases 2 (types) and 3 (values) are
structured JSON. The phase-1 analysis must be woven into both later prompts.
ToneSession records every turn and refine() re-sends the full history.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from katana_tonestream.llm_generator import PatchGenerationError, ToneSession, generate_patch

_CHARACTER = (
    "High-gain rhythm tone with scooped mids, tight palm-muted low end and a "
    "touch of plate reverb. A tube screamer style booster tightens the amp."
)

_TYPES = {
    "preamp_type": 3,
    "od_type": 7,
    "fx1_type": 14,
    "fx2_type": 3,
    "delay_type": 0,
    "reverb_type": 3,
}

_VALUES = {
    "preamp_gain": 95,
    "bass": 60,
    "mid": 40,
    "treble": 70,
    "presence": 55,
    "od_on": True,
    "od_drive": 80,
    "od_level": 60,
    "fx1_on": True,
    "fx2_on": False,
    "delay_on": False,
    "delay_level": 50,
    "reverb_on": True,
    "reverb_level": 40,
    "confidence": 80,
}


def _make_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _happy_side_effect():
    return [
        _make_response(_CHARACTER),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]


@patch("litellm.completion")
def test_happy_path(mock_completion):
    mock_completion.side_effect = _happy_side_effect()
    result = generate_patch("Metallica", "Master of Puppets", "key", "openai/gpt-4o")

    assert result["preamp_type"] == 3
    assert result["od_type"] == 7
    assert result["fx1_type"] == 14
    assert result["delay_type"] == 0
    assert result["reverb_type"] == 3
    assert result["preamp_gain"] == 95
    assert result["od_drive"] == 80
    assert result["bass"] == 60
    assert result["od_on"] is True
    assert result["reverb_on"] is True
    assert result["confidence"] == 80
    assert mock_completion.call_count == 3


@patch("litellm.completion")
def test_character_analysis_is_a_real_assistant_turn(mock_completion):
    # The redesign keeps the phase-1 analysis as an assistant message in context
    # rather than pasting it into the later prompts. So it must appear as an
    # assistant turn in the type and value calls, not inside their user prompt.
    mock_completion.side_effect = _happy_side_effect()
    generate_patch("A", "B", "", "")
    for call_idx in (1, 2):
        msgs = mock_completion.call_args_list[call_idx].kwargs["messages"]
        assert any("scooped mids" in m["content"] for m in msgs if m["role"] == "assistant")
        # Not re-embedded into the new user turn.
        assert "scooped mids" not in msgs[-1]["content"]


@patch("litellm.completion")
def test_extra_request_persists_in_conversation(mock_completion):
    # The user's extra request is stated once (opening turn) and stays in context,
    # so it appears somewhere in every subsequent call's message list.
    mock_completion.side_effect = _happy_side_effect()
    generate_patch("A", "B", "", "", extra="brighter, less gain")
    for call in mock_completion.call_args_list:
        msgs = call.kwargs["messages"]
        assert any("brighter, less gain" in m["content"] for m in msgs)


@patch("litellm.completion")
def test_no_extra_request_leaves_prompts_clean(mock_completion):
    mock_completion.side_effect = _happy_side_effect()
    generate_patch("A", "B", "", "")
    for call in mock_completion.call_args_list:
        for msg in call.kwargs["messages"]:
            assert "Additional user request" not in msg["content"]


@patch("litellm.completion")
def test_progress_called_three_times(mock_completion):
    mock_completion.side_effect = _happy_side_effect()
    progress_msgs = []
    generate_patch("A", "B", "", "", on_progress=progress_msgs.append)
    assert len(progress_msgs) == 3
    assert all(isinstance(m, str) and m for m in progress_msgs)


@patch("litellm.completion")
def test_empty_character_phase_raises(mock_completion):
    mock_completion.return_value = _make_response("")
    with pytest.raises(PatchGenerationError):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_types_phase_json_failure_raises(mock_completion):
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response("oops"),
        _make_response("still not json"),  # corrective retry also fails
    ]
    with pytest.raises(PatchGenerationError):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_values_phase_json_failure_raises(mock_completion):
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response(json.dumps(_TYPES)),
        _make_response("oops"),
        _make_response("still not json"),  # corrective retry also fails
    ]
    with pytest.raises(PatchGenerationError):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_invalid_model_gives_clear_message(mock_completion):
    import litellm

    mock_completion.side_effect = litellm.NotFoundError(
        message="model not found, blah blah huge provider json blob " * 20,
        model="gemini/gemini-3.1-pro",
        llm_provider="gemini",
    )
    with pytest.raises(PatchGenerationError) as exc_info:
        generate_patch("A", "B", "key", "gemini/gemini-3.1-pro")
    msg = str(exc_info.value)
    assert "gemini/gemini-3.1-pro" in msg
    assert "Fetch available models" in msg
    # The raw provider blob must NOT leak through.
    assert "blah blah huge provider json blob blah" not in msg


@patch("litellm.completion")
def test_auth_error_gives_clear_message(mock_completion):
    import litellm

    mock_completion.side_effect = litellm.AuthenticationError(
        message="invalid api key 401", model="openai/gpt-4o", llm_provider="openai"
    )
    with pytest.raises(PatchGenerationError, match="API key"):
        generate_patch("A", "B", "bad-key", "openai/gpt-4o")


@patch("litellm.completion")
def test_non_json_error_message_is_short(mock_completion):
    huge_blob = "x" * 5000
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response(huge_blob),
        _make_response(huge_blob),
    ]
    with pytest.raises(PatchGenerationError) as exc_info:
        generate_patch("A", "B", "", "")
    # Message stays concise — the 5000-char blob is truncated, not dumped.
    assert len(str(exc_info.value)) < 400


@patch("litellm.completion")
def test_type_values_snapped_to_catalog(mock_completion):
    out_of_range = dict(
        _TYPES,
        preamp_type=99,  # beyond max → snaps to 32
        od_type=25,  # firmware range but unnamed → snaps to 21 (Custom)
        fx1_type=999,  # beyond max → snaps to 40 (Pedal Bend)
        fx2_type=34,  # hidden GT type → snaps to 35 (Phaser 90E)
        reverb_type=50,  # beyond max → snaps to 6
    )
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response(json.dumps(out_of_range)),
        _make_response(json.dumps(_VALUES)),
    ]
    result = generate_patch("A", "B", "", "")
    assert result["preamp_type"] == 32
    assert result["od_type"] == 21
    assert result["fx1_type"] == 40
    assert result["fx2_type"] == 35
    assert result["reverb_type"] == 6


@patch("litellm.completion")
def test_hidden_preamp_gap_snaps_to_nearest(mock_completion):
    # 27 is not in PREAMP_TYPES (26/27 unknown); nearest offered id is 28.
    gap_types = dict(_TYPES, preamp_type=27)
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response(json.dumps(gap_types)),
        _make_response(json.dumps(_VALUES)),
    ]
    result = generate_patch("A", "B", "", "")
    assert result["preamp_type"] == 28


@patch("litellm.completion")
def test_continuous_values_clamped(mock_completion):
    out_of_range_values = dict(_VALUES, preamp_gain=999, bass=-5, confidence=200)
    mock_completion.side_effect = [
        _make_response(_CHARACTER),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(out_of_range_values)),
    ]
    result = generate_patch("A", "B", "", "")
    assert result["preamp_gain"] == 120
    assert result["bass"] == 0
    assert result["confidence"] == 100


@patch("litellm.completion")
def test_default_model_used_when_empty(mock_completion):
    mock_completion.side_effect = _happy_side_effect()
    generate_patch("A", "B", "key", "")
    for c in mock_completion.call_args_list:
        assert c.kwargs["model"] == "openai/gpt-4o"


@patch("litellm.completion")
def test_timeout_passed_to_every_call(mock_completion):
    mock_completion.side_effect = _happy_side_effect()
    generate_patch("A", "B", "", "openai/gpt-4o", timeout=42.0)
    assert mock_completion.call_count == 3
    for c in mock_completion.call_args_list:
        assert c.kwargs["timeout"] == 42.0


@patch("litellm.completion")
def test_provider_timeout_gives_clear_message(mock_completion):
    import litellm

    # A hung provider (e.g. Ollama loading a model) surfaces as litellm.Timeout;
    # it must raise a clear message, not hang, so the UI can re-enable Generate.
    mock_completion.side_effect = litellm.Timeout(
        message="request timed out", model="ollama/llama3", llm_provider="ollama"
    )
    with pytest.raises(PatchGenerationError, match="timed out"):
        generate_patch("A", "B", "", "ollama/llama3", timeout=1.0)


# ── ToneSession: history recording + refinement chat ─────────────────────────


def _refine_response(message, params=None):
    d = {"message": message}
    if params is not None:
        d["params"] = params
    return _make_response(json.dumps(d))


def _generated_session(mock_completion, extra_responses=()):
    mock_completion.side_effect = [*_happy_side_effect(), *extra_responses]
    session = ToneSession(api_key="", model="openai/gpt-4o")
    session.generate("Metallica", "Master of Puppets")
    return session


@patch("litellm.completion")
def test_session_generate_records_history(mock_completion):
    entries_seen = []
    mock_completion.side_effect = _happy_side_effect()
    session = ToneSession(api_key="", model="openai/gpt-4o", on_entry=entries_seen.append)
    session.generate("A", "B")

    assert [e.role for e in session.history] == ["user", "assistant"] * 3
    user_entries = session.history[::2]
    assert all(e.auto for e in user_entries)
    assert user_entries[0].label.startswith("Phase 1/3")
    assert user_entries[1].label.startswith("Phase 2/3")
    assert user_entries[2].label.startswith("Phase 3/3")
    # on_entry streamed every recorded entry, in order.
    assert entries_seen == session.history
    # api_messages strips to plain role/content for the provider.
    assert all(set(m) == {"role", "content"} for m in session.api_messages())


@patch("litellm.completion")
def test_on_request_fires_before_each_reply(mock_completion):
    events = []
    mock_completion.side_effect = _happy_side_effect()
    session = ToneSession(
        api_key="",
        model="openai/gpt-4o",
        on_entry=lambda e: events.append(("entry", e)),
        on_request=lambda e: events.append(("request", e)),
    )
    session.generate("A", "B")

    # Per phase: request(user), then entry(user) + entry(assistant) on success —
    # and the request carries the very same entry object that gets committed.
    assert [kind for kind, _ in events] == ["request", "entry", "entry"] * 3
    for i in range(0, 9, 3):
        assert events[i][1] is events[i + 1][1]


@patch("litellm.completion")
def test_on_request_fires_even_when_call_fails(mock_completion):
    requests = []
    mock_completion.side_effect = RuntimeError("boom")
    session = ToneSession(api_key="", model="openai/gpt-4o", on_request=requests.append)
    with pytest.raises(PatchGenerationError):
        session.generate("A", "B")

    # The dispatch notification fired, but nothing was committed.
    assert len(requests) == 1
    assert requests[0].role == "user"
    assert session.history == []


@patch("litellm.completion")
def test_refine_happy_path(mock_completion):
    session = _generated_session(
        mock_completion,
        [_refine_response("Brighter now", {"preamp_gain": 130, "od_type": 25, "treble": 80})],
    )
    msg, partial = session.refine("brighter please", {"preamp_gain": 90})

    assert msg == "Brighter now"
    # 130 clamped to 120; od_type 25 (unnamed firmware id) snapped to 21 (Custom).
    assert partial == {"preamp_gain": 120, "od_type": 21, "treble": 80}
    last_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    # One shared system prompt + full 6-turn generation history + the new user turn.
    assert last_msgs[0]["role"] == "system"
    assert len(last_msgs) == 1 + 6 + 1
    assert "brighter please" in last_msgs[-1]["content"]
    assert '"preamp_gain": 90' in last_msgs[-1]["content"]


@patch("litellm.completion")
def test_refine_without_params_returns_none(mock_completion):
    session = _generated_session(mock_completion, [_refine_response("Sounds right as is.")])
    msg, partial = session.refine("is this OK?", {})
    assert msg == "Sounds right as is."
    assert partial is None


@patch("litellm.completion")
def test_refine_unknown_params_dropped(mock_completion):
    session = _generated_session(
        mock_completion, [_refine_response("Done", {"wah_level": 3, "bass": 70})]
    )
    _, partial = session.refine("more wah", {})
    assert partial == {"bass": 70}


@patch("litellm.completion")
def test_refine_unusable_value_skipped(mock_completion):
    session = _generated_session(
        mock_completion, [_refine_response("Done", {"preamp_gain": "loud"})]
    )
    _, partial = session.refine("louder", {})
    assert partial is None


@patch("litellm.completion")
def test_refine_missing_message_raises(mock_completion):
    session = _generated_session(
        mock_completion, [_make_response(json.dumps({"params": {"bass": 70}}))]
    )
    with pytest.raises(PatchGenerationError, match="could not be used"):
        session.refine("more bass", {})


@patch("litellm.completion")
def test_refine_invalid_json_raises(mock_completion):
    session = _generated_session(
        mock_completion, [_make_response("nope"), _make_response("still nope")]
    )
    with pytest.raises(PatchGenerationError):
        session.refine("more bass", {})


@patch("litellm.completion")
def test_second_refine_includes_first_refine_turns(mock_completion):
    session = _generated_session(
        mock_completion,
        [_refine_response("Brighter", {"treble": 80}), _refine_response("Even brighter")],
    )
    session.refine("brighter", {})
    session.refine("more", {})
    last_msgs = mock_completion.call_args_list[-1].kwargs["messages"]
    # system + 6 generation turns + 2 first-refine turns + new user turn.
    assert len(last_msgs) == 1 + 6 + 2 + 1
    assert any("brighter" in m["content"] for m in last_msgs if m["role"] == "user")


@patch("litellm.completion")
def test_refine_provider_failure_records_nothing(mock_completion):
    import litellm

    session = _generated_session(mock_completion)
    mock_completion.side_effect = litellm.Timeout(
        message="request timed out", model="openai/gpt-4o", llm_provider="openai"
    )
    with pytest.raises(PatchGenerationError):
        session.refine("brighter", {})
    # Nothing recorded → retrying re-sends a clean history.
    assert len(session.history) == 6


@patch("litellm.completion")
def test_refine_display_fields(mock_completion):
    session = _generated_session(mock_completion, [_refine_response("Raised the treble.")])
    session.refine("brighter please", {"treble": 50})
    user_entry, assistant_entry = session.history[-2:]
    # The user bubble shows the typed text, not the wrapped prompt with the
    # params snapshot; the assistant bubble shows the message, not raw JSON.
    assert user_entry.display == "brighter please"
    assert user_entry.content != "brighter please"
    assert not user_entry.auto
    assert assistant_entry.display == "Raised the treble."
