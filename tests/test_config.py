"""Tests for config.midi_target_patch using a temp config.ini."""

import pytest

from katana_tonestream import config


def _write_config(app_home, body: str) -> None:
    (app_home / "config.ini").write_text(body, encoding="utf-8")
    config.reload()


@pytest.mark.parametrize(
    "slot,expected",
    [("A1", 0), ("A8", 7), ("B1", 8), ("E8", 39), ("a1", 0)],
)
def test_valid_slots(app_home, slot, expected):
    _write_config(app_home, f"[midi]\ntarget_patch = {slot}\n")
    assert config.midi_target_patch() == expected


def test_missing_key_returns_minus_one(app_home):
    _write_config(app_home, "[midi]\n")
    assert config.midi_target_patch() == -1


def test_junk_value_returns_minus_one(app_home):
    _write_config(app_home, "[midi]\ntarget_patch = ZZ\n")
    assert config.midi_target_patch() == -1


def test_out_of_range_returns_minus_one(app_home):
    _write_config(app_home, "[midi]\ntarget_patch = A9\n")
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
