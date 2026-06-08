"""
Seed two test patches into the local cache for MIDI debugging.

TST_CLEAN   — JC Clean (type 6), gain=0, all FX off, delay off, reverb off
TST_OD      — Lead (type 4), gain=100 (max), FX on
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from katana_tonestream.cache import load_index, save_patch
from katana_tonestream.models import PatchMeta

ALB_PATH = Path("prerequisites/BossKatanaII/KATANA MkII_backup_170523.alb")


def _id(name: str) -> str:
    return "tst_" + hashlib.md5(name.encode()).hexdigest()[:12]


def _name_bytes(name: str) -> list[str]:
    """16-byte ASCII patch name, space-padded, as uppercase hex strings."""
    padded = name[:16].ljust(16)
    return [f"{ord(c):02X}" for c in padded]


def _make(template: dict, name: str, preamp_type: int, gain: int,
          fx1_on: bool, delay_on: bool, reverb_on: bool) -> dict:
    p = copy.deepcopy(template)

    p["UserPatch%PatchName"] = _name_bytes(name)

    # Patch_0 bytes: [0]=flags [1]=preamp_type [2]=gain [3]=bass [4]=mid [5]=treble
    if "UserPatch%Patch_0" in p:
        b = list(p["UserPatch%Patch_0"])
        b[0] = "00"
        b[1] = f"{preamp_type:02X}"
        b[2] = f"{gain:02X}"
        # For clean: lower treble (3C=60); for OD: boost treble (50=80)
        if not fx1_on:
            b[5] = "3C"
        else:
            b[3] = "50"   # bass
            b[5] = "50"   # treble
        p["UserPatch%Patch_0"] = b

    # FX1 on/off (byte 0)
    if "UserPatch%Fx(1)" in p:
        fx = list(p["UserPatch%Fx(1)"])
        fx[0] = "01" if fx1_on else "00"
        p["UserPatch%Fx(1)"] = fx

    # FX2 off for clean
    if "UserPatch%Fx(2)" in p and not fx1_on:
        fx2 = list(p["UserPatch%Fx(2)"])
        fx2[0] = "00"
        p["UserPatch%Fx(2)"] = fx2

    # Delay on/off (byte 0)
    for key in ("UserPatch%Delay(1)", "UserPatch%Delay(2)"):
        if key in p:
            d = list(p[key])
            d[0] = "01" if delay_on else "00"
            p[key] = d

    # Status byte 1 encodes reverb on/off (best guess; 0x01=on 0x00=off)
    if "UserPatch%Status" in p:
        st = list(p["UserPatch%Status"])
        if len(st) > 1:
            st[1] = "01" if reverb_on else "00"
        p["UserPatch%Status"] = st

    return p


def save(patch_dict: dict, display_name: str, desc: str) -> None:
    pid = _id(display_name)
    meta = PatchMeta(
        id=pid,
        name=display_name,
        author="test",
        source="local",
        rating=0.0,
        download_url="",
    )
    alb_json = json.dumps({
        "formatRev": "0002",
        "device": "KATANA MkII",
        "model": "KATANA-100MkII",
        "userPatch": [patch_dict],
    }).encode()
    save_patch(meta, alb_json)
    print(f"  Saved  {display_name!r}  id={pid}  ({desc})")


def main() -> None:
    with open(ALB_PATH, encoding="utf-8") as f:
        alb = json.load(f)
    template = alb["userPatch"][0]

    clean = _make(template,
                  name="TST_CLEAN",
                  preamp_type=6,   # JC Clean
                  gain=0,
                  fx1_on=False,
                  delay_on=False,
                  reverb_on=False)

    od = _make(template,
               name="TST_OD",
               preamp_type=4,   # Lead
               gain=0x64,       # 100 = max
               fx1_on=True,
               delay_on=False,
               reverb_on=False)

    print("Seeding test patches …")
    save(clean, "TST_CLEAN", "JC Clean, gain=0, all FX off")
    save(od,    "TST_OD",    "Lead, gain=100 (max), FX on")
    print("Done. Restart the app and click 'Cached' to see them.")


if __name__ == "__main__":
    main()
