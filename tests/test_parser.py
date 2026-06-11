"""Tests for the .tsl / .alb parsers."""

import glob
import json

import pytest

from katana_tonestream.parser import (
    _decode_patch_name,
    parse_alb,
    parse_tsl,
)


def test_parse_real_tsl(sample_tsl_bytes):
    patches = parse_tsl(sample_tsl_bytes)
    assert len(patches) >= 1
    assert patches[0].display_name


def test_parse_all_sample_tsl_files():
    files = glob.glob("prerequisites/**/*.tsl", recursive=True)
    if not files:
        pytest.skip("no sample .tsl files")
    for path in files:
        with open(path, "rb") as f:
            patches = parse_tsl(f.read())
        assert isinstance(patches, list)


def test_parse_alb_sample_if_present():
    files = glob.glob("prerequisites/**/*.alb", recursive=True)
    if not files:
        pytest.skip("no sample .alb files")
    with open(files[0], "rb") as f:
        patches = parse_alb(f.read())
    assert isinstance(patches, list)


def test_liveset_branch():
    # Patch_0 layout from address_map.js: [0]=od_on, [1]=od_type, [17]=preamp_type,
    # [18]=preamp_gain, [20]=bass, [21]=mid, [22]=treble.
    patch_0 = ["00"] * 23
    patch_0[0] = "01"  # od_on = True
    patch_0[1] = "05"  # od_type = 5
    patch_0[17] = "03"  # preamp_type = 3 (HiGain)
    patch_0[18] = "50"  # preamp_gain = 80
    patch_0[21] = "40"  # mid = 64
    status = ["00"] * 18
    status[12] = "01"  # variation = 1
    liveset = {
        "device": "KATANA MkII",
        "name": "MyLiveset",
        "data": [
            [
                {
                    "memo": "slot memo",
                    "paramSet": {
                        # "Test" = 0x54 0x65 0x73 0x74
                        "UserPatch%PatchName": ["54", "65", "73", "74"],
                        "UserPatch%Patch_0": patch_0,
                        "UserPatch%Status": status,
                    },
                }
            ]
        ],
    }
    patches = parse_tsl(json.dumps(liveset).encode())
    assert len(patches) == 1
    p = patches[0]
    assert p.display_name == "Test"
    assert p.raw_bytes is not None
    assert p.od_on is True
    assert p.od_type == 0x05
    assert p.preamp_type == 3
    assert p.preamp_gain == 0x50
    assert p.mid == 64
    assert p.variation == 1


def test_decode_patch_name():
    assert _decode_patch_name(["0x48", "0x69"]) == "Hi"
    assert _decode_patch_name(["0x48", "0x69", "0x00", "0x00"]) == "Hi"  # trailing nulls trimmed


def test_decode_patch_name_malformed_uses_fallback():
    assert _decode_patch_name(["zz"], fallback="X") == "X"
    assert _decode_patch_name(None, fallback="X") == "X"


def test_empty_input_returns_empty_list():
    assert parse_tsl(b"{}") == []
    assert parse_alb(b"{}") == []
