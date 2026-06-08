"""Single source of truth for application directories.

All app state lives under the app dir, which defaults to ~/.katana_tonestream but
can be overridden with the KATANA_TONESTREAM_HOME environment variable. The
override makes cache/config/logging testable against a temporary directory.

Paths are resolved lazily (per call) so a test can point them at a fresh tmp dir
with monkeypatch.setenv without needing to reload modules.
"""

import os
from pathlib import Path


def app_dir() -> Path:
    override = os.environ.get("KATANA_TONESTREAM_HOME")
    return Path(override) if override else Path.home() / ".katana_tonestream"


def cache_dir() -> Path:
    return app_dir() / "cache"


def art_dir() -> Path:
    return app_dir() / "art"


def log_dir() -> Path:
    return app_dir()


def index_file() -> Path:
    return app_dir() / "index.json"


def log_file() -> Path:
    return app_dir() / "app.log"


def ensure_dirs() -> None:
    """Create the cache and art directories (and the app dir) if missing."""
    cache_dir().mkdir(parents=True, exist_ok=True)
    art_dir().mkdir(parents=True, exist_ok=True)
