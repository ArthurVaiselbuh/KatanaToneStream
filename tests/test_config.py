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
