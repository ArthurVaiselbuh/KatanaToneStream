# KatanaToneStream

> ⚠️ **Unofficial software.** KatanaToneStream is an independent, community project. It is **not
> affiliated with, endorsed by, sponsored by, or supported by Roland Corporation or BOSS** in any
> way. "BOSS", "Katana", "Roland", "Boss Tone Studio", and "Boss Tone Exchange" are trademarks of
> their respective owners and are used here only to describe compatibility. Use this software at your
> own risk.

A Windows desktop app that finds guitar tone patches for the **BOSS Katana Mk2**, caches them
locally, and pushes them to a physically connected amp over MIDI — plus an LLM-powered generator that
creates a patch from just an artist and song name.

---

## Features

- **Search & import patches** from Boss Tone Exchange (Roland's public API) and guitarpatches.com.
- **Send to amp over USB MIDI** — writes directly to the Katana's live TONE buffer via Roland DT1
  SysEx (reverse-engineered from real Boss Tone Studio captures for interoperability).
- **LLM tone generation** — give it an artist + song and it dials in a plausible patch (preamp, OD,
  FX, delay, reverb, EQ). Works with multiple providers (OpenAI, Anthropic, Gemini, xAI, Mistral,
  Groq, DeepSeek) **or a fully local model via [Ollama](https://ollama.com)** — no cloud required.
- **Local cache** of patches and artwork, so a tone you found once is one click away.

## Requirements

- Windows
- Python **3.14+**
- [uv](https://docs.astral.sh/uv/) for dependency management
- A BOSS Katana Mk2 connected by USB (only needed to actually send patches to hardware)

## Install & run

```bash
uv sync                 # install dependencies
uv run python main.py   # launch the app
```

## Configuration

- **Boss Tone Exchange** (optional, only needed to download patches that require login): copy
  `config.example.ini` to `config.ini` and fill in your credentials, or enter them in the app's
  Settings pane.
- **LLM API keys** (optional, for tone generation): set them in the Settings pane. Keys are stored in
  the OS keyring (Windows Credential Manager), one per provider — never in plain text in the repo.
- **Local generation with Ollama**: install the Ollama app, `ollama pull qwen2.5:14b` (a 16 GB GPU
  comfortably runs a 14B model), then pick **Ollama (local)** in the Generate dialog. No API key
  needed.

## Development

```bash
uv run pytest                       # run the test suite
uv run ruff check                   # lint
uv run ruff format                  # format
uv run pre-commit run --all-files   # lint + format via .pre-commit-config.yaml
```

See **[AGENTS.md](AGENTS.md)** for architecture, the MIDI protocol details, and the capture toolchain.

## A note on the patch sources

This app fetches patches at runtime from third-party services. It does **not** bundle or redistribute
any patches. You are responsible for complying with the terms of service of any source you query
(Boss Tone Exchange, guitarpatches.com, etc.).

## License

[MIT](LICENSE) © 2026 Arthur Vaiselbuh
