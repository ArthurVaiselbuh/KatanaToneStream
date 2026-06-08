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
    except (ValueError, TypeError):
        return fallback


def _apply_patch0(patch: KatanaPatch, patch_0: list[str]) -> None:
    """Pull preamp type/gain out of a raw ``UserPatch%Patch_0`` hex block, if present."""
    if len(patch_0) < 2:
        return
    try:
        patch.preamp_type = int(patch_0[1], 16) & 0x0F
        patch.preamp_gain = int(patch_0[2], 16) if len(patch_0) > 2 else 0
    except (ValueError, IndexError):
        pass


def _patch_from_params(patch_id: str, display_name: str, params: dict) -> KatanaPatch:
    patch_name = str(params.get("patchname", display_name)).strip()
    return KatanaPatch(
        meta=_make_meta_local(patch_id, display_name),
        raw_params=params,
        patch_name=patch_name,
        preamp_type=int(params.get("preamp_a_type", 0)),
        preamp_gain=int(params.get("preamp_a_gain", 0)),
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
                patch_name
                or entry.get("memo", "").strip()
                or f"{liveset_name} {slot_idx + 1}"
            )
            patch_id = f"bte_{bank_idx}_{slot_idx}"

            patch = KatanaPatch(
                meta=_make_meta_local(patch_id, display_name),
                raw_params={},
                patch_name=display_name,
                raw_bytes=param_set,
            )
            _apply_patch0(patch, param_set.get("UserPatch%Patch_0", []))
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
        patches.append(patch)

    return patches


def parse_file(path: str | Path) -> list[KatanaPatch]:
    """Auto-detect format from extension and parse."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".tsl":
        return parse_tsl(path)
    if suffix == ".alb":
        return parse_alb(path)
    raise ValueError(f"Unsupported file type: {suffix}")
