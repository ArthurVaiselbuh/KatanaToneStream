"""LogPanel — the slide-out log view bound to the in-memory FletLogHandler."""

import logging
from datetime import datetime

import flet as ft

from .. import config
from ..logging_setup import FletLogHandler
from . import theme

_MAX_ROWS = 300
_LEVEL_OPTS = [
    (logging.DEBUG, "DEBUG", "#6B7280"),
    (logging.INFO, "INFO", "#3B82F6"),
    (logging.WARNING, "WARN", "#F59E0B"),
    (logging.ERROR, "ERROR", "#EF4444"),
]


def _level_color(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "#EF4444"
    if levelno >= logging.WARNING:
        return "#F59E0B"
    if levelno >= logging.INFO:
        return "#3B82F6"
    return "#6B7280"


class LogPanel:
    def __init__(self, page: ft.Page, ui_log: FletLogHandler) -> None:
        self._page = page
        self._ui_log = ui_log
        self._level = logging.DEBUG

        self.toggle_button = ft.IconButton(
            ft.Icons.TERMINAL,
            tooltip="Toggle log",
            icon_color=theme.TEXT_DIM,
            on_click=lambda e: self.toggle(),
        )

        self._list = ft.ListView(
            auto_scroll=True,
            spacing=0,
            padding=ft.Padding.symmetric(horizontal=4, vertical=4),
        )
        self._level_row = ft.Row(spacing=4)
        self._rebuild_level_chips()

        self._panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.TERMINAL, size=14, color=theme.AMBER),
                            ft.Text("Log", size=12, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                            ft.Container(self._level_row, expand=True),
                            ft.IconButton(
                                ft.Icons.COPY_OUTLINED,
                                icon_size=16,
                                icon_color=theme.TEXT_DIM,
                                tooltip="Copy all to clipboard",
                                on_click=lambda e: self._copy(),
                            ),
                            ft.IconButton(
                                ft.Icons.DELETE_SWEEP_OUTLINED,
                                icon_size=16,
                                icon_color=theme.TEXT_DIM,
                                tooltip="Clear log",
                                on_click=lambda e: self._clear(),
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Container(self._list, expand=True),
                ],
                spacing=6,
                expand=True,
            ),
            width=config.log_panel_width(),
            bgcolor=theme.SURFACE_VAR,
            padding=ft.Padding.symmetric(horizontal=8, vertical=8),
        )
        self.control = ft.Row(
            [
                theme.resize_handle(
                    self._panel, min_width=260, on_change=config.set_log_panel_width
                ),
                self._panel,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=False,
        )

        ui_log.add_callback(self._on_new_record)

    # ── Rendering ───────────────────────────────────────────────────────────
    def _log_row(self, record: logging.LogRecord) -> ft.Container:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        col = _level_color(record.levelno)
        return ft.Container(
            ft.Row(
                [
                    ft.Text(
                        f"{ts} {record.levelname[0]}",
                        size=10,
                        color=col,
                        width=80,
                        font_family="Courier New",
                        selectable=True,
                    ),
                    ft.Text(
                        record.getMessage(),
                        size=10,
                        color="#C9D1D9",
                        expand=True,
                        no_wrap=False,
                        font_family="Courier New",
                        selectable=True,
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            padding=ft.Padding.symmetric(horizontal=4, vertical=1),
        )

    def _refresh(self) -> None:
        self._list.controls.clear()
        for r in self._ui_log.get_records(self._level)[-_MAX_ROWS:]:
            self._list.controls.append(self._log_row(r))
        self._page.update()

    def _rebuild_level_chips(self) -> None:
        self._level_row.controls.clear()
        for lvl, label, col in _LEVEL_OPTS:
            self._level_row.controls.append(
                theme.chip(
                    label,
                    active=self._level == lvl,
                    on_tap=lambda e, lv=lvl: self._set_level(lv),
                    color=col,
                    size=10,
                    padding_h=7,
                    padding_v=2,
                    radius=8,
                    fill=False,
                    animate=False,
                )
            )

    # ── Actions ─────────────────────────────────────────────────────────────
    def _set_level(self, lvl: int) -> None:
        self._level = lvl
        self._rebuild_level_chips()
        self._refresh()

    def _copy(self) -> None:
        lines = [
            f"{datetime.fromtimestamp(r.created).strftime('%H:%M:%S')} "
            f"[{r.levelname}] {r.name}: {r.getMessage()}"
            for r in self._ui_log.get_records(self._level)[-_MAX_ROWS:]
        ]
        self._page.set_clipboard("\n".join(lines))

    def _clear(self) -> None:
        self._ui_log.clear()
        self._list.controls.clear()
        self._page.update()

    def toggle(self) -> None:
        self.control.visible = not self.control.visible
        self.toggle_button.icon_color = theme.AMBER if self.control.visible else theme.TEXT_DIM
        if self.control.visible:
            self._refresh()
        self._page.update()

    def _on_new_record(self, record: logging.LogRecord) -> None:
        if not self.control.visible or record.levelno < self._level:
            return
        self._list.controls.append(self._log_row(record))
        if len(self._list.controls) > _MAX_ROWS:
            self._list.controls.pop(0)
        try:
            self._page.update()
        except Exception:
            logging.getLogger(__name__).debug("log panel update failed", exc_info=True)
