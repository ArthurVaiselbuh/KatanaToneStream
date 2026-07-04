"""Tests for SlotPicker.set_model — the settings-pane model switch rebuilds the map."""

from katana_tonestream.ui.slot_picker import SlotPicker


class _FakePage:
    def update(self, *a, **k):
        pass


def test_set_model_resets_target_when_channel_gone():
    # A: CH-4 = PC 3 exists on the 100 W but not the 50 W.
    sp = SlotPicker(_FakePage(), initial=3, model="100")
    assert sp.target == 3
    sp.set_model("50")
    assert sp.target is None  # PC 3 is not a 50 W channel → reset to TONE
    assert sp._display() == "→ TONE"


def test_set_model_keeps_target_when_channel_still_exists():
    # B: CH-1 = PC 5 exists on both models.
    sp = SlotPicker(_FakePage(), initial=5, model="100")
    sp.set_model("50")
    assert sp.target == 5
    assert sp._display() == "→ B: CH-1"


def test_set_model_expands_rows_for_100w():
    sp = SlotPicker(_FakePage(), initial=None, model="50")
    assert [len(r) for r in sp._bank_rows] == [2, 2]
    sp.set_model("100")
    assert [len(r) for r in sp._bank_rows] == [4, 4]
