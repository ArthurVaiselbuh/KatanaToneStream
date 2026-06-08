"""Application configuration — keyring for credentials, config.ini for everything else.

Credentials (ToneExchange username/password) are stored in the OS keyring
(Windows Credential Manager). ``set_toneexchange_credentials`` / ``delete_toneexchange_credentials``
are the write path; the UI settings pane calls them. config.ini is NOT used for
credentials — only for non-secret settings like [midi] target_patch.

config.ini search order (first found wins):
  1. ~/.katana_tonestream/config.ini
  2. config.ini next to main.py (project root / working directory)
"""

import configparser
import logging
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "katana_tonestream"
_KEYRING_USER_KEY = "toneexchange_username"
_KEYRING_PASS_KEY = "toneexchange_password"

try:
    import keyring as _keyring
    _KEYRING_OK = True
except Exception:
    _KEYRING_OK = False
    log.warning("keyring not available — credentials cannot be stored securely")


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


# ── ToneExchange credentials (keyring) ───────────────────────────────────────

def toneexchange_credentials() -> tuple[str, str]:
    """Return (username, password) from the OS keyring, or ('', '') if not set."""
    if not _KEYRING_OK:
        return "", ""
    try:
        username = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER_KEY) or ""
        password = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_PASS_KEY) or ""
        return username, password
    except Exception:
        log.warning("Failed to read credentials from keyring", exc_info=True)
        return "", ""


def set_toneexchange_credentials(username: str, password: str) -> bool:
    """Save credentials to the OS keyring. Returns True on success."""
    if not _KEYRING_OK:
        return False
    try:
        _keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER_KEY, username)
        _keyring.set_password(_KEYRING_SERVICE, _KEYRING_PASS_KEY, password)
        log.info("ToneExchange credentials saved to keyring")
        return True
    except Exception:
        log.warning("Failed to save credentials to keyring", exc_info=True)
        return False


def delete_toneexchange_credentials() -> None:
    """Remove ToneExchange credentials from the keyring."""
    if not _KEYRING_OK:
        return
    for key in (_KEYRING_USER_KEY, _KEYRING_PASS_KEY):
        try:
            _keyring.delete_password(_KEYRING_SERVICE, key)
        except Exception:
            pass
