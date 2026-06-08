"""SearchBar — search field plus source-filter chips."""

from collections.abc import Callable

import flet as ft

from . import theme

_FILTERS = [
    ("all", "All"),
    ("toneexchange", "ToneExchange"),
    ("guitarpatches", "GuitarPatches"),
    ("cached", "Cached"),
]


class SearchBar:
    def __init__(self, page: ft.Page, on_search: Callable[[str, str], None]) -> None:
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

    def _rebuild_chips(self) -> None:
        self._filter_row.controls.clear()
        for value, label in _FILTERS:
            self._filter_row.controls.append(
                theme.chip(
                    label,
                    active=self.filter == value,
                    on_tap=lambda e, v=value: self._set_filter(v),
                )
            )

    def _set_filter(self, value: str) -> None:
        self.filter = value
        self._rebuild_chips()
        self._page.update()
        if self.query or value == "cached":
            self._trigger()
