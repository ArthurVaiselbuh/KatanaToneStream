"""Boss Katana Mk2 channel (tone-setting memory) model.

The amp recalls a stored tone setting via a MIDI Program Change. The default
PC→channel map is defined in Boss Tone Studio's address_map.js (prm_prop_midi):
``RxPcACh1-4`` = PC 0-3, ``RxPcPanel`` = PC 4, ``RxPcBCh1-4`` = PC 5-8.

The amp does NOT expose 40 contiguous slots. The 100 W class
(Katana-100 / 212 / Head / Artist Mk2) has 8 channel memories — banks A/B ×
CH1-4 — and the 50 W has 4 — banks A/B × CH1-2. Note the PCs are non-contiguous:
PC 4 is PANEL, so bank B always starts at PC 5 (both models), and the 50 W
simply omits CH-3/CH-4 in each bank.

Channel display names match Boss Tone Studio ("A: CH-1", "B: CH-2", …).

PANEL (PC 4) is the live front-panel knob position, not a storage slot, so it is
not offered as a patch target; the app's "TONE only" option already covers
"write the live buffer without switching channel".
"""

# Config value -> banks, each an ordered list of (display name, PC). Grouping by
# bank is the single source of truth: the flat channel list and the picker's
# per-bank rows are both derived from it.
CHANNELS_BY_MODEL: dict[str, list[list[tuple[str, int]]]] = {
    "100": [
        [("A: CH-1", 0), ("A: CH-2", 1), ("A: CH-3", 2), ("A: CH-4", 3)],
        [("B: CH-1", 5), ("B: CH-2", 6), ("B: CH-3", 7), ("B: CH-4", 8)],
    ],
    "50": [
        [("A: CH-1", 0), ("A: CH-2", 1)],
        [("B: CH-1", 5), ("B: CH-2", 6)],
    ],
}

DEFAULT_MODEL = "50"

# Human-readable labels for the model selector (settings pane dropdown).
MODEL_LABELS: dict[str, str] = {
    "50": "50 W — 4 channels",
    "100": "100 W / Artist — 8 channels",
}


def normalize_model(model: str) -> str:
    """Map a raw config value (e.g. '100', '100W', '50w') to a known model key.

    Unknown/blank values fall back to the 50 W map (the more common amp and this
    project's target); 100 W owners set ``[midi] amp_model = 100``.
    """
    m = (model or "").strip().lower().replace("w", "")
    return m if m in CHANNELS_BY_MODEL else DEFAULT_MODEL


def channel_rows_for_model(model: str) -> list[list[tuple[str, int]]]:
    """Return channels grouped into rows by bank (A row, then B row)."""
    return CHANNELS_BY_MODEL[normalize_model(model)]


def channels_for_model(model: str) -> list[tuple[str, int]]:
    """Return the flat ordered (name, PC) channel list for an amp model."""
    return [ch for row in channel_rows_for_model(model) for ch in row]


def _norm(name: str) -> str:
    """Normalise a channel name for tolerant matching (case/space-insensitive)."""
    return (name or "").upper().replace(" ", "")


def pc_for_name(name: str, model: str) -> int | None:
    """Resolve a channel name (e.g. 'A: CH-1', 'a:ch-1') to its PC, or None."""
    want = _norm(name)
    for n, pc in channels_for_model(model):
        if _norm(n) == want:
            return pc
    return None


def name_for_pc(pc: int, model: str) -> str:
    """Human name for a PC on the given model, or 'PC<n>' if it is not a channel."""
    for n, p in channels_for_model(model):
        if p == pc:
            return n
    return f"PC{pc}"
