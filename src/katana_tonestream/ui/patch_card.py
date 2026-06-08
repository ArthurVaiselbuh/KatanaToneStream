"""PatchCard — one result row (art, name/author/source, apply button)."""

from collections.abc import Callable
from pathlib import Path

import flet as ft

from ..cache import get_art_path, is_cached
from ..models import PatchMeta
from . import theme


def _art_image(path: Path) -> ft.Image:
    return ft.Image(src=str(path), width=theme.ART_SIZE, height=theme.ART_SIZE, fit="cover")


def _art_placeholder() -> ft.Control:
    return ft.Icon(ft.Icons.MUSIC_NOTE_OUTLINED, size=28, color=theme.TEXT_DIM)


class PatchCard:
    """Builds and owns the controls for a single patch result."""

    def __init__(
        self, meta: PatchMeta, on_apply: Callable[[PatchMeta], None], page: ft.Page
    ) -> None:
        self.meta = meta
        self._page = page

        self._apply_btn = theme.amber_button(
            "Apply",
            lambda e: on_apply(meta),
            icon=ft.Icons.SEND_ROUNDED,
            radius=8,
            padding_h=14,
            padding_v=9,
        )
        self._spinner = ft.ProgressRing(
            width=22, height=22, stroke_width=2, visible=False, color=theme.AMBER
        )
        self._cached_pill = ft.Container(
            ft.Row(
                [ft.Icon(ft.Icons.STAR_ROUNDED, size=11, color=theme.AMBER),
                 ft.Text("cached", size=10, color=theme.AMBER)],
                spacing=3, tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            border_radius=8,
            border=ft.Border.all(1, theme.AMBER),
            visible=is_cached(meta.id),
        )

        art_path = get_art_path(meta.id)
        self._art_slot = ft.Container(
            content=_art_image(art_path) if art_path else _art_placeholder(),
            width=theme.ART_SIZE, height=theme.ART_SIZE,
            border_radius=8,
            bgcolor="#2A2A3C",
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        info_col = ft.Column(
            [
                ft.Row(
                    [ft.Text(meta.name or "Unnamed Patch",
                             weight=ft.FontWeight.W_600, size=14, expand=True),
                     self._cached_pill],
                    spacing=6,
                ),
                ft.Text(meta.author or "Unknown author", size=11, color=theme.TEXT_DIM),
                ft.Row([theme.source_badge(meta.source)], spacing=8),
            ],
            expand=True, spacing=4,
        )
        action_col = ft.Column(
            [self._spinner, self._apply_btn],
            horizontal_alignment=ft.CrossAxisAlignment.END,
            spacing=0,
        )

        self.control = ft.Container(
            ft.Row(
                [self._art_slot, info_col, action_col],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=12,
            border_radius=10,
            bgcolor=theme.CARD_BG,
            border=ft.Border.all(1, theme.BORDER_DIM),
        )

    # ── Live updates (call from the UI thread) ──────────────────────────────
    def set_busy(self, busy: bool) -> None:
        self._apply_btn.visible = not busy
        self._spinner.visible = busy
        self._page.update()

    def mark_cached(self) -> None:
        self._cached_pill.visible = True
        self._page.update()

    def set_art(self, path: Path) -> None:
        self._art_slot.content = _art_image(path)
        self._page.update()
