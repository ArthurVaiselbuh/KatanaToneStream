"""Palette constants and small styled-control factories shared by the UI."""

import flet as ft

# ── Palette ─────────────────────────────────────────────────────────────────
AMBER       = "#F59E0B"
AMBER_DARK  = "#B45309"
CARD_BG     = "#1E1E2E"
SURFACE_VAR = "#181825"
BORDER_DIM  = "#2A2A3C"
TEXT_DIM    = "#6B7280"
PAGE_BG     = "#13131F"
ART_SIZE    = 64

_WHITE = "#FFFFFF"


def chip(
    label: str,
    *,
    active: bool,
    on_tap,
    color: str = AMBER,
    size: int = 12,
    padding_h: int = 13,
    padding_v: int = 6,
    radius: int = 16,
    fill: bool = True,
    animate: bool = True,
) -> ft.GestureDetector:
    """A tappable pill. ``fill`` toggles the filled (filter) vs outline (log-level) style."""
    if fill:
        text_color = _WHITE if active else TEXT_DIM
        bgcolor = AMBER_DARK if active else CARD_BG
    else:
        text_color = color if active else TEXT_DIM
        bgcolor = None
    container = ft.Container(
        ft.Text(label, size=size, weight=ft.FontWeight.W_500, color=text_color),
        padding=ft.Padding.symmetric(horizontal=padding_h, vertical=padding_v),
        border_radius=radius,
        bgcolor=bgcolor,
        border=ft.Border.all(1, color if active else BORDER_DIM),
    )
    if animate:
        container.animate = ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT)
    return ft.GestureDetector(container, on_tap=on_tap)


def amber_button(
    text: str,
    on_click,
    *,
    icon=None,
    radius: int = 20,
    padding_h: int = 20,
    padding_v: int = 14,
) -> ft.ElevatedButton:
    """The amber-on-hover elevated button used for Search / Apply actions."""
    return ft.ElevatedButton(
        text,
        icon=icon,
        on_click=on_click,
        style=ft.ButtonStyle(
            bgcolor={ft.ControlState.DEFAULT: AMBER_DARK, ft.ControlState.HOVERED: AMBER},
            color={ft.ControlState.DEFAULT: _WHITE},
            shape=ft.RoundedRectangleBorder(radius=radius),
            padding=ft.Padding.symmetric(horizontal=padding_h, vertical=padding_v),
        ),
    )


_SOURCE_STYLE: dict[str, tuple[str, str]] = {
    "toneexchange":  ("#1D4ED8", "#93C5FD"),
    "guitarpatches": ("#6D28D9", "#C4B5FD"),
    "local":         ("#0F766E", "#5EEAD4"),
}
_SOURCE_LABEL = {"toneexchange": "ToneExchange", "guitarpatches": "GuitarPatches", "local": "Local"}


def source_badge(source: str) -> ft.Container:
    """A small colored badge naming a patch's source."""
    bg, fg = _SOURCE_STYLE.get(source, ("#374151", "#D1D5DB"))
    label = _SOURCE_LABEL.get(source, source)
    return ft.Container(
        ft.Text(label, size=10, color=fg, weight=ft.FontWeight.W_500),
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
        border_radius=8,
        bgcolor=bg,
    )
