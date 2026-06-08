from dataclasses import dataclass
from datetime import datetime

# Amp type index → human label (Boss Katana Mk2)
PREAMP_TYPES = {
    0: "Natural", 1: "Boutique CLN", 2: "Crunch", 3: "HiGain",
    4: "Lead", 5: "Brown", 6: "JC Clean", 7: "Mini Rect",
    8: "Cali Cln", 9: "Cali Ld", 10: "Brit Nm", 11: "Brit Hi",
    12: "Orng Cln", 13: "Orng Ld", 14: "BlackPnl", 15: "Tweed",
}

# OD/DS type index → label
OD_TYPES = {
    0: "OD-1", 1: "Blues OD", 2: "Warm OD", 3: "Natural OD",
    4: "Crunch OD", 5: "Mid Boost", 6: "Fat DS", 7: "Hi-Gain DS",
    8: "Metal DS", 9: "Stack DS", 10: "Lead DS", 11: "B-DRV",
    12: "TS-style", 13: "SD-1", 14: "Boss BD-2", 15: "RAT",
    16: "Fuzz", 17: "Custom",
}


@dataclass
class PatchMeta:
    id: str
    name: str
    author: str
    source: str  # "toneexchange" | "guitarpatches" | "local"
    rating: float
    download_url: str
    cached: bool = False
    last_used: datetime | None = None
    image_url: str = ""


@dataclass
class KatanaPatch:
    meta: PatchMeta
    raw_params: dict
    patch_name: str
    preamp_type: int = 0
    preamp_gain: int = 0
    od_type: int = 0
    od_on: bool = False
    reverb_on: bool = False
    reverb_type: int = 0
    delay_on: bool = False
    raw_bytes: dict | None = None  # populated for ALB-sourced patches

    @property
    def preamp_label(self) -> str:
        return PREAMP_TYPES.get(self.preamp_type, f"Type {self.preamp_type}")

    @property
    def od_label(self) -> str:
        return OD_TYPES.get(self.od_type, f"OD {self.od_type}")

    @property
    def display_name(self) -> str:
        return self.patch_name or self.meta.name or "Unnamed Patch"
