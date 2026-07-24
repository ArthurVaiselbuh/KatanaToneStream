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
import contextlib
import logging
from pathlib import Path

import keyring as _keyring

from . import katana_channels, paths

log = logging.getLogger(__name__)

_KEYRING_SERVICE = "katana_tonestream"
_KEYRING_USER_KEY = "toneexchange_username"
_KEYRING_PASS_KEY = "toneexchange_password"
_KEYRING_LLM_PREFIX = "katana_tonestream_llm_api_key"  # per-provider: "<prefix>:<provider>"


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


def amp_model() -> str:
    """Configured Katana model key ('100' or '50'); defaults to 50 W.

    Determines how many channel memories exist and the name→PC map used to
    resolve ``[midi] target_patch`` (100 W: A1-A4/B1-B4, 50 W: A1-A2/B1-B2).
    """
    return katana_channels.normalize_model(get("midi", "amp_model", fallback=""))


def set_amp_model(model: str) -> None:
    """Persist the Katana model ('50' or '100') to config.ini [midi] amp_model."""
    _set_ini_value("midi", "amp_model", katana_channels.normalize_model(model))


def llm_timeout() -> float:
    """Per-call LLM request timeout in seconds; default 120.

    Bounds a hung provider (e.g. a local Ollama model that never finishes
    loading) so generation fails cleanly instead of blocking forever. Override
    with ``[llm] timeout_seconds`` in config.ini.
    """
    raw = get("llm", "timeout_seconds", fallback="120").strip()
    try:
        val = float(raw)
    except ValueError:
        return 120.0
    return val if val > 0 else 120.0


def dial_sidebar_width() -> int:
    """Persisted width (px) of the Create tab's amp-dial sidebar; default 280."""
    raw = get("ui", "dial_sidebar_width", fallback="280")
    try:
        return int(float(raw))
    except ValueError:
        return 280


def set_dial_sidebar_width(width: int) -> None:
    _set_ini_value("ui", "dial_sidebar_width", str(int(width)))


def log_panel_width() -> int:
    """Persisted width (px) of the log panel; default 360."""
    raw = get("ui", "log_panel_width", fallback="360")
    try:
        return int(float(raw))
    except ValueError:
        return 360


def set_log_panel_width(width: int) -> None:
    _set_ini_value("ui", "log_panel_width", str(int(width)))


def library_view_mode() -> str:
    """Persisted Library tab view style ('list' or 'grid'); default 'list'."""
    raw = get("ui", "library_view_mode", fallback="list").strip().lower()
    return raw if raw in ("list", "grid") else "list"


def set_library_view_mode(mode: str) -> None:
    _set_ini_value("ui", "library_view_mode", "grid" if mode == "grid" else "list")


def midi_target_patch() -> int:
    """Return the default target channel as a MIDI PC number, or -1 for TONE-only.

    Channel names and PCs depend on [midi] amp_model. Blank/unknown → -1
    (write the live TONE buffer only, no channel switch).
    """
    raw = get("midi", "target_patch", fallback="")
    pc = katana_channels.pc_for_name(raw, amp_model())
    return pc if pc is not None else -1


# ── ToneExchange credentials (keyring) ───────────────────────────────────────


def toneexchange_credentials() -> tuple[str, str]:
    """Return (username, password) from the OS keyring, or ('', '') if not set."""
    try:
        username = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER_KEY) or ""
        password = _keyring.get_password(_KEYRING_SERVICE, _KEYRING_PASS_KEY) or ""
        return username, password
    except Exception:
        log.warning("Failed to read credentials from keyring", exc_info=True)
        return "", ""


def set_toneexchange_credentials(username: str, password: str) -> bool:
    """Save credentials to the OS keyring. Returns True on success."""
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
    for key in (_KEYRING_USER_KEY, _KEYRING_PASS_KEY):
        with contextlib.suppress(Exception):
            _keyring.delete_password(_KEYRING_SERVICE, key)


# ── LLM API keys, one per provider (keyring) + default selection (config.ini) ─


def _llm_key_name(provider: str) -> str:
    return f"{_KEYRING_LLM_PREFIX}:{provider}"


def llm_api_key(provider: str) -> str:
    """Return the stored API key for a provider (e.g. 'openai'), or '' if not set."""
    if not provider:
        return ""
    try:
        return _keyring.get_password(_KEYRING_SERVICE, _llm_key_name(provider)) or ""
    except Exception:
        log.warning("Failed to read LLM API key for %s from keyring", provider, exc_info=True)
        return ""


def set_llm_api_key(provider: str, key: str) -> bool:
    """Save a provider's API key to the OS keyring. Returns True on success."""
    try:
        _keyring.set_password(_KEYRING_SERVICE, _llm_key_name(provider), key)
        return True
    except Exception:
        log.warning("Failed to save LLM API key for %s to keyring", provider, exc_info=True)
        return False


def delete_llm_api_key(provider: str) -> None:
    """Remove a provider's API key from the keyring."""
    with contextlib.suppress(Exception):
        _keyring.delete_password(_KEYRING_SERVICE, _llm_key_name(provider))


def _set_ini_value(section: str, key: str, value: str) -> None:
    """Write a single key into config.ini and reload."""
    ini_path = paths.app_dir() / "config.ini"
    paths.ensure_dirs()
    parser = configparser.RawConfigParser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    with open(ini_path, "w", encoding="utf-8") as f:
        parser.write(f)
    reload()


def default_llm_provider() -> str:
    """Return the last-used LLM provider key, defaulting to 'openai'."""
    return get("llm", "provider", fallback="openai")


def default_llm_model(provider: str) -> str:
    """Return the last-used model (provider/model form) for a provider, or ''."""
    if not provider:
        return ""
    return get("llm", f"model.{provider}", fallback="")


def set_default_llm(provider: str, model: str) -> None:
    """Remember the default provider and the last-used model for that provider."""
    _set_ini_value("llm", "provider", provider)
    _set_ini_value("llm", f"model.{provider}", model)
