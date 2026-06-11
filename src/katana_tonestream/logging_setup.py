"""Logging configuration — UI handler + rotating file handler.

Lives outside the GUI module so logging is configured explicitly via
``setup_logging()`` (called once from ``app.run()``) rather than as an
import-time side effect. ``setup_logging`` returns the in-memory
``FletLogHandler`` so the UI can bind a live callback to it.
"""

import collections
import logging
from logging.handlers import RotatingFileHandler

from . import paths

# Third-party loggers that are too chatty for DEBUG. The flet_* loggers are the
# important ones: routing their records into the UI handler (which calls
# page.update()) creates a feedback cascade that freezes the log panel.
_NOISY_LOGGERS = (
    "urllib3",
    "requests",
    "flet",
    "flet_core",
    "flet_controls",
    "flet_transport",
    "asyncio",
    "websockets",
)


class FletLogHandler(logging.Handler):
    """In-memory log handler — notifies registered Flet callbacks on each record."""

    MAX = 600
    # Records from these logger trees never reach the UI: emitting them would
    # trigger page.update(), which itself logs via flet_*, cascading into a freeze.
    _SKIP_PREFIXES = ("flet", "asyncio", "websockets", "concurrent")

    def __init__(self) -> None:
        super().__init__()
        self._records: collections.deque[logging.LogRecord] = collections.deque(maxlen=self.MAX)
        self._callbacks: list = []

    def emit(self, record: logging.LogRecord) -> None:
        if any(record.name.startswith(p) for p in self._SKIP_PREFIXES):
            return
        self.format(record)
        self._records.append(record)
        for cb in list(self._callbacks):
            try:
                cb(record)
            except Exception:
                log = logging.getLogger(__name__)
                log.debug("log callback failed", exc_info=True)

    def get_records(self, min_level: int = logging.DEBUG) -> list[logging.LogRecord]:
        return [r for r in self._records if r.levelno >= min_level]

    def add_callback(self, cb) -> None:
        self._callbacks.append(cb)

    def clear(self) -> None:
        self._records.clear()


def setup_logging() -> FletLogHandler:
    """Install the UI + rotating-file handlers and return the UI handler.

    Idempotent for the file/UI handlers in the sense that calling it again adds
    fresh handlers; intended to be called exactly once from ``app.run()``.
    """
    root = logging.getLogger()

    ui_handler = FletLogHandler()
    ui_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(ui_handler)

    # Rotating log file — 2 MB cap, 3 backups → max 8 MB on disk.
    paths.ensure_dirs()
    file_handler = RotatingFileHandler(
        paths.log_file(), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(file_handler)

    root.setLevel(logging.DEBUG)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    return ui_handler


# litellm installs its own stderr StreamHandler and prints a provider banner. We
# strip the handler (records already propagate to root → file + UI handlers) and
# silence the banner so all LLM logging lands in the app's log window, not the console.
_LITELLM_LOGGERS = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")
_litellm_tamed = False


def tame_litellm_logging() -> None:
    """Redirect litellm's console output into the app log handlers. Idempotent."""
    global _litellm_tamed
    if _litellm_tamed:
        return
    try:
        import litellm

        litellm.suppress_debug_info = True
    except Exception:
        return
    for name in _LITELLM_LOGGERS:
        lg = logging.getLogger(name)
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
        lg.propagate = True
        lg.setLevel(logging.WARNING)
    _litellm_tamed = True
