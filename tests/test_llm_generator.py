"""Tests for llm_generator.generate_patch — 3-phase flow."""

import json
from unittest.mock import MagicMock, patch

import pytest

from katana_tonestream.llm_generator import (
    NORMALIZATION_FACTOR,
    NORMALIZED_PARAMS,
    PatchGenerationError,
    _normalize_levels,
    generate_patch,
)

_CHARACTER = {
    "booster_on": True,
    "fx_on": True,
    "delay_on": False,
    "reverb_on": True,
    "overall_character": "High-gain lead tone with scooped mids",
    "key_traits": ["high gain", "mid-scooped", "spring reverb"],
}

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


@patch("litellm.completion")
def test_happy_path(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    result = generate_patch("Metallica", "Master of Puppets", "key", "openai/gpt-4o")

    assert result["preamp_type"] == 3
    assert result["od_type"] == 7
    assert result["fx1_type"] == 14
    assert result["delay_type"] == 0
    assert result["reverb_type"] == 3
    assert result["preamp_gain"] == round(95 * NORMALIZATION_FACTOR)  # drive params normalized
    assert result["od_drive"] == round(80 * NORMALIZATION_FACTOR)
    assert result["od_level"] == round(60 * NORMALIZATION_FACTOR)
    assert result["bass"] == 60  # EQ is not normalized
    assert result["od_on"] is True
    assert result["reverb_on"] is True
    assert result["confidence"] == 80
    assert mock_completion.call_count == 3


@patch("litellm.completion")
def test_three_llm_calls_made(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    generate_patch("A", "B", "", "")
    assert mock_completion.call_count == 3


@patch("litellm.completion")
def test_extra_request_woven_into_every_prompt(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    generate_patch("A", "B", "", "", extra="brighter, less gain")
    for call in mock_completion.call_args_list:
        user_msg = call.kwargs["messages"][-1]["content"]
        assert "brighter, less gain" in user_msg


@patch("litellm.completion")
def test_no_extra_request_leaves_prompts_clean(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    generate_patch("A", "B", "", "")
    for call in mock_completion.call_args_list:
        user_msg = call.kwargs["messages"][-1]["content"]
        assert "Additional user request" not in user_msg


@patch("litellm.completion")
def test_progress_called_three_times(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    progress_msgs = []
    generate_patch("A", "B", "", "", on_progress=progress_msgs.append)
    assert len(progress_msgs) == 3
    assert all(isinstance(m, str) and m for m in progress_msgs)


@patch("litellm.completion")
def test_character_phase_json_failure_raises(mock_completion):
    mock_completion.return_value = _make_response("not json at all")
    with pytest.raises(PatchGenerationError):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_character_phase_missing_field_raises(mock_completion):
    incomplete = {k: v for k, v in _CHARACTER.items() if k != "overall_character"}
    mock_completion.return_value = _make_response(json.dumps(incomplete))
    with pytest.raises(PatchGenerationError, match="overall_character"):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_types_phase_json_failure_raises(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response("oops"),
    ]
    with pytest.raises(PatchGenerationError):
        generate_patch("A", "B", "", "")


@patch("litellm.completion")
def test_values_phase_json_failure_raises(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response("oops"),
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
    mock_completion.return_value = _make_response(huge_blob)
    with pytest.raises(PatchGenerationError) as exc_info:
        generate_patch("A", "B", "", "")
    # Message stays concise — the 5000-char blob is truncated, not dumped.
    assert len(str(exc_info.value)) < 400


def test_normalize_levels_scales_only_power_params():
    values = {"preamp_gain": 120, "bass": 80, "mid": 40, "treble": 60, "od_on": True}
    out = _normalize_levels(values, factor=0.5)
    assert out["preamp_gain"] == 60  # scaled
    assert out["bass"] == 80  # EQ untouched
    assert out["mid"] == 40
    assert out["od_on"] is True  # booleans untouched


def test_normalize_levels_reclamps_to_range():
    # A factor > 1 must not push a normalized param past its max.
    out = _normalize_levels({"preamp_gain": 120}, factor=2.0)
    assert out["preamp_gain"] == NORMALIZED_PARAMS["preamp_gain"]


@patch("litellm.completion")
def test_generate_applies_normalization(mock_completion):
    high_gain = dict(_VALUES, preamp_gain=120)
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(high_gain)),
    ]
    result = generate_patch("A", "B", "", "")
    assert result["preamp_gain"] == round(120 * NORMALIZATION_FACTOR)


@patch("litellm.completion")
def test_type_values_clamped_to_catalog_ranges(mock_completion):
    out_of_range_types = dict(_TYPES, preamp_type=99, fx1_type=999, reverb_type=50)
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(out_of_range_types)),
        _make_response(json.dumps(_VALUES)),
    ]
    result = generate_patch("A", "B", "", "")
    assert result["preamp_type"] <= 15
    assert result["fx1_type"] <= 30
    assert result["reverb_type"] <= 6


@patch("litellm.completion")
def test_continuous_values_clamped(mock_completion):
    out_of_range_values = dict(_VALUES, preamp_gain=999, bass=-5, confidence=200)
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(out_of_range_values)),
    ]
    result = generate_patch("A", "B", "", "")
    # preamp_gain clamped 999→120, then normalized by the configured factor.
    assert result["preamp_gain"] == round(120 * NORMALIZATION_FACTOR)
    assert result["bass"] == 0
    assert result["confidence"] == 100


@patch("litellm.completion")
def test_default_model_used_when_empty(mock_completion):
    mock_completion.side_effect = [
        _make_response(json.dumps(_CHARACTER)),
        _make_response(json.dumps(_TYPES)),
        _make_response(json.dumps(_VALUES)),
    ]
    generate_patch("A", "B", "key", "")
    for c in mock_completion.call_args_list:
        assert c.kwargs["model"] == "openai/gpt-4o"
