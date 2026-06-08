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
    liveset = {
        "device": "KATANA MkII",
        "name": "MyLiveset",
        "data": [
            [
                {
                    "memo": "slot memo",
                    "paramSet": {
                        # "Test" = 0x54 0x65 0x73 0x74
                        "UserPatch%PatchName": ["0x54", "0x65", "0x73", "0x74"],
                        "UserPatch%Patch_0": ["0x01", "0x05", "0x32"],
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
    assert p.preamp_type == 0x05 & 0x0F
    assert p.preamp_gain == 0x32


def test_decode_patch_name():
    assert _decode_patch_name(["0x48", "0x69"]) == "Hi"
    assert _decode_patch_name(["0x48", "0x69", "0x00", "0x00"]) == "Hi"  # trailing nulls trimmed


def test_decode_patch_name_malformed_uses_fallback():
    assert _decode_patch_name(["zz"], fallback="X") == "X"
    assert _decode_patch_name(None, fallback="X") == "X"


def test_empty_input_returns_empty_list():
    assert parse_tsl(b"{}") == []
    assert parse_alb(b"{}") == []
