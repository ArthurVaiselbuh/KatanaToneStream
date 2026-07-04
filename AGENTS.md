# AGENTS.md

This file provides guidance to AI coding agents working in this repository.

## Maintaining this file

Update AGENTS.md only when the architecture changes or something here is no longer
correct. Routine work — bug fixes, refactors, dependency bumps, one-off tasks — does
not need an update. Do not record thought process, implementation detail, or change
history here; the code and git log are the source of truth for those.

## Code style

No docstrings on short functions; no comments unless the code is genuinely obscure.
Well-named functions and variables are the documentation.

## What this is

A Flet desktop GUI (Windows-focused, Python) that searches Boss Katana Mk2
guitar tone patches online, downloads/caches them, and pushes them to a physically
connected Katana amp over MIDI SysEx. Package lives in `src/katana_tonestream/`;
entry point is `main.py` → `app.run()`.

## Commands

This project uses **uv**

```bash
uv sync                      # install deps incl. dev group (pytest, ruff)
uv run python main.py        # launch the Flet GUI
uv run pytest                # run all tests
uv run ruff check            # lint
uv run ruff format           # format
uv run pre-commit run --all-files   # ruff lint + format
```

pre-commit is not installed as a git hook — run it manually.

Tests set `pythonpath=["src"]`. Some tests read a `prerequisites/` dir of real
`.tsl`/`.alb` files that is not in the repo, and `skip` when absent.

## Assets

Runtime assets (`base_template.json`, `logo.ico`) live inside the package at
`src/katana_tonestream/assets/` so they ship in the wheel; load them relative to
`__file__`, never from repo-root `assets/` (non-shipped source art and the
`address_map.js` reversing source — absent in an installed wheel).

## Architecture

Strict layering, UI-free core so the flow is testable without Flet or a real amp:

- **`service.py` (`PatchService`)** — orchestrator. `search()` merges/de-dupes across
  sources (cached patches lead); `apply()` runs download → cache → parse → send-to-amp.
  UI talks only to this; tests inject fakes for `midi`/fetcher.
- **`fetcher.py`** — web scrapers. Boss Tone Exchange via the Roland JSON API (search
  unauthenticated, download needs a cached login `idToken`) and guitarpatches.com via
  HTML scraping. `download_patch()` dispatches on `meta.source`.
- **`parser.py`** — downloaded files → `KatanaPatch`. Two TSL shapes (local `patchList`
  vs. ToneExchange `data[][].paramSet`) plus `.alb` backups. Only patches carrying
  `raw_bytes` (`UserPatch%*` hex arrays) can be sent to the amp; param-dict-only TSLs
  cannot.
- **`katana_midi.py` (`KatanaMidi`)** — Roland DT1 SysEx engine over python-rtmidi.
  Sends per-section writes to the amp's live TONE buffer, optionally preceded by a
  Program Change. See MIDI notes below.
- **`cache.py` + `paths.py`** — local store under `app_dir()` (`~/.katana_tonestream`,
  overridable via `KATANA_TONESTREAM_HOME`, which tests use to redirect to a tmp dir).
- **`config.py`** — `config.ini` for non-secrets; credentials and LLM API keys live in
  the OS keyring (LLM keys one per provider). Last-used LLM provider/model persisted in
  `config.ini`.
- **`llm_providers.py`** — provider catalog plus `list_models()`, which queries each
  provider's real `/models` endpoint via litellm (non-blocking, `[]` on failure).
- **`katana_catalog.py`** — pure data: type enumerations and the capture-derived
  `KATANA_CHANNELS`. Byte mappings/type ranges are hardcoded here and in
  `parser.py`/`patch_builder.py`;
- **`katana_channels.py`** — pure data: the amp's real MIDI PC→channel recall map per
  model, grouped by bank (100 W: A/B × CH-1..4, 50 W: A/B × CH-1..2), with Tone
  Studio channel names. See MIDI notes below.
- **`llm_generator.py`** — `generate_patch(...)`, a pure (no Flet) three-phase LLM
  conversation (character → type selection → parameter values) returning a merged dict.
- **`patch_builder.py`** — `build_raw_bytes()` deep-copies a base template and overwrites
  only known byte positions (named constants in the module); `get_template()` returns the
  bundled clean Tone Studio base, falling back to a cached patch if missing.
- **`ui/`** — Flet components; `app_shell.py` wires them and owns background threads (MIDI
  monitor poll, search/apply/art via `page.run_thread`). Flet 0.85.2 API patterns are in
  `.claude/commands/flet.md` (run `/flet`).
- **`logging_setup.py`** — `setup_logging()` (called once in `app.run()`) returns the
  `FletLogHandler` the UI binds to; flet's own loggers are muted to avoid a
  `page.update()` feedback cascade.

## Katana MIDI

`katana_midi.py`'s `_TONE_SECTIONS` and `DEVICE_ID = 0x00` are decoded from a real Tone
Studio capture and verified by `tools/verify_send_against_capture.py` (33/33 frames
match) — run it after any change to the section map, chunking, or frame format. Section
bases are fixed and 0x100-page-aligned (Fx(1) at `60 00 01 00`); DT1 payloads cap at 128
bytes (`MAX_CHUNK`), splitting larger sections at the page boundary.

`send_patch` writes to the **live TONE buffer** (`0x60000000`) only.

**Channel recall (`katana_channels.py`).** The amp recalls a stored channel via MIDI
Program Change; the 100 W class exposes 8 channels — `A: CH-1`…`A: CH-4` = PC 0-3,
`B: CH-1`…`B: CH-4` = PC 5-8 — and the 50 W exposes 4 (`A: CH-1/2`, `B: CH-1/2`). PC 4
is PANEL (skipped), so bank B always starts at PC 5. Names match Boss Tone Studio.
`[midi] amp_model` (`100`|`50`, default `50`) selects the map; `[midi] target_patch`
(e.g. `A: CH-1`) picks the default channel,
blank/unknown → TONE-only.

### Capture toolchain (`tools/`)

Windows MIDI Services allows sniffing traffic while Tone Studio runs.

- **`record_midi.py`** — run interactively, drive Tone Studio, press **Esc** to stop
  (Esc flushes the file; a force-kill leaves it empty). Cannot run headless. Writes
  `.midi2` UMP files to `captures/`.
- **`decode_capture.py`** — reassembles UMP SysEx7 into Roland frames (`--dt1-only`,
  `--rq1-only`, `--grep <addr-prefix>`).
- **`seed_test_patches.py`** — seeds known test patches into the cache from a local `.alb`.

## Roland DT1 frame

`F0 41 <devId> 00 00 00 33 12 <addr4> <data...> <checksum> F7`, where
`checksum = (0x80 - sum(addr+data) % 0x80) & 0x7F`. Addresses are 7-bit per byte
(base-128 carry, see `_roland_addr_add`). `cmd 0x12` = DT1 write, `0x11` = RQ1 read.
