"""Application entry point — configures logging then launches the Flet app."""

import flet as ft

from .logging_setup import setup_logging
from .service import PatchService
from .ui.app_shell import build_app


def run() -> None:
    ui_log = setup_logging()

    def target(page: ft.Page) -> None:
        build_app(page, PatchService(), ui_log)

    ft.run(target)
