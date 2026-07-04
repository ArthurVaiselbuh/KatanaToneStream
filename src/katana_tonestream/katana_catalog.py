# Boss Katana Mk2 type enumerations — sourced from assets/address_map.js analysis.
# Byte ranges (min/max) are authoritative from the JS; human names from Boss documentation.

# Patch_0[17]: PRM_PREAMP_A_TYPE — max=32. 0-25 is the GT-100 preamp list;
# 26-27 are unnamed/unknown; 28-32 are the Katana panel red-variation voicings
# (capture-confirmed, see KATANA_CHANNELS).
PREAMP_TYPES: dict[int, str] = {
    0: "Natural Clean",
    1: "Full Range",
    2: "Combo Crunch",
    3: "Stack Crunch",
    4: "HiGain Stack",
    5: "Power Drive",
    6: "Extreme Lead",
    7: "Core Metal",
    8: "JC-120",
    9: "Clean Twin",
    10: "Pro Crunch",
    11: "Tweed",
    12: "Deluxe Crunch",
    13: "VO Drive",
    14: "VO Lead",
    15: "Match Drive",
    16: "BG Lead",
    17: "BG Drive",
    18: "MS1959 I",
    19: "MS1959 I+II",
    20: "R-Fier Vintage",
    21: "R-Fier Modern",
    22: "T-Amp Lead",
    23: "Brown",
    24: "Lead Stack",
    25: "Custom",
    28: "Acoustic (panel variation)",
    29: "Clean (panel variation)",
    30: "Crunch (panel variation)",
    31: "Lead (panel variation)",
    32: "Brown (panel variation)",
}

# Patch_0[1]: PRM_ODDS_TYPE — max=25. 0-21 is the GT-100 OD/DS list (anchored
# by the panel Booster defaults 10/11/14 = Blues Drive/Overdrive/Dist);
# 22-25 exist in firmware but are unnamed in this address_map.js version.
OD_TYPES: dict[int, str] = {
    0: "Mid Boost",
    1: "Clean Boost",
    2: "Treble Boost",
    3: "Crunch",
    4: "Natural OD",
    5: "Warm OD",
    6: "Fat DS",
    7: "Lead DS",
    8: "Metal DS",
    9: "Oct Fuzz",
    10: "Blues Drive",
    11: "Overdrive",
    12: "T-Scream",
    13: "Turbo OD",
    14: "Dist",
    15: "Rat",
    16: "Guv DS",
    17: "DST+",
    18: "Metal Zone",
    19: "'60s Fuzz",
    20: "Muff Fuzz",
    21: "Custom",
}

# Fx(1/2)[1]: PRM_FX1_FXTYPE — max=40. The enum is the GT-100 FX list; Tone
# Studio's prm_prop_patch_fx defines param groups for exactly the 31 values
# below. The gaps (5, 8, 11, 13, 17, 24, 30, 32-34: Distortion, Tone Modify,
# Defretter, Sitar Sim, Sound Hold, Pan, Sub Delay, ...) are GT-lineage types
# with no Tone Studio editor support, so they are not offered here.
FX_TYPES: dict[int, str] = {
    0: "T.Wah",
    1: "Auto Wah",
    2: "Sub Wah",
    3: "Compressor",
    4: "Limiter",
    6: "Graphic EQ",
    7: "Parametric EQ",
    9: "Guitar Sim",
    10: "Slow Gear",
    12: "Wave Synth",
    14: "Octave",
    15: "Pitch Shifter",
    16: "Harmonist",
    18: "AC. Processor",
    19: "Phaser",
    20: "Flanger",
    21: "Tremolo",
    22: "Rotary",
    23: "Uni-V",
    25: "Slicer",
    26: "Vibrato",
    27: "Ring Mod",
    28: "Humanizer",
    29: "2x2 Chorus",
    31: "AC Guitar Sim",
    35: "Phaser 90E",
    36: "Flanger 117E",
    37: "Wah 95E",
    38: "DC-30",
    39: "Heavy Octave",
    40: "Pedal Bend",
}

# Delay(1/2)[1]: PRM_DLY_TYPE — max=10 (11 types). GT-100 delay list, anchored
# by the panel Delay defaults 0/7/8 = Digital/Analog/Tape Echo.
DELAY_TYPES: dict[int, str] = {
    0: "Digital",
    1: "Pan",
    2: "Stereo",
    3: "Dual Series",
    4: "Dual Parallel",
    5: "Dual L/R",
    6: "Reverse",
    7: "Analog",
    8: "Tape Echo",
    9: "Modulate",
    10: "SDE-3000",
}

# Patch_1[1]: PRM_REVERB_TYPE — max=6 (7 types). GT-100 reverb list, anchored
# by the panel Reverb defaults 4/5/3 = Plate/Spring/Hall.
REVERB_TYPES: dict[int, str] = {
    0: "Ambience",
    1: "Room",
    2: "Hall 1",
    3: "Hall 2",
    4: "Plate",
    5: "Spring",
    6: "Modulate",
}

# Front-panel amp characters, captured from real channel switches (each sends
# Patch_0[17] PREAMP_A_TYPE + Status[12] VARIATION). The 5 panel positions each
# have a green (variation 0) and red (variation 1) voicing → 10 entries.
# name -> (preamp_type, variation). These preamp_type indices are ground truth
# and line up with the GT-100 names in PREAMP_TYPES (Clean=8=JC-120,
# Brown=23=Brown, Lead=24=Lead Stack, ...).
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
