"""Application configuration — reads config.ini using RawConfigParser.

Search order (first found wins):
  1. ~/.katana_tonestream/config.ini
  2. config.ini next to main.py (project root / working directory)
"""

import configparser
import logging
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)


def _search_paths() -> list[Path]:
    return [
        paths.app_dir() / "config.ini",
        Path("config.ini"),
    ]


def _load() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    for path in _search_paths():
        if path.exists():
            parser.read(path, encoding="utf-8")
            log.debug("Loaded config from %s", path)
            return parser
    log.debug("No config.ini found; using empty config")
    return parser


_cfg = _load()


def reload() -> None:
    """Re-read config from disk. Mainly for tests after changing the app dir."""
    global _cfg
    _cfg = _load()


def get(section: str, key: str, fallback: str = "") -> str:
    return _cfg.get(section, key, fallback=fallback)


def midi_target_patch() -> int:
    """Return the default target patch slot as a PC number (0=A1 … 39=E8), or -1 for TONE-only."""
    raw = get("midi", "target_patch", fallback="").strip().upper()
    if len(raw) == 2 and raw[0] in "ABCDE" and raw[1] in "12345678":
        return (ord(raw[0]) - ord("A")) * 8 + (int(raw[1]) - 1)
    return -1  # no config → default to TONE-only (no PC)


def toneexchange_credentials() -> tuple[str, str]:
    """Return (username, password) from [toneexchange] section, or ('', '')."""
    username = get("toneexchange", "username")
    password = get("toneexchange", "password")
    # Treat the placeholder values as absent
    if username == "your_email@example.com" or password == "your_password_here":
        return "", ""
    return username, password
