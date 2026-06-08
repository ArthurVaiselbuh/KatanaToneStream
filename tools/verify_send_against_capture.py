"""Verify katana_midi builds byte-identical DT1 frames to a real Tone Studio capture.

Reconstructs each UserPatch%* section's data from the live-buffer (0x60..) frames
in captures/tonestudio_send.midi2, feeds it back through the actual
katana_midi._build_dt1 + _roland_addr_add + MAX_CHUNK chunking, and asserts the
produced SysEx bytes match the captured bytes exactly.

    uv run python tools/verify_send_against_capture.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from decode_capture import decode_roland, read_words, reassemble_sysex  # noqa: E402

from katana_tonestream.katana_midi import (  # noqa: E402
    _TONE_SECTIONS,
    DEVICE_ID,
    MAX_CHUNK,
    _build_dt1,
    _roland_addr_add,
)

CAPTURE = Path(__file__).resolve().parent.parent / "captures" / "tonestudio_send.midi2"


def captured_tone_frames() -> list[dict]:
    """All DT1 writes to the live TONE buffer (addr starts 0x60 0x00), in order."""
    msgs = reassemble_sysex(read_words(str(CAPTURE)))
    frames = []
    for sysex in msgs:
        d = decode_roland(sysex)
        if d and d.get("kind") == "katana" and d["cmd"] == 0x12 and d["addr"][:2] == [0x60, 0x00]:
            frames.append(d)
    return frames


def main() -> int:
    frames = captured_tone_frames()
    # Group captured frames by section: a section base + every page-aligned
    # continuation (addr >= base, < next section base). Rebuild its data array.
    bases = {tuple(s.addr): s.key for s in _TONE_SECTIONS}

    # Reconstruct each section's full data by concatenating its captured frame(s).
    section_data: dict[str, list[int]] = {}
    cur_key = None
    for d in frames:
        addr = tuple(d["addr"])
        if addr in bases:
            cur_key = bases[addr]
            section_data[cur_key] = list(d["data"])
        elif cur_key is not None:
            # page continuation of the current section (e.g. Fx split)
            section_data[cur_key] += list(d["data"])

    raw_bytes = {k: [f"{b:02X}" for b in v] for k, v in section_data.items()}

    # Now rebuild frames through the real code path and compare to the capture.
    rebuilt: list[list[int]] = []
    for section in _TONE_SECTIONS:
        hex_list = raw_bytes.get(section.key)
        if not hex_list:
            continue
        data = [int(h, 16) for h in hex_list]
        for start in range(0, len(data), MAX_CHUNK):
            chunk = data[start : start + MAX_CHUNK]
            rebuilt.append(_build_dt1(_roland_addr_add(section.addr, start), chunk))

    captured = [[0xF0, 0x41, d["dev"], 0x00, 0x00, 0x00, 0x33, d["cmd"], *d["addr"],
                 *d["data"], d["chk"], 0xF7] for d in frames]

    print(f"DEVICE_ID=0x{DEVICE_ID:02X}  MAX_CHUNK={MAX_CHUNK}")
    print(f"captured live-buffer frames: {len(captured)}   rebuilt frames: {len(rebuilt)}")

    ok = True
    if len(captured) != len(rebuilt):
        print("!! frame COUNT mismatch")
        ok = False
    for i, (c, r) in enumerate(zip(captured, rebuilt)):
        if c != r:
            ok = False
            print(f"!! frame {i} differs")
            print(f"   captured: {' '.join(f'{b:02X}' for b in c)}")
            print(f"   rebuilt : {' '.join(f'{b:02X}' for b in r)}")

    print("\nALL FRAMES MATCH" if ok else "\nMISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
