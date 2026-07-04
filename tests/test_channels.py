"""Tests for katana_channels — the real Katana Mk2 PC→channel recall map.

Ground truth: address_map.js prm_prop_midi defaults —
RxPcACh1-4 = PC 0-3, RxPcPanel = PC 4, RxPcBCh1-4 = PC 5-8.
"""

import pytest

from katana_tonestream import katana_channels as kc


def test_100w_has_eight_channels_with_noncontiguous_pcs():
    chans = kc.channels_for_model("100")
    assert [name for name, _ in chans] == [
        "A: CH-1",
        "A: CH-2",
        "A: CH-3",
        "A: CH-4",
        "B: CH-1",
        "B: CH-2",
        "B: CH-3",
        "B: CH-4",
    ]
    # PC 4 is PANEL, not a channel — bank B starts at 5.
    assert [pc for _, pc in chans] == [0, 1, 2, 3, 5, 6, 7, 8]
    assert 4 not in [pc for _, pc in chans]


def test_50w_has_four_channels_two_per_bank():
    chans = kc.channels_for_model("50")
    assert [name for name, _ in chans] == ["A: CH-1", "A: CH-2", "B: CH-1", "B: CH-2"]
    # Same PC scheme as the 100 W (bank B starts at 5); 50 W just omits CH-3/CH-4.
    assert [pc for _, pc in chans] == [0, 1, 5, 6]


def test_channel_rows_are_grouped_by_bank():
    rows = kc.channel_rows_for_model("50")
    assert len(rows) == 2  # bank A row, bank B row
    assert [name for name, _ in rows[0]] == ["A: CH-1", "A: CH-2"]
    assert [name for name, _ in rows[1]] == ["B: CH-1", "B: CH-2"]
    # 100 W keeps two bank rows too, 4 channels each.
    rows_100 = kc.channel_rows_for_model("100")
    assert [len(r) for r in rows_100] == [4, 4]


@pytest.mark.parametrize("raw", ["", "bogus", "50W", "50", None])
def test_normalize_model_defaults_to_50(raw):
    assert kc.normalize_model(raw) == "50"


@pytest.mark.parametrize("raw", ["100", "100W", "100w", " 100 "])
def test_normalize_model_accepts_100(raw):
    assert kc.normalize_model(raw) == "100"


def test_pc_for_name_round_trips():
    assert kc.pc_for_name("B: CH-1", "100") == 5
    assert kc.pc_for_name("b:ch-1", "100") == 5  # case/space-insensitive
    assert kc.pc_for_name("A: CH-4", "50") is None  # 50 W has no CH-3/CH-4
    assert kc.pc_for_name("B: CH-2", "50") == 6
    assert kc.pc_for_name("Z: CH-9", "100") is None


def test_name_for_pc():
    assert kc.name_for_pc(5, "100") == "B: CH-1"
    assert kc.name_for_pc(6, "50") == "B: CH-2"
    assert kc.name_for_pc(4, "100") == "PC4"  # PANEL — not a target channel
    assert kc.name_for_pc(3, "50") == "PC3"  # CH-4 doesn't exist on the 50 W
    assert kc.name_for_pc(99, "100") == "PC99"
