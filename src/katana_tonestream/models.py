from dataclasses import dataclass
from datetime import datetime


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
    bass: int = 64
    mid: int = 64
    treble: int = 64
    presence: int = 50
    od_type: int = 0
    od_on: bool = False
    od_drive: int = 50
    od_level: int = 40
    variation: int = 0  # amp green/red variation (Status[12]); see katana_catalog.KATANA_CHANNELS
    fx1_on: bool = False
    fx1_type: int = 0
    fx2_on: bool = False
    fx2_type: int = 0
    reverb_on: bool = False
    reverb_type: int = 0
    reverb_level: int = 35
    delay_on: bool = False
    delay_type: int = 0
    delay_level: int = 50
    raw_bytes: dict | None = None

    @property
    def display_name(self) -> str:
        return self.patch_name or self.meta.name or "Unnamed Patch"
