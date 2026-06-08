# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Flet desktop GUI (Windows-focused, Python 3.14) that searches Boss Katana Mk2
guitar tone patches from online sources, downloads/caches them, and pushes them
to a physically connected Katana amp over MIDI SysEx. Package lives in
`src/katana_tonestream/`; entry point is `main.py` → `app.run()`.

## Commands

This project uses **uv** (see `uv.lock`, `requires-python >=3.14`).

```bash
uv sync                      # install deps incl. dev group (pytest, ruff)
uv run python main.py        # launch the Flet GUI
uv run pytest                # run all tests
uv run pytest tests/test_midi.py::TestRolandChecksum::test_simple   # single test
uv run ruff check            # lint (rules: E, F, I; line-length 100)
uv run ruff format           # format
```

Tests configure `pythonpath=["src"]` via `pyproject.toml`, so imports use the
installed package name `katana_tonestream`. Some tests (`sample_tsl_path`,
`seed_test_patches.py`) read from a `prerequisites/` directory of real `.tsl`/
`.alb` files that is **not** in the repo — those tests `skip` when absent.

## Architecture

Strict layering, UI-free core so the flow is testable without Flet or a real amp:

- **`service.py` (`PatchService`)** — the orchestrator. `search()` merges/de-dupes
  results across sources (cached patches lead); `apply()` runs the
  download → cache → parse → send-to-amp pipeline. UI talks only to this. Tests
  inject fakes for `midi`/fetcher here (see `tests/test_service.py`).
- **`fetcher.py`** — web scrapers. Boss Tone Exchange via the Roland JSON API
  (`rcpsvc.roland.com/btc`; search is unauthenticated, download needs login →
  cached `idToken`); guitarpatches.com via HTML scraping. `download_patch()`
  dispatches on `meta.source`.
- **`parser.py`** — turns downloaded files into `KatanaPatch`. Two TSL shapes
  (local `patchList` vs. ToneExchange `data[][].paramSet`) plus `.alb` backups.
  ToneExchange/ALB patches carry `raw_bytes` (a dict of `UserPatch%*` hex arrays)
  — **only patches with `raw_bytes` can be sent to the amp**; param-dict-only
  TSLs cannot build SysEx.
- **`katana_midi.py` (`KatanaMidi`)** — Roland DT1 SysEx engine over
  python-rtmidi. Builds/sends per-section writes to the amp's live TONE buffer,
  optionally preceded by a Program Change to a slot. See the critical caveat below.
- **`cache.py` + `paths.py`** — local store. All state under `app_dir()`
  (`~/.katana_tonestream`, overridable via `KATANA_TONESTREAM_HOME` — this is how
  tests redirect to a tmp dir). `index.json` + `<id>.tsl` files + `art/`.
- **`config.py`** — `config.ini` (RawConfigParser) from app dir or CWD.
  Placeholder credential values are treated as absent. `_cfg` is module-level;
  call `config.reload()` after changing the app dir (the `app_home` fixture does).
- **`ui/`** — Flet components (`app_shell.py` wires everything; `patch_card`,
  `search_bar`, `slot_picker`, `log_panel`, `theme`). Components stay dumb;
  `AppShell` owns orchestration and background threads (MIDI monitor polls every
  5s, search/apply/art run via `page.run_thread`).
- **`logging_setup.py`** — explicit `setup_logging()` (called once in `app.run()`),
  returns a `FletLogHandler` the UI binds to. Note: flet's own loggers are muted
  to avoid a `page.update()` log-feedback cascade.

## Katana MIDI addresses — now capture-verified

`katana_midi.py`'s `_TONE_SECTIONS` and `DEVICE_ID = 0x00` are decoded from a
real Boss Tone Studio capture (`captures/tonestudio_send.midi2`) and are byte-for-byte
confirmed: `tools/verify_send_against_capture.py` reconstructs each `UserPatch%*`
section from the capture, rebuilds the DT1 frames through the live code path, and
asserts they equal what Tone Studio sent (33/33 frames match). Run it after any
change to the section map, chunking, or frame format.

Key facts the capture established (history: earlier *computed* offsets like Fx(1)
at `0x58` were wrong, overlapped amp routing tables, and made the physical volume
knobs stop responding):
- Section bases are **fixed and 0x100-page-aligned** (Fx(1) at `60 00 01 00`),
  not accumulated from section sizes.
- Device ID is `0x00`, not the `0x7F` broadcast.
- Tone Studio caps DT1 payloads at **128 bytes** (`MAX_CHUNK`), splitting larger
  sections (Fx = 128 + 97) at the page boundary; `_roland_addr_add(base, 128)`
  reproduces the continuation address.
- All sections are now sent — there is no longer an "unverified, skip" set.

`send_patch` writes to the **live TONE buffer** (`0x60000000`) only. Storing a
patch permanently into a slot (the `0x10010000` mirror plus the `0x7F 01 02 0x`
commit commands seen later in the capture) is not yet implemented.

### MIDI capture toolchain (`tools/`)

The machine has Windows MIDI Services (multi-client endpoints), so MIDI traffic
can be sniffed *while Tone Studio runs* — no USBPcap/Wireshark needed.

- **`record_midi.ps1`** — run in an **interactive** terminal, drive Tone Studio,
  press **Esc** to stop (Esc is what flushes the capture file; a force-kill leaves
  a 0-byte file). The monitor crashes if stdin is redirected, so it cannot run
  headless/background. Writes timestamped `.midi2` UMP files to `captures/`.
- **`decode_capture.py`** — reassembles UMP SysEx7 packets into Roland frames:
  `uv run python tools/decode_capture.py captures/<file>.midi2 --dt1-only`
  (also `--rq1-only`, `--grep <addr-prefix>`).
- **`seed_test_patches.py`** — seeds known test patches into the cache from a
  local `.alb` for MIDI debugging.

## Roland DT1 frame reference

`F0 41 <devId> 00 00 00 33 12 <addr4> <data...> <checksum> F7`, where
`checksum = (0x80 - sum(addr+data) % 0x80) & 0x7F`. Addresses are 7-bit per byte
(base-128 carry, see `_roland_addr_add`). `cmd 0x12` = DT1 write, `0x11` = RQ1
read. The frame format and checksum are confirmed; only the *addresses* are suspect.
