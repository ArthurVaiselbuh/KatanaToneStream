"""Tests for config.midi_target_patch using a temp config.ini."""

import pytest

from katana_tonestream import config


def _write_config(app_home, body: str) -> None:
    (app_home / "config.ini").write_text(body, encoding="utf-8")
    config.reload()


# Default model is 50 W: A: CH-1/2 = PC 0-1, B: CH-1/2 = PC 5-6 (PC 4 is PANEL).
@pytest.mark.parametrize(
    "slot,expected",
    [("A: CH-1", 0), ("A: CH-2", 1), ("B: CH-1", 5), ("B: CH-2", 6), ("a:ch-1", 0)],
)
def test_valid_slots_50w_default(app_home, slot, expected):
    _write_config(app_home, f"[midi]\ntarget_patch = {slot}\n")
    assert config.midi_target_patch() == expected


@pytest.mark.parametrize(
    "slot,expected",
    [("A: CH-1", 0), ("A: CH-4", 3), ("B: CH-1", 5), ("B: CH-4", 8)],
)
def test_valid_slots_100w(app_home, slot, expected):
    _write_config(app_home, f"[midi]\namp_model = 100\ntarget_patch = {slot}\n")
    assert config.midi_target_patch() == expected


def test_100w_only_names_invalid_on_50w(app_home):
    # CH-3/CH-4 don't exist on the 50 W (2 channels per bank).
    _write_config(app_home, "[midi]\ntarget_patch = A: CH-4\n")  # default 50 W
    assert config.midi_target_patch() == -1


def test_amp_model_defaults_to_50w(app_home):
    _write_config(app_home, "")
    assert config.amp_model() == "50"


def test_amp_model_normalizes_junk_to_50w(app_home):
    _write_config(app_home, "[midi]\namp_model = bogus\n")
    assert config.amp_model() == "50"


def test_amp_model_accepts_100(app_home):
    _write_config(app_home, "[midi]\namp_model = 100\n")
    assert config.amp_model() == "100"


def test_set_amp_model_round_trip(app_home):
    _write_config(app_home, "")
    config.set_amp_model("100")
    assert config.amp_model() == "100"
    config.set_amp_model("50")
    assert config.amp_model() == "50"


def test_set_amp_model_normalizes_before_saving(app_home):
    _write_config(app_home, "")
    config.set_amp_model("100W")
    assert config.get("midi", "amp_model") == "100"


def test_missing_key_returns_minus_one(app_home):
    _write_config(app_home, "[midi]\n")
    assert config.midi_target_patch() == -1


def test_junk_value_returns_minus_one(app_home):
    _write_config(app_home, "[midi]\ntarget_patch = ZZ\n")
    assert config.midi_target_patch() == -1


def test_out_of_range_returns_minus_one(app_home):
    # CH-8 doesn't exist on either model (100 W tops out at CH-4).
    _write_config(app_home, "[midi]\namp_model = 100\ntarget_patch = A: CH-8\n")
    assert config.midi_target_patch() == -1


def test_default_llm_defaults(app_home):
    _write_config(app_home, "")
    assert config.default_llm_provider() == "openai"
    assert config.default_llm_model("gemini") == ""


def test_set_default_llm_round_trip(app_home):
    _write_config(app_home, "")
    config.set_default_llm("gemini", "gemini/gemini-2.5-pro")
    assert config.default_llm_provider() == "gemini"
    assert config.default_llm_model("gemini") == "gemini/gemini-2.5-pro"


def test_default_llm_model_is_per_provider(app_home):
    _write_config(app_home, "")
    config.set_default_llm("gemini", "gemini/gemini-flash-latest")
    config.set_default_llm("openai", "openai/gpt-4o")
    # Switching the default provider must not lose the other provider's last model.
    assert config.default_llm_model("gemini") == "gemini/gemini-flash-latest"
    assert config.default_llm_model("openai") == "openai/gpt-4o"
    assert config.default_llm_model("anthropic") == ""


def test_configured_providers_filters_by_key(monkeypatch):
    from katana_tonestream import llm_providers

    have = {"openai", "anthropic"}
    monkeypatch.setattr(config, "llm_api_key", lambda provider: "sk-x" if provider in have else "")
    keys = {k for _, k in llm_providers.configured_providers()}
    assert "openai" in keys and "anthropic" in keys
    assert "gemini" not in keys
    assert "ollama" in keys  # local, always available
