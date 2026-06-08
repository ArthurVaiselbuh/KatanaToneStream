"""Decode a Windows MIDI Services UMP capture (.midi2) into Roland SysEx frames.

The `midi endpoint <id> monitor --capture-to-file` tool writes Universal MIDI
Packet (UMP) words, one or two 32-bit hex words per line (e.g. `0x30164100 0x00000033`),
with `#` annotation/comment lines in between.

A MIDI 1.0 device like the Katana sends its SysEx wrapped in UMP "Data Message 64"
(message type 3, "SysEx 7-bit") packets. Each packet carries up to 6 SysEx data
bytes plus a status nibble (0=complete, 1=start, 2=continue, 3=end). The F0/F7
framing bytes are implied by the start/end status, so they are NOT in the data.

This script reassembles those packets back into raw SysEx byte streams and decodes
the Roland frames:

    F0 41 <dev> 00 00 00 33 <cmd> <addr4> <data..> <chk> F7
      cmd 0x11 = RQ1 (request data / read)
      cmd 0x12 = DT1 (data set / write)

Usage:
    python decode_capture.py captures\tonestudio_send.midi2
    python decode_capture.py captures\tonestudio_send.midi2 --dt1-only
    python decode_capture.py captures\tonestudio_send.midi2 --grep 10000000
"""

import argparse
import re
import sys

# UMP packet size (in 32-bit words) by message type (top nibble of first byte).
_UMP_WORDS = {
    0x0: 1, 0x1: 1, 0x2: 1,   # utility / system / MIDI 1.0 channel voice
    0x3: 2, 0x4: 2,           # data (SysEx7) / MIDI 2.0 channel voice
    0x5: 4, 0xD: 4, 0xF: 4,   # data (SysEx8) / flex / stream
}

ROLAND = 0x41
KATANA_MODEL = [0x00, 0x00, 0x00, 0x33]
CMD_NAMES = {0x11: "RQ1 (read)", 0x12: "DT1 (write)"}

_HEX_WORD = re.compile(r"0x([0-9A-Fa-f]{8})")


def read_words(path: str) -> list[int]:
    """Return the flat list of 32-bit UMP words from a capture file."""
    words: list[int] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for m in _HEX_WORD.findall(line):
                words.append(int(m, 16))
    return words


def words_to_bytes(word: int) -> list[int]:
    """Big-endian 4 bytes of a 32-bit word."""
    return [(word >> 24) & 0xFF, (word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF]


def reassemble_sysex(words: list[int]) -> list[list[int]]:
    """Walk the UMP word stream and return a list of raw SysEx payloads.

    Each payload is the byte list BETWEEN F0 and F7 (framing not included).
    Non-SysEx packets (program change, etc.) are skipped here but reported by
    the caller via `iter_packets` if desired.
    """
    messages: list[list[int]] = []
    current: list[int] = []
    i = 0
    n = len(words)
    while i < n:
        w0 = words[i]
        b = words_to_bytes(w0)
        mt = b[0] >> 4
        size = _UMP_WORDS.get(mt, 1)
        chunk = words[i : i + size]
        i += size
        if mt != 0x3:  # only SysEx7 data messages carry Roland frames
            continue
        status = b[1] >> 4
        count = b[1] & 0x0F
        # data bytes are b[2], b[3], then the bytes of subsequent words in chunk
        data: list[int] = [b[2], b[3]]
        for w in chunk[1:]:
            data += words_to_bytes(w)
        data = data[:count]
        if status == 0x0:        # complete in one packet
            messages.append(data)
            current = []
        elif status == 0x1:      # start
            current = list(data)
        elif status == 0x2:      # continue
            current += data
        elif status == 0x3:      # end
            current += data
            messages.append(current)
            current = []
    return messages


def checksum(payload: list[int]) -> int:
    return (0x80 - sum(payload) % 0x80) & 0x7F


def ascii_render(data: list[int]) -> str:
    return "".join(chr(c) if 0x20 <= c <= 0x7E else "." for c in data)


def decode_roland(sysex: list[int]) -> dict | None:
    """Decode a Katana Roland frame. Returns None if not a recognized frame."""
    if len(sysex) < 9 or sysex[0] != ROLAND:
        return None
    dev = sysex[1]
    model = sysex[2:6]
    if model != KATANA_MODEL:
        return {"kind": "non-katana", "dev": dev, "model": model, "raw": sysex}
    cmd = sysex[6]
    body = sysex[7:-1]          # address + data
    chk = sysex[-1]
    addr = body[:4]
    data = body[4:]
    return {
        "kind": "katana",
        "dev": dev,
        "cmd": cmd,
        "addr": addr,
        "data": data,
        "chk": chk,
        "chk_ok": chk == checksum(body),
    }


def fmt(bs: list[int]) -> str:
    return " ".join(f"{b:02X}" for b in bs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("capture", help="path to .midi2 capture file")
    ap.add_argument("--dt1-only", action="store_true", help="show only DT1 (write) frames")
    ap.add_argument("--rq1-only", action="store_true", help="show only RQ1 (read) frames")
    ap.add_argument("--grep", help="only show frames whose address hex starts with this (e.g. 60 or 10000000)")
    args = ap.parse_args()

    words = read_words(args.capture)
    msgs = reassemble_sysex(words)
    print(f"# {len(words)} UMP words -> {len(msgs)} reassembled SysEx message(s)\n")

    grep = args.grep.upper().replace(" ", "") if args.grep else None
    shown = 0
    for idx, sysex in enumerate(msgs):
        d = decode_roland(sysex)
        if d is None:
            continue
        if d["kind"] != "katana":
            continue
        if args.dt1_only and d["cmd"] != 0x12:
            continue
        if args.rq1_only and d["cmd"] != 0x11:
            continue
        addr_hex = "".join(f"{b:02X}" for b in d["addr"])
        if grep and not addr_hex.startswith(grep):
            continue
        cmd_name = CMD_NAMES.get(d["cmd"], f"cmd 0x{d['cmd']:02X}")
        flag = "" if d["chk_ok"] else "  !! BAD CHECKSUM"
        print(f"[{idx:4}] {cmd_name:12} dev=0x{d['dev']:02X} addr={fmt(d['addr'])}  "
              f"len={len(d['data']):3}{flag}")
        if d["data"]:
            print(f"        data: {fmt(d['data'])}")
            print(f"        ascii: {ascii_render(d['data'])}")
        shown += 1

    print(f"\n# {shown} frame(s) shown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
