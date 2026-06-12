"""Parse Boss Tone Studio .tsl and .alb patch files into KatanaPatch objects."""

import json
from pathlib import Path

from .models import KatanaPatch, PatchMeta


def _make_meta_local(patch_id: str, name: str) -> PatchMeta:
    return PatchMeta(
        id=patch_id,
        name=name,
        author="",
        source="local",
        rating=0.0,
        download_url="",
        cached=True,
    )


def _decode_patch_name(name_hex: list[str], fallback: str = "") -> str:
    """Decode a ``UserPatch%PatchName`` hex-string array into an ASCII name."""
    try:
        return (
            bytes(int(h, 16) for h in name_hex)
            .rstrip(b"\x00 ")
            .decode("ascii", errors="replace")
            .strip()
        )
    except ValueError, TypeError:
        return fallback


def _apply_patch0(patch: KatanaPatch, patch_0: list[str]) -> None:
    """Decode known fields from UserPatch%Patch_0 hex array.

    Layout from address_map.js (Tone Studio config), Patch_0 base = 0x60000010:
      [0]  = PRM_ODDS_SW      — od_on (OD/DS pedal switch)
      [1]  = PRM_ODDS_TYPE    — od_type (OD/DS pedal type, 0-25)
      [2]  = PRM_ODDS_DRIVE   — od_drive (booster gain, 0-120)
      [7]  = PRM_ODDS_EFFECT_LEVEL — od_level (booster level, 0-100)
      [3-14] = OD/DS bottom, tone, solo, direct mix, etc.
      [15] = padding
      [17] = PRM_PREAMP_A_TYPE  — preamp_type (amp channel, 0-32)
      [18] = PRM_PREAMP_A_GAIN  — preamp_gain (0-120)
      [20] = PRM_PREAMP_A_BASS
      [21] = PRM_PREAMP_A_MIDDLE — capture-confirmed addr 0x60000025
      [22] = PRM_PREAMP_A_TREBLE
      [23] = PRM_PREAMP_A_PRESENCE (0-100)
    """
    if not patch_0:
        return
    try:
        if len(patch_0) > 0:
            patch.od_on = int(patch_0[0], 16) != 0
        if len(patch_0) > 1:
            patch.od_type = int(patch_0[1], 16)
        if len(patch_0) > 2:
            patch.od_drive = int(patch_0[2], 16)
        if len(patch_0) > 7:
            patch.od_level = int(patch_0[7], 16)
        if len(patch_0) > 17:
            patch.preamp_type = int(patch_0[17], 16)
        if len(patch_0) > 18:
            patch.preamp_gain = int(patch_0[18], 16)
        if len(patch_0) > 20:
            patch.bass = int(patch_0[20], 16)
        if len(patch_0) > 21:
            patch.mid = int(patch_0[21], 16)
        if len(patch_0) > 22:
            patch.treble = int(patch_0[22], 16)
        if len(patch_0) > 23:
            patch.presence = int(patch_0[23], 16)
    except ValueError, IndexError:
        pass


def _apply_fx(patch: KatanaPatch, param_set: dict) -> None:
    """Decode FX on/off and type from Fx(1) and Fx(2) bytes 0-1."""
    try:
        fx1 = param_set.get("UserPatch%Fx(1)", [])
        if len(fx1) > 0:
            patch.fx1_on = int(fx1[0], 16) != 0
        if len(fx1) > 1:
            patch.fx1_type = int(fx1[1], 16)
        fx2 = param_set.get("UserPatch%Fx(2)", [])
        if len(fx2) > 0:
            patch.fx2_on = int(fx2[0], 16) != 0
        if len(fx2) > 1:
            patch.fx2_type = int(fx2[1], 16)
    except ValueError, IndexError:
        pass


def _apply_delay(patch: KatanaPatch, param_set: dict) -> None:
    """Decode delay on/off and type from Delay(1) bytes 0-1."""
    try:
        d = param_set.get("UserPatch%Delay(1)", [])
        if len(d) > 0:
            patch.delay_on = int(d[0], 16) != 0
        if len(d) > 1:
            patch.delay_type = int(d[1], 16)
        if len(d) > 6:
            patch.delay_level = int(d[6], 16)
    except ValueError, IndexError:
        pass


def _apply_reverb(patch: KatanaPatch, param_set: dict) -> None:
    """Decode reverb on/off and type from UserPatch%Patch_1 bytes 0-1.

    Confirmed from reverb_on_off capture: addr 0x60000540 toggles on each reverb
    switch. Patch_1 base = 0x60000540 → byte [0] is on/off, byte [1] is type.
    """
    try:
        p1 = param_set.get("UserPatch%Patch_1", [])
        if len(p1) > 0:
            patch.reverb_on = int(p1[0], 16) != 0
        if len(p1) > 1:
            patch.reverb_type = int(p1[1], 16)
        if len(p1) > 8:
            patch.reverb_level = int(p1[8], 16)
    except ValueError, IndexError:
        pass


def _apply_status(patch: KatanaPatch, param_set: dict) -> None:
    """Decode the amp green/red variation from UserPatch%Status byte 12.

    Capture-confirmed: a channel switch sends Status[12] (PRM_LED_STATE_VARI,
    addr 0x6000065C) alongside the preamp type.
    """
    try:
        st = param_set.get("UserPatch%Status", [])
        if len(st) > 12:
            patch.variation = int(st[12], 16)
    except ValueError, IndexError:
        pass


def _patch_from_params(patch_id: str, display_name: str, params: dict) -> KatanaPatch:
    patch_name = str(params.get("patchname", display_name)).strip()
    return KatanaPatch(
        meta=_make_meta_local(patch_id, display_name),
        raw_params=params,
        patch_name=patch_name,
        preamp_type=int(params.get("preamp_a_type", 0)),
        preamp_gain=int(params.get("preamp_a_gain", 0)),
        bass=int(params.get("preamp_a_bass", 64)),
        mid=int(params.get("preamp_a_mid", 64)),
        treble=int(params.get("preamp_a_treble", 64)),
        od_type=int(params.get("od_ds_type", 0)),
        od_on=bool(params.get("od_ds_on_off", 0)),
        reverb_on=bool(params.get("reverb_on_off", 0)),
        reverb_type=int(params.get("reverb_type", 0)),
        delay_on=bool(params.get("delay_on_off", 0)),
    )


def parse_tsl(source: str | bytes | Path) -> list[KatanaPatch]:
    """Parse a Boss Tone Studio .tsl file (JSON) into a list of KatanaPatch objects.

    Handles two formats:
    - Local TSL: {"patchList": [...], "device": "GT", ...}
    - ToneExchange liveset: {"data": [[{"memo": ..., "paramSet": {...}}]], "device": "KATANA MkII"}
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            data = json.load(f)
    else:
        data = json.loads(source)

    # ToneExchange liveset format: top-level "data" key contains banks of patches
    if "data" in data and "patchList" not in data:
        return _parse_tsl_liveset(data)

    patches: list[KatanaPatch] = []
    for entry in data.get("patchList", []):
        if not entry:
            continue
        params = entry.get("params") or {}
        display_name = str(entry.get("name", "")).strip()
        patch_id = str(entry.get("id", ""))
        patches.append(_patch_from_params(patch_id, display_name, params))

    return patches


def _parse_tsl_liveset(data: dict) -> list[KatanaPatch]:
    """Parse ToneExchange liveset format (data[][].paramSet with UserPatch% keys)."""
    liveset_name = str(data.get("name", "")).strip()
    patches: list[KatanaPatch] = []

    for bank_idx, bank in enumerate(data.get("data", [])):
        if not isinstance(bank, list):
            continue
        for slot_idx, entry in enumerate(bank):
            if not entry or not isinstance(entry, dict):
                continue
            param_set: dict = entry.get("paramSet") or {}
            if not param_set:
                continue

            patch_name = _decode_patch_name(param_set.get("UserPatch%PatchName", []))
            display_name = (
                patch_name or entry.get("memo", "").strip() or f"{liveset_name} {slot_idx + 1}"
            )
            patch_id = f"bte_{bank_idx}_{slot_idx}"

            patch = KatanaPatch(
                meta=_make_meta_local(patch_id, display_name),
                raw_params={},
                patch_name=display_name,
                raw_bytes=param_set,
            )
            _apply_patch0(patch, param_set.get("UserPatch%Patch_0", []))
            _apply_fx(patch, param_set)
            _apply_delay(patch, param_set)
            _apply_reverb(patch, param_set)
            _apply_status(patch, param_set)
            patches.append(patch)

    return patches


def parse_alb(source: str | bytes | Path) -> list[KatanaPatch]:
    """Parse a Boss Tone Studio .alb backup file into a list of KatanaPatch objects."""
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            data = json.load(f)
    else:
        data = json.loads(source)

    patches: list[KatanaPatch] = []
    for i, entry in enumerate(data.get("userPatch", [])):
        if not entry:
            continue

        patch_name = _decode_patch_name(
            entry.get("UserPatch%PatchName", []), fallback=f"Patch {i + 1}"
        )
        patch = KatanaPatch(
            meta=_make_meta_local(f"alb_{i}", patch_name),
            raw_params={},
            patch_name=patch_name,
            raw_bytes=entry,
        )
        _apply_patch0(patch, entry.get("UserPatch%Patch_0", []))
        _apply_fx(patch, entry)
        _apply_delay(patch, entry)
        _apply_reverb(patch, entry)
        _apply_status(patch, entry)
        patches.append(patch)

    return patches
