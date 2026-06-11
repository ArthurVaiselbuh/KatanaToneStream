"""Record MIDI traffic from the Katana endpoint via Windows MIDI Services.

Finds the Katana UMP endpoint, then runs `midi endpoint <id> monitor` with
stdin/stdout inherited from the terminal so the user can press ESC to stop.

Usage:
    uv run python tools/record_midi.py
    uv run python tools/record_midi.py --label od_type
    uv run python tools/record_midi.py --output captures/my_session.midi2
    uv run python tools/record_midi.py --port "DAW CTRL"

Decode the result:
    uv run python tools/decode_capture.py captures/<file>.midi2 --dt1-only --grep 60

Notes:
    - Must run in a real interactive terminal — midi.exe reads ESC from stdin.
    - The Katana endpoint is multi-client: this monitor can run at the same time
      as Tone Studio and will see Tone Studio's outbound DT1 writes.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CAPTURES_DIR = Path(__file__).parent.parent / "captures"
MIDI_EXE = "midi.exe"


def find_endpoint(port_keyword: str) -> str:
    """Return the full UMP endpoint ID for the first endpoint matching port_keyword.

    midi.exe renders a Unicode box-drawing table and hard-wraps the endpoint ID
    at the column boundary (31 chars), splitting it across multiple rows like:

        │ \\?\\swd#midisrv#midiu_ksa_626 │ ...
        │ 7693523090701851#{e7cce071-3c  │ ...
        │ 03-423f-88d3-f1045d02552b}     │ ...

    Strategy: strip all non-ASCII (removes box-drawing chars and emoji), split
    on whitespace to get clean tokens, then find the path start token and greedily
    reassemble continuation tokens (hex/symbol chars only) until the closing '}'.
    """
    result = subprocess.run(
        [MIDI_EXE, "enumerate", "midi-services-endpoints", "--show-endpoint-id"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"midi.exe enumerate failed (exit {result.returncode}):\n{result.stderr}"
        )

    # Strip ANSI color/style escapes, then non-ASCII (box-drawing, emoji), then tokenise.
    clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", result.stdout)
    tokens = re.sub(r"[^\x00-\x7F]+", " ", clean).split()

    path_prefix = r"\\?\swd#midisrv#"
    continuation = re.compile(r"^[0-9a-fA-F#{}\-_]+$")

    for i, token in enumerate(tokens):
        if not token.lower().startswith(path_prefix.lower()):
            continue
        parts = [token]
        for j in range(i + 1, min(i + 8, len(tokens))):
            nxt = tokens[j]
            if continuation.match(nxt):
                parts.append(nxt)
                if nxt.endswith("}"):
                    break
            else:
                break
        candidate = "".join(parts)
        # The keyword may appear in the endpoint ID itself (e.g. --port ksa)
        # OR in the display-name tokens that precede the path in the table row.
        context = " ".join(tokens[max(0, i - 15):i])
        if port_keyword.lower() in candidate.lower() or port_keyword.lower() in context.lower():
            return candidate

    detected = [t for t in tokens if t.lower().startswith(path_prefix.lower())]
    hint = f"\nDetected path starts: {detected}" if detected else "\nNo swd#midisrv paths found."
    raise RuntimeError(
        f"No endpoint matching '{port_keyword}' found.{hint}\n"
        "Is the amp connected and powered on?"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="capture",
                    help="Prefix for the auto-generated filename (default: capture)")
    ap.add_argument("--output", "-o", default=None,
                    help="Explicit output path (overrides --label + timestamp)")
    ap.add_argument("--port", default="KATANA",
                    help="Keyword to match in the endpoint ID (default: KATANA)")
    args = ap.parse_args()

    print("Searching for MIDI endpoint…")
    try:
        endpoint_id = find_endpoint(args.port)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Endpoint : {endpoint_id}")

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = CAPTURES_DIR / f"{args.label}_{stamp}.midi2"

    print(f"Capturing: {out}")
    print("Now drive Tone Studio. Press ESC in this window to stop & flush.\n")

    # Run with inherited stdin/stdout so the user can interact (ESC to stop).
    subprocess.run(
        [
            MIDI_EXE, "endpoint", endpoint_id, "monitor",
            "--capture-to-file", str(out),
            "--annotate-capture",
            "--include-timestamp",
            "--verbose",
        ]
    )

    print(f"\nSaved: {out}")
    print(f'Decode with:\n  uv run python tools/decode_capture.py "{out}" --dt1-only --grep 60')


if __name__ == "__main__":
    main()
