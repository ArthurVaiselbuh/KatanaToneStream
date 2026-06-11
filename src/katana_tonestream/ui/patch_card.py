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
        self,
        meta: PatchMeta,
        on_apply: Callable[[PatchMeta], None],
        on_remove: Callable[[PatchMeta], None],
        page: ft.Page,
        midi_connected: Callable[[], bool],
    ) -> None:
        self.meta = meta
        self._page = page
        self._midi_connected = midi_connected
        self._busy = False

        self._apply_btn = theme.amber_button(
            "Apply",
            lambda e: on_apply(meta),
            icon=ft.Icons.SEND_ROUNDED,
            radius=8,
            padding_h=14,
            padding_v=9,
        )
        self._spinner_wrap = ft.Container(
            content=ft.ProgressRing(width=22, height=22, stroke_width=2, color=theme.AMBER),
            alignment=ft.Alignment(0, 0),
            expand=True,
            opacity=0,
        )

        cached = is_cached(meta.id)
        self._remove_btn = ft.IconButton(
            ft.Icons.DELETE_OUTLINE,
            icon_size=15,
            icon_color=theme.TEXT_DIM,
            tooltip="Remove from cache",
            visible=cached,
            on_click=lambda e: on_remove(meta),
            style=ft.ButtonStyle(
                padding=ft.Padding.all(4),
                overlay_color={ft.ControlState.HOVERED: "#3A2020"},
            ),
        )
        self._cached_pill = ft.Container(
            ft.Row(
                [
                    ft.Icon(ft.Icons.STAR_ROUNDED, size=11, color=theme.AMBER),
                    ft.Text("cached", size=10, color=theme.AMBER),
                ],
                spacing=3,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
            border_radius=8,
            border=ft.Border.all(1, theme.AMBER),
            visible=cached,
        )

        art_path = get_art_path(meta.id)
        self._art_slot = ft.Container(
            content=_art_image(art_path) if art_path else _art_placeholder(),
            width=theme.ART_SIZE,
            height=theme.ART_SIZE,
            border_radius=8,
            bgcolor="#2A2A3C",
            alignment=ft.Alignment.CENTER,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        info_col = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            meta.name or "Unnamed Patch",
                            weight=ft.FontWeight.W_600,
                            size=14,
                            expand=True,
                        ),
                        self._cached_pill,
                        self._remove_btn,
                    ],
                    spacing=4,
                ),
                ft.Text(meta.author or "Unknown author", size=11, color=theme.TEXT_DIM),
                ft.Row([theme.source_badge(meta.source)], spacing=8),
            ],
            expand=True,
            spacing=4,
        )
        action_box = ft.Stack([self._apply_btn, self._spinner_wrap])

        self.control = ft.Container(
            ft.Row(
                [self._art_slot, info_col, action_box],
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
        self._busy = busy
        self._apply_btn.opacity = 0.0 if busy else (1.0 if self._midi_connected() else 0.4)
        self._apply_btn.disabled = busy or not self._midi_connected()
        self._spinner_wrap.opacity = 1.0 if busy else 0.0
        self._page.update()

    def mark_cached(self) -> None:
        self._cached_pill.visible = True
        self._remove_btn.visible = True
        self._page.update()

    def set_art(self, path: Path) -> None:
        self._art_slot.content = _art_image(path)
        self._page.update()

    def refresh_apply_state(self) -> None:
        """Dim/enable the apply button based on MIDI connection (skipped while busy)."""
        if self._busy:
            return
        connected = self._midi_connected()
        self._apply_btn.disabled = not connected
        self._apply_btn.opacity = 1.0 if connected else 0.4
        self._page.update()
