"""Validates that assets/address_map.js is present and structurally correct.

Only verifies that the expected Boss Katana Mk2 sections and type-range markers
exist. Does not parse type names — those live in katana_catalog.py.
"""

from pathlib import Path


class AddressMapError(Exception):
    pass


_REQUIRED_MARKERS = [
    "prm_prop_patch_0",
    "prm_prop_patch_fx",
    "prm_prop_patch_delay",
    "prm_prop_patch_1",
    "PRM_PREAMP_A_TYPE",
    "PRM_FX1_FXTYPE",
    "PRM_DLY_TYPE",
    "PRM_REVERB_TYPE",
]


def load_and_validate(path: Path) -> None:
    """Read address_map.js and verify it contains the expected Katana Mk2 structure.

    Raises AddressMapError if the file is missing, empty, or lacks required section markers.
    """
    if not path.exists():
        raise AddressMapError(f"address_map.js not found at {path}")

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AddressMapError(f"Cannot read address_map.js: {exc}") from exc

    if not text.strip():
        raise AddressMapError("address_map.js is empty")

    missing = [m for m in _REQUIRED_MARKERS if m not in text]
    if missing:
        raise AddressMapError(
            f"address_map.js is missing expected Katana Mk2 markers: {', '.join(missing)}"
        )
