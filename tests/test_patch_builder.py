"""Tests for patch_builder — byte-level field encoding.

Byte positions authoritative from address_map.js (Tone Studio config).
Patch_0 layout:
  [0]  = PRM_ODDS_SW      (od_on)
  [1]  = PRM_ODDS_TYPE    (od_type)
  [17] = PRM_PREAMP_A_TYPE  (preamp_type)
  [18] = PRM_PREAMP_A_GAIN  (preamp_gain)
  [20] = PRM_PREAMP_A_BASS
  [21] = PRM_PREAMP_A_MIDDLE
  [22] = PRM_PREAMP_A_TREBLE
"""

import copy

from katana_tonestream.models import KatanaPatch, PatchMeta
from katana_tonestream.patch_builder import build_raw_bytes, get_template, to_alb_bytes

# Patch_0 needs >= 24 bytes (presence at index 23); Patch_1 >= 9 (reverb level at index 8);
# Status >= 13 (variation at index 12).
_TEMPLATE = {
    "UserPatch%PatchName": ["20"] * 16,
    "UserPatch%Patch_0": ["00"] * 24,
    "UserPatch%Patch_1": ["00"] * 16,
    "UserPatch%Fx(1)": ["00"] * 64,
    "UserPatch%Fx(2)": ["00"] * 64,
    "UserPatch%Delay(1)": ["00"] * 32,
    "UserPatch%Delay(2)": ["00"] * 32,
    "UserPatch%Status": ["00"] * 18,
}


def _patch(**kwargs) -> KatanaPatch:
    defaults = dict(
        preamp_type=4,
        preamp_gain=90,
        bass=70,
        mid=60,
        treble=80,
        presence=50,
        od_type=6,
        od_on=True,
        od_drive=85,
        od_level=55,
        variation=1,
        fx1_type=14,
        fx1_on=True,
        fx2_type=3,
        fx2_on=False,
        reverb_on=True,
        reverb_type=1,
        reverb_level=45,
        delay_on=False,
        delay_type=2,
        delay_level=65,
    )
    defaults.update(kwargs)
    meta = PatchMeta(
        id="test", name="Test", author="", source="generated", rating=0.0, download_url=""
    )
    return KatanaPatch(meta=meta, raw_params={}, patch_name="Test Patch", **defaults)


def test_od_on_and_type():
    rb = build_raw_bytes(_patch(od_on=True, od_type=7), copy.deepcopy(_TEMPLATE))
    p0 = rb["UserPatch%Patch_0"]
    assert int(p0[0], 16) == 1  # PRM_ODDS_SW
    assert int(p0[1], 16) == 7  # PRM_ODDS_TYPE


def test_od_off():
    rb = build_raw_bytes(_patch(od_on=False), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Patch_0"][0], 16) == 0


def test_preamp_type_and_gain():
    rb = build_raw_bytes(_patch(preamp_type=11, preamp_gain=80), copy.deepcopy(_TEMPLATE))
    p0 = rb["UserPatch%Patch_0"]
    assert int(p0[17], 16) == 11  # PRM_PREAMP_A_TYPE
    assert int(p0[18], 16) == 80  # PRM_PREAMP_A_GAIN


def test_eq_bytes():
    rb = build_raw_bytes(_patch(bass=10, mid=64, treble=90), copy.deepcopy(_TEMPLATE))
    p0 = rb["UserPatch%Patch_0"]
    assert int(p0[20], 16) == 10  # PRM_PREAMP_A_BASS
    assert int(p0[21], 16) == 64  # PRM_PREAMP_A_MIDDLE (capture-confirmed)
    assert int(p0[22], 16) == 90  # PRM_PREAMP_A_TREBLE


def test_od_drive_and_level():
    rb = build_raw_bytes(_patch(od_drive=100, od_level=70), copy.deepcopy(_TEMPLATE))
    p0 = rb["UserPatch%Patch_0"]
    assert int(p0[2], 16) == 100  # PRM_ODDS_DRIVE
    assert int(p0[7], 16) == 70  # PRM_ODDS_EFFECT_LEVEL


def test_unowned_od_bytes_not_stomped_by_eq():
    # OD/DS bytes we DON'T own (3-6, 8-14) must never be written by EQ.
    untouched = [3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14]
    template = copy.deepcopy(_TEMPLATE)
    for i in untouched:
        template["UserPatch%Patch_0"][i] = "AA"
    rb = build_raw_bytes(_patch(bass=50, mid=50, treble=50), template)
    p0 = rb["UserPatch%Patch_0"]
    for i in untouched:
        assert p0[i] == "AA", f"byte {i} was overwritten"


def test_fx_on_off():
    rb = build_raw_bytes(_patch(fx1_on=True, fx2_on=False), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Fx(1)"][0], 16) == 1
    assert int(rb["UserPatch%Fx(2)"][0], 16) == 0


def test_delay_on_off():
    rb = build_raw_bytes(_patch(delay_on=True), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Delay(1)"][0], 16) == 1
    assert int(rb["UserPatch%Delay(2)"][0], 16) == 1


def test_reverb_on_off():
    rb_on = build_raw_bytes(_patch(reverb_on=True), copy.deepcopy(_TEMPLATE))
    rb_off = build_raw_bytes(_patch(reverb_on=False), copy.deepcopy(_TEMPLATE))
    assert int(rb_on["UserPatch%Patch_1"][0], 16) == 1  # addr 0x60000540 capture-confirmed
    assert int(rb_off["UserPatch%Patch_1"][0], 16) == 0


def test_patch_name_encoded():
    p = _patch()
    p.patch_name = "Hello"
    rb = build_raw_bytes(p, copy.deepcopy(_TEMPLATE))
    name_bytes = bytes(int(h, 16) for h in rb["UserPatch%PatchName"])
    assert name_bytes[:5] == b"Hello"


def test_patch_name_non_ascii_stays_in_byte_range():
    # A generated "Artist – Song" name uses an en-dash (U+2013); every name byte
    # must still fit in a single byte so the SysEx frame can be built.
    p = _patch()
    p.patch_name = "Seether – Careless ☃"
    rb = build_raw_bytes(p, copy.deepcopy(_TEMPLATE))
    vals = [int(h, 16) for h in rb["UserPatch%PatchName"]]
    assert all(0 <= v <= 0xFF for v in vals)
    # en-dash collapses to "-" (name is capped at 16 bytes), and bytes() must not raise.
    assert bytes(vals) == b"Seether - Carele"


def test_template_sections_not_in_patch_are_untouched():
    template = copy.deepcopy(_TEMPLATE)
    template["UserPatch%KnobAsgn"] = ["AB"] * 48
    rb = build_raw_bytes(_patch(), template)
    assert rb["UserPatch%KnobAsgn"] == ["AB"] * 48


def test_to_alb_bytes_is_valid_json():
    import json

    rb = build_raw_bytes(_patch(), copy.deepcopy(_TEMPLATE))
    alb_bytes = to_alb_bytes(rb)
    alb = json.loads(alb_bytes)
    assert alb["device"] == "KATANA MkII"
    assert len(alb["userPatch"]) == 1


def test_presence_written():
    rb = build_raw_bytes(_patch(presence=75), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Patch_0"][23], 16) == 75  # PRM_PREAMP_A_PRESENCE


def test_fx_types_written():
    rb = build_raw_bytes(_patch(fx1_type=14, fx2_type=3), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Fx(1)"][1], 16) == 14  # Phaser
    assert int(rb["UserPatch%Fx(2)"][1], 16) == 3  # Compressor


def test_delay_type_written():
    rb = build_raw_bytes(_patch(delay_type=4), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Delay(1)"][1], 16) == 4  # Tape delay
    assert int(rb["UserPatch%Delay(2)"][1], 16) == 4  # both delay blocks


def test_reverb_type_written():
    rb_spring = build_raw_bytes(_patch(reverb_type=3), copy.deepcopy(_TEMPLATE))
    rb_hall = build_raw_bytes(_patch(reverb_type=1), copy.deepcopy(_TEMPLATE))
    assert int(rb_spring["UserPatch%Patch_1"][1], 16) == 3  # Spring
    assert int(rb_hall["UserPatch%Patch_1"][1], 16) == 1  # Hall


def test_delay_level_written():
    rb = build_raw_bytes(_patch(delay_level=90), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Delay(1)"][6], 16) == 90  # PRM_DLY_COMMON_EFFECT_LEVEL
    assert int(rb["UserPatch%Delay(2)"][6], 16) == 90


def test_reverb_level_written():
    rb = build_raw_bytes(_patch(reverb_level=75), copy.deepcopy(_TEMPLATE))
    assert int(rb["UserPatch%Patch_1"][8], 16) == 75  # PRM_REVERB_EFFECT_LEVEL


def test_variation_written():
    rb1 = build_raw_bytes(_patch(variation=1), copy.deepcopy(_TEMPLATE))
    rb0 = build_raw_bytes(_patch(variation=0), copy.deepcopy(_TEMPLATE))
    assert int(rb1["UserPatch%Status"][12], 16) == 1  # PRM_LED_STATE_VARI (capture 0x6000065C)
    assert int(rb0["UserPatch%Status"][12], 16) == 0


def test_presence_not_stomped_by_treble():
    template = copy.deepcopy(_TEMPLATE)
    template["UserPatch%Patch_0"][23] = "FF"
    rb = build_raw_bytes(_patch(presence=40), template)
    assert int(rb["UserPatch%Patch_0"][23], 16) == 40


def test_fx_type_not_stomped_by_fx_on():
    template = copy.deepcopy(_TEMPLATE)
    template["UserPatch%Fx(1)"][1] = "FF"
    rb = build_raw_bytes(_patch(fx1_type=7), template)
    assert int(rb["UserPatch%Fx(1)"][1], 16) == 7


def test_get_template_returns_bundled_clean_base():
    # The bundled clean Tone Studio base is used regardless of cache contents.
    t = get_template()
    assert t is not None
    assert "UserPatch%Patch_0" in t
    assert "UserPatch%Status" in t
    # Clean base captured with variation on: Patch_0[17]=0x1D (29), Status[12]=0x01.
    assert int(t["UserPatch%Patch_0"][17], 16) == 0x1D
    assert int(t["UserPatch%Status"][12], 16) == 0x01
