"""Build sendable raw_bytes dicts from KatanaPatch named fields.

Uses a clean Tone Studio base patch as a template — all sections are deep-copied
from the template and only the bytes at known positions are overwritten. Unknown
bytes keep the template values, which means the amp's non-targeted parameters (EQ
curves, effect depths, assignments, etc.) come from a known-good neutral patch.

The base template is bundled at ``assets/base_template.json`` (the paramSet of a
clean patch exported from Tone Studio, byte-verified against a real MIDI send).
"""

import copy
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from . import cache
from .parser import parse_alb, parse_tsl

if TYPE_CHECKING:
    from .models import KatanaPatch

log = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_BASE_TEMPLATE_PATH = _ASSETS_DIR / "base_template.json"

# Byte positions within UserPatch%Patch_0. Authoritative source: address_map.js
# from Tone Studio config. Patch_0 base = 0x60000010 (Roland addr 60 00 00 10).
# Bytes 0-14: OD/DS pedal; 15=padding; 16=unknown; 17+=Preamp A.
_PATCH0_OD_ON = 0  # PRM_ODDS_SW (0/1)
_PATCH0_OD_TYPE = 1  # PRM_ODDS_TYPE (0-25)
_PATCH0_OD_DRIVE = 2  # PRM_ODDS_DRIVE (0-120) — booster gain/drive
_PATCH0_OD_LEVEL = 7  # PRM_ODDS_EFFECT_LEVEL (0-100) — booster output level
_PATCH0_PREAMP_TYPE = 17  # PRM_PREAMP_A_TYPE (0-32) — amp channel (Crunch/HiGain/…)
_PATCH0_PREAMP_GAIN = 18  # PRM_PREAMP_A_GAIN (0-120)
_PATCH0_BASS = 20  # PRM_PREAMP_A_BASS (0-100)
_PATCH0_MID = 21  # PRM_PREAMP_A_MIDDLE (0-100) — capture-confirmed addr 60 00 00 25
_PATCH0_TREBLE = 22  # PRM_PREAMP_A_TREBLE (0-100)

_PATCH0_PRESENCE = 23  # PRM_PREAMP_A_PRESENCE (0-100)

_FX1_ON_OFF = 0  # UserPatch%Fx(1)[0] = PRM_FX1_SW
_FX1_TYPE = 1  # UserPatch%Fx(1)[1] = PRM_FX1_FXTYPE (0-40)
_FX2_ON_OFF = 0  # UserPatch%Fx(2)[0] = PRM_FX1_SW (second block)
_FX2_TYPE = 1  # UserPatch%Fx(2)[1] = PRM_FX1_FXTYPE
_DELAY_ON_OFF = 0  # UserPatch%Delay(1/2)[0] = PRM_DLY_SW
_DELAY_TYPE = 1  # UserPatch%Delay(1)[1] = PRM_DLY_TYPE (0-10)
_DELAY_LEVEL = 6  # UserPatch%Delay(1)[6] = PRM_DLY_COMMON_EFFECT_LEVEL (0-120)
_PATCH1_REVERB_ON = 0  # UserPatch%Patch_1[0] = PRM_REVERB_SW (capture: addr 60 00 05 40)
_PATCH1_REVERB_TYPE = 1  # UserPatch%Patch_1[1] = PRM_REVERB_TYPE (0-6)
_PATCH1_REVERB_LEVEL = 8  # UserPatch%Patch_1[8] = PRM_REVERB_EFFECT_LEVEL (0-100)

# Amp character green/red variation. Confirmed sent on channel switch (capture:
# addr 60 00 06 5C). Tone follows PREAMP_A_TYPE; this is the panel LED/voicing flag.
_STATUS_VARIATION = 12  # UserPatch%Status[12] = PRM_LED_STATE_VARI (0/1)


def _set_byte(section: list[str], index: int, value: int) -> None:
    if index < len(section):
        section[index] = f"{value & 0xFF:02X}"


def _name_bytes(name: str) -> list[str]:
    # The patch name field is a 16-byte ASCII region. Any non-ASCII char (e.g. an
    # en-dash "–" from a generated "Artist – Song" name) would overflow a byte, so
    # collapse to ASCII first. Common dashes map to "-"; anything else becomes "?".
    cleaned = name.translate({0x2013: "-", 0x2014: "-", 0x2012: "-", 0x2212: "-"})
    ascii_name = cleaned.encode("ascii", errors="replace").decode("ascii")
    padded = ascii_name[:16].ljust(16)
    return [f"{ord(c):02X}" for c in padded]


def build_raw_bytes(patch: "KatanaPatch", template: dict) -> dict:
    """Return a deep-copy of template with all known KatanaPatch fields written in."""
    rb = copy.deepcopy(template)

    rb["UserPatch%PatchName"] = _name_bytes(patch.patch_name or patch.meta.name or "Generated")

    if "UserPatch%Patch_0" in rb:
        p0 = list(rb["UserPatch%Patch_0"])
        _set_byte(p0, _PATCH0_OD_ON, 0x01 if patch.od_on else 0x00)
        _set_byte(p0, _PATCH0_OD_TYPE, patch.od_type)
        _set_byte(p0, _PATCH0_OD_DRIVE, patch.od_drive)
        _set_byte(p0, _PATCH0_OD_LEVEL, patch.od_level)
        _set_byte(p0, _PATCH0_PREAMP_TYPE, patch.preamp_type)
        _set_byte(p0, _PATCH0_PREAMP_GAIN, patch.preamp_gain)
        _set_byte(p0, _PATCH0_BASS, patch.bass)
        _set_byte(p0, _PATCH0_MID, patch.mid)
        _set_byte(p0, _PATCH0_TREBLE, patch.treble)
        _set_byte(p0, _PATCH0_PRESENCE, patch.presence)
        rb["UserPatch%Patch_0"] = p0

    if "UserPatch%Fx(1)" in rb:
        fx1 = list(rb["UserPatch%Fx(1)"])
        _set_byte(fx1, _FX1_ON_OFF, 0x01 if patch.fx1_on else 0x00)
        _set_byte(fx1, _FX1_TYPE, patch.fx1_type)
        rb["UserPatch%Fx(1)"] = fx1

    if "UserPatch%Fx(2)" in rb:
        fx2 = list(rb["UserPatch%Fx(2)"])
        _set_byte(fx2, _FX2_ON_OFF, 0x01 if patch.fx2_on else 0x00)
        _set_byte(fx2, _FX2_TYPE, patch.fx2_type)
        rb["UserPatch%Fx(2)"] = fx2

    if "UserPatch%Delay(1)" in rb:
        d = list(rb["UserPatch%Delay(1)"])
        _set_byte(d, _DELAY_ON_OFF, 0x01 if patch.delay_on else 0x00)
        _set_byte(d, _DELAY_TYPE, patch.delay_type)
        _set_byte(d, _DELAY_LEVEL, patch.delay_level)
        rb["UserPatch%Delay(1)"] = d

    # Delay(2) is an independent second delay unit that runs simultaneously
    # with Delay(1) (address_map.js: 0x520 vs 0x500). Writing the generated
    # settings to both would stack two identical delays, so the single
    # generated delay lives on Delay(1) and Delay(2) is forced off.
    if "UserPatch%Delay(2)" in rb:
        d2 = list(rb["UserPatch%Delay(2)"])
        _set_byte(d2, _DELAY_ON_OFF, 0x00)
        rb["UserPatch%Delay(2)"] = d2

    if "UserPatch%Patch_1" in rb:
        p1 = list(rb["UserPatch%Patch_1"])
        _set_byte(p1, _PATCH1_REVERB_ON, 0x01 if patch.reverb_on else 0x00)
        _set_byte(p1, _PATCH1_REVERB_TYPE, patch.reverb_type)
        _set_byte(p1, _PATCH1_REVERB_LEVEL, patch.reverb_level)
        rb["UserPatch%Patch_1"] = p1

    if "UserPatch%Status" in rb:
        st = list(rb["UserPatch%Status"])
        _set_byte(st, _STATUS_VARIATION, patch.variation)
        rb["UserPatch%Status"] = st

    return rb


def _load_base_template() -> dict | None:
    """Load the bundled clean Tone Studio base patch (assets/base_template.json)."""
    try:
        with open(_BASE_TEMPLATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        log.warning("Bundled base template missing/invalid at %s", _BASE_TEMPLATE_PATH)
        return None
    if not isinstance(data, dict) or "UserPatch%Patch_0" not in data:
        log.warning("Bundled base template has unexpected structure")
        return None
    return data


def _cached_template() -> dict | None:
    """Fallback: raw_bytes from the most-recently-used cached real patch, or None."""
    for meta in cache.get_cached_patches():
        if meta.source == "generated":
            continue
        raw = cache.get_patch_bytes(meta.id)
        if not raw:
            continue
        try:
            patches = parse_tsl(raw) or parse_alb(raw)
        except Exception:
            continue
        for p in patches:
            if p.raw_bytes:
                log.debug("Using cached '%s' as patch template (no bundled base)", meta.name)
                return p.raw_bytes
    return None


def get_template() -> dict | None:
    """Return the raw_bytes template that generated patches are built on.

    Prefers the bundled clean Tone Studio base patch (a neutral, known-good
    starting point). Falls back to a real cached patch only if the bundled base
    is unavailable.
    """
    base = _load_base_template()
    if base is not None:
        return base
    log.warning("Falling back to cached patch as template")
    return _cached_template()


def to_alb_bytes(raw_bytes: dict) -> bytes:
    """Serialise a raw_bytes dict as a minimal .alb JSON blob."""
    alb = {
        "formatRev": "0002",
        "device": "KATANA MkII",
        "model": "KATANA-100MkII",
        "userPatch": [raw_bytes],
    }
    return json.dumps(alb).encode()
