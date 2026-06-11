# Boss Katana Mk2 type enumerations — sourced from assets/address_map.js analysis.
# Byte ranges (min/max) are authoritative from the JS; human names from Boss documentation.

# Patch_0[17]: PRM_PREAMP_A_TYPE — max=32 in JS; types 16-32 exist in firmware but names
# below are confirmed from Boss Katana Mk2 panel/documentation.
PREAMP_TYPES: dict[int, str] = {
    0: "Natural",
    1: "Boutique CLN",
    2: "Crunch",
    3: "HiGain",
    4: "Lead",
    5: "Brown",
    6: "JC Clean",
    7: "Mini Rect",
    8: "Cali Cln",
    9: "Cali Ld",
    10: "Brit Nm",
    11: "Brit Hi",
    12: "Orng Cln",
    13: "Orng Ld",
    14: "BlackPnl",
    15: "Tweed",
}

# Patch_0[1]: PRM_ODDS_TYPE — max=25 in JS; types 18-25 exist in firmware but
# names are unconfirmed from this JS version.
OD_TYPES: dict[int, str] = {
    0: "OD-1",
    1: "Blues OD",
    2: "Warm OD",
    3: "Natural OD",
    4: "Crunch OD",
    5: "Mid Boost",
    6: "Fat DS",
    7: "Hi-Gain DS",
    8: "Metal DS",
    9: "Stack DS",
    10: "Lead DS",
    11: "B-DRV",
    12: "TS-style",
    13: "SD-1",
    14: "Boss BD-2",
    15: "RAT",
    16: "Fuzz",
    17: "Custom",
}

# Fx(1/2)[1]: PRM_FX1_FXTYPE — max=40 in JS; types 0-30 named below in parameter
# group name order from prm_prop_patch_fx. Types 31-40 exist but are unnamed in
# this JS version.
FX_TYPES: dict[int, str] = {
    0: "T.WAH",
    1: "Auto Wah",
    2: "Sub Wah",
    3: "Compressor",
    4: "Limiter",
    5: "Graphic EQ",
    6: "Parametric EQ",
    7: "Guitar Sim",
    8: "Slow Gear",
    9: "Wave Synth",
    10: "Octave",
    11: "Pitch Shift",
    12: "Harmonist",
    13: "Acoustic Processor",
    14: "Phaser",
    15: "Flanger",
    16: "Tremolo",
    17: "Rotary",
    18: "Uni-V",
    19: "Slicer",
    20: "Vibrato",
    21: "Ring Mod",
    22: "Humanizer",
    23: "2x2 Chorus",
    24: "AC Sim",
    25: "EVH Phaser 90",
    26: "EVH Flanger 117E",
    27: "EVH Wah 95E",
    28: "DC-30",
    29: "Heavy Octave",
    30: "Pedal Bend",
}

# Delay(1/2)[1]: PRM_DLY_TYPE — max=10 in address_map.js (11 types).
DELAY_TYPES: dict[int, str] = {
    0: "Standard",
    1: "Mono",
    2: "Pan",
    3: "Analog",
    4: "Tape",
    5: "Mod",
    6: "T.Mod",
    7: "Reverse",
    8: "Roll",
    9: "Tera Echo",
    10: "SDE-3000",
}

# Patch_1[1]: PRM_REVERB_TYPE — max=6 in address_map.js (7 types).
REVERB_TYPES: dict[int, str] = {
    0: "Room",
    1: "Hall",
    2: "Plate",
    3: "Spring",
    4: "Mod",
    5: "Shimmer",
    6: "SFX",
}

# Front-panel amp characters, captured from real channel switches (each sends
# Patch_0[17] PREAMP_A_TYPE + Status[12] VARIATION). The 5 panel positions each
# have a green (variation 0) and red (variation 1) voicing → 10 entries.
# name -> (preamp_type, variation). These preamp_type indices are ground truth.
KATANA_CHANNELS: dict[str, tuple[int, int]] = {
    "Acoustic": (1, 0),
    "Clean": (8, 0),
    "Crunch": (11, 0),
    "Lead": (24, 0),
    "Brown": (23, 0),
    "Acoustic (var)": (28, 1),
    "Clean (var)": (29, 1),
    "Crunch (var)": (30, 1),
    "Lead (var)": (31, 1),
    "Brown (var)": (32, 1),
}

# Preamp type indices whose panel voicing is the red "variation". Derived from the
# captured channel set above.
_VARIATION_PREAMP_TYPES = {t for t, v in KATANA_CHANNELS.values() if v == 1}


def variation_for_preamp(preamp_type: int) -> int:
    """Return the green/red variation flag (0/1) for a preamp type.

    Captured panel "var" voicings (types 28-32) are red (1); all others are 0.
    """
    return 1 if preamp_type in _VARIATION_PREAMP_TYPES else 0
