"""SearchBar — search field plus source-filter chips."""

from collections.abc import Callable

import flet as ft

from ..config import toneexchange_credentials
from . import theme

_FILTERS = [
    ("all", "All"),
    ("toneexchange", "ToneExchange"),
    ("guitarpatches", "GuitarPatches"),
    ("cached", "Cached"),
]


class SearchBar:
    def __init__(
        self,
        page: ft.Page,
        on_search: Callable[[str, str], None],
    ) -> None:
        self._page = page
        self._on_search = on_search
        self.filter = "all"

        self._field = ft.TextField(
            hint_text="Search for a song or patch name…",
            prefix_icon=ft.Icons.SEARCH,
            border_radius=22,
            focused_border_color=theme.AMBER,
            expand=True,
            on_submit=lambda e: self._trigger(),
            text_size=14,
            bgcolor=theme.CARD_BG,
            border_color=theme.BORDER_DIM,
        )
        search_btn = theme.amber_button("Search", lambda e: self._trigger())

        self._filter_row = ft.Row(spacing=6, wrap=False)
        self._rebuild_chips()

        self.control = ft.Container(
            ft.Column(
                [ft.Row([self._field, search_btn], spacing=10), self._filter_row],
                spacing=10,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            bgcolor=theme.SURFACE_VAR,
        )

    @property
    def query(self) -> str:
        return self._field.value.strip()

    def _trigger(self) -> None:
        self._on_search(self.query, self.filter)

    def _has_te_credentials(self) -> bool:
        username, password = toneexchange_credentials()
        return bool(username and password)

    def _rebuild_chips(self) -> None:
        self._filter_row.controls.clear()
        for value, label in _FILTERS:
            # Show a warning badge on ToneExchange when credentials are missing
            if value == "toneexchange" and not self._has_te_credentials():
                display = ft.Row(
                    [
                        ft.Text(
                            label,
                            size=12,
                            weight=ft.FontWeight.W_500,
                            color="#FFFFFF" if self.filter == value else theme.TEXT_DIM,
                        ),
                        ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=13, color="#F59E0B"),
                    ],
                    spacing=4,
                    tight=True,
                )
                container = ft.Container(
                    display,
                    padding=ft.Padding.symmetric(horizontal=13, vertical=6),
                    border_radius=16,
                    bgcolor=theme.AMBER_DARK if self.filter == value else theme.CARD_BG,
                    border=ft.Border.all(
                        1, theme.AMBER if self.filter == value else theme.BORDER_DIM
                    ),
                    tooltip="ToneExchange credentials not set — configure in Settings",
                    animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT),
                )
                self._filter_row.controls.append(
                    ft.GestureDetector(container, on_tap=lambda e, v=value: self._set_filter(v))
                )
            else:
                self._filter_row.controls.append(
                    theme.chip(
                        label,
                        active=self.filter == value,
                        on_tap=lambda e, v=value: self._set_filter(v),
                    )
                )

    def refresh_chips(self) -> None:
        """Rebuild and repaint chips (call after credentials change)."""
        self._rebuild_chips()
        self._page.update()

    def _set_filter(self, value: str) -> None:
        self.filter = value
        self._rebuild_chips()
        self._page.update()
        if self.query or value == "cached":
            self._trigger()
