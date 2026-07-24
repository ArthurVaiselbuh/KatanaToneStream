"""AppShell — assembles the UI components and owns cross-cutting orchestration.

Holds the result-card registry, the status/MIDI indicators, and the background
threads (MIDI monitor, search, apply, art loading). Components are kept dumb;
this is where they are wired to the PatchService.
"""

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import flet as ft

from ..art_resolver import resolve_art
from ..cache import delete_patch, get_art_path, get_cached_patches, save_art
from ..config import amp_model, library_view_mode, midi_target_patch, set_library_view_mode
from ..fetcher import fetch_art
from ..logging_setup import FletLogHandler
from ..models import PatchMeta
from ..service import PatchService
from . import theme
from .generate_panel import GeneratePanel
from .log_panel import LogPanel
from .patch_card import PatchCard
from .search_bar import SearchBar
from .settings_pane import SettingsPane
from .slot_picker import SlotPicker

log = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_ICON = _ASSETS / "logo.ico"
_MIDI_OK = "#22C55E"
_MIDI_BAD = "#EF4444"
_CREATE_TAB = 0
_LIBRARY_TAB = 1


class AppShell:
    def __init__(self, page: ft.Page, service: PatchService, ui_log: FletLogHandler) -> None:
        self.page = page
        self.service = service
        self.midi = service.midi
        self._applying: set[str] = set()
        self._cards: dict[str, PatchCard] = {}
        self._last_results: list[PatchMeta] = []
        self._view_mode = library_view_mode()

        cfg_patch = midi_target_patch()
        self.slot_picker = SlotPicker(
            page, initial=cfg_patch if cfg_patch >= 0 else None, model=amp_model()
        )
        self.log_panel = LogPanel(page, ui_log)
        self.settings_pane = SettingsPane(
            page,
            on_credentials_changed=self._on_credentials_changed,
            on_amp_model_changed=self.slot_picker.set_model,
        )
        self.generate_panel = GeneratePanel(page, self.midi, on_save=self._on_generate_saved)
        self.search_bar = SearchBar(page, self._on_search, trailing=self._build_view_toggle())

        self._build_page()
        self._start_threads()

    # ── View mode toggle ────────────────────────────────────────────────────
    def _build_view_toggle(self) -> ft.Control:
        self._view_list_btn = ft.IconButton(
            ft.Icons.VIEW_LIST_ROUNDED,
            icon_size=18,
            tooltip="List view",
            icon_color=theme.AMBER if self._view_mode == "list" else theme.TEXT_DIM,
            on_click=lambda e: self._set_view_mode("list"),
        )
        self._view_grid_btn = ft.IconButton(
            ft.Icons.GRID_VIEW_ROUNDED,
            icon_size=18,
            tooltip="Grid view",
            icon_color=theme.AMBER if self._view_mode == "grid" else theme.TEXT_DIM,
            on_click=lambda e: self._set_view_mode("grid"),
        )
        return ft.Row([self._view_list_btn, self._view_grid_btn], spacing=2, tight=True)

    # ── Create/Library tab switcher (lives in the AppBar title row) ────────
    def _build_tab_switcher(self) -> ft.Control:
        # Create lands first/selected — generation is the app's primary flow,
        # browsing existing patches is the secondary one.
        self._active_tab = _CREATE_TAB
        self._tab_switcher_row = ft.Row(spacing=6, tight=True)
        self._rebuild_tab_switcher()
        return self._tab_switcher_row

    def _rebuild_tab_switcher(self) -> None:
        self._tab_switcher_row.controls = [
            self._tab_button("Create", ft.Icons.AUTO_AWESOME, _CREATE_TAB),
            self._tab_button("Library", ft.Icons.LIBRARY_MUSIC_OUTLINED, _LIBRARY_TAB),
        ]

    def _tab_button(self, label: str, icon: str, index: int) -> ft.Container:
        """A bigger, TabBar-style button — bold label, amber icon + underline when active."""
        active = self._active_tab == index
        return ft.Container(
            ft.Row(
                [
                    ft.Icon(icon, size=20, color=theme.AMBER if active else theme.TEXT_DIM),
                    ft.Text(
                        label,
                        size=15,
                        weight=ft.FontWeight.BOLD,
                        color="#FFFFFF" if active else theme.TEXT_DIM,
                    ),
                ],
                spacing=8,
                tight=True,
            ),
            padding=ft.Padding.only(left=4, right=4, top=10, bottom=7),
            border=ft.Border.only(
                bottom=ft.BorderSide(3, theme.AMBER if active else "transparent")
            ),
            on_click=lambda e: self._select_tab(index),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT),
        )

    def _select_tab(self, index: int) -> None:
        if index == self._active_tab:
            return
        self._active_tab = index
        self._rebuild_tab_switcher()
        self._create_view.visible = index == _CREATE_TAB
        self._library_view.visible = index == _LIBRARY_TAB
        self.page.update()

    # ── Status + MIDI indicator ─────────────────────────────────────────────
    def _set_status(self, msg: str) -> None:
        if not self.midi.is_connected():
            return
        self._status_text.value = f"{msg}  ·  {datetime.now().strftime('%H:%M:%S')}"
        self.page.update()

    def _refresh_midi_chip(self) -> None:
        ok = self.midi.is_connected()
        c = _MIDI_OK if ok else _MIDI_BAD
        self._midi_dot.color = c
        self._midi_label.color = c
        self._midi_label.value = f"Katana  ·  {self.midi.port_name}" if ok else "Disconnected"
        self._midi_chip.border = ft.Border.all(1, c)
        self._status_bar.bgcolor = theme.SURFACE_VAR if ok else "#1A1010"
        self._status_text.color = theme.TEXT_DIM if ok else _MIDI_BAD
        if not ok:
            self._status_text.value = "No amp connected"
        # Snapshot before iterating — _render_results() can clear/rebuild self._cards
        # from a search thread while this (the MIDI monitor thread) is mid-loop.
        for card in list(self._cards.values()):
            card.refresh_apply_state()
        self.generate_panel.refresh_apply_state()
        self.page.update()

    # ── Generate ────────────────────────────────────────────────────────────
    def _on_edit_patch(self, meta: PatchMeta) -> None:
        self.generate_panel.edit(meta)
        self._select_tab(_CREATE_TAB)

    # ── Credentials changed ─────────────────────────────────────────────────
    def _on_credentials_changed(self) -> None:
        self.search_bar.refresh_chips()

    # ── Generate saved ──────────────────────────────────────────────────────
    def _on_generate_saved(self) -> None:
        self.page.run_thread(self._do_search, "", "cached")

    # ── Cards ───────────────────────────────────────────────────────────────
    def _make_card(self, meta: PatchMeta) -> ft.Control:
        card = PatchCard(
            meta,
            self._apply,
            self._remove,
            self.page,
            self.midi.is_connected,
            on_edit=self._on_edit_patch,
            layout=self._view_mode,
            on_select=self._on_card_selected,
        )
        self._cards[meta.id] = card
        card.refresh_apply_state()
        return card.control

    def _on_card_selected(self, selected: PatchCard) -> None:
        """Enforce single selection — deselect every other card in the results."""
        for card in self._cards.values():
            if card is not selected:
                card.deselect()

    def _active_results_container(self) -> ft.ListView | ft.GridView:
        return self._results_grid if self._view_mode == "grid" else self._results_list

    def _render_results(self, results: list[PatchMeta]) -> None:
        self._last_results = results
        self._cards.clear()
        self._results_list.controls.clear()
        self._results_grid.controls.clear()
        container = self._active_results_container()
        for meta in results:
            container.controls.append(self._make_card(meta))
        has = len(results) > 0
        self._empty_state.visible = not has
        self._results_wrapper.visible = has and self._view_mode == "list"
        self._results_grid_wrapper.visible = has and self._view_mode == "grid"

    # ── View mode ───────────────────────────────────────────────────────────
    def _set_view_mode(self, mode: str) -> None:
        if mode == self._view_mode:
            return
        self._view_mode = mode
        self._view_list_btn.icon_color = theme.AMBER if mode == "list" else theme.TEXT_DIM
        self._view_grid_btn.icon_color = theme.AMBER if mode == "grid" else theme.TEXT_DIM
        self._render_results(self._last_results)
        self.page.update()
        set_library_view_mode(mode)

    # ── Search ──────────────────────────────────────────────────────────────
    def _on_search(self, query: str, source_filter: str) -> None:
        self.page.run_thread(self._do_search, query, source_filter)

    def _do_search(self, query: str, source_filter: str) -> None:
        try:
            self._status_text.value = f"Searching…  ·  {datetime.now().strftime('%H:%M:%S')}"
            self.page.update()
            results = self.service.search(query, source_filter)
            log.debug("Search returned %d results, rendering…", len(results))
            self._render_results(results)
            log.debug("Render complete")
            noun = "patch" if len(results) == 1 else "patches"
            msg = f"Found {len(results)} {noun}" + (f" for '{query}'" if query else "")
            if self.midi.is_connected():
                self._status_text.value = f"{msg}  ·  {datetime.now().strftime('%H:%M:%S')}"
            self.page.update()
            self.page.run_thread(self._load_arts, results)
        except Exception as ex:
            log.exception(f"Search failed unexpectedly, {ex}")

    # ── Apply ───────────────────────────────────────────────────────────────
    def _apply(self, meta: PatchMeta) -> None:
        if meta.id in self._applying:
            return
        self._applying.add(meta.id)
        self.page.run_thread(self._apply_worker, meta)

    def _apply_worker(self, meta: PatchMeta) -> None:
        card = self._cards.get(meta.id)
        if card:
            card.set_busy(True)
        try:
            self.service.apply(
                meta,
                target_patch=self.slot_picker.target,
                on_status=self._set_status,
                on_cached=(card.mark_cached if card else None),
            )
        except Exception as exc:
            log.exception("Failed to apply %s", meta.id)
            self._set_status(f"Error: {exc}")
        finally:
            self._applying.discard(meta.id)
            if card:
                card.set_busy(False)

    # ── Remove ──────────────────────────────────────────────────────────────
    def _remove(self, meta: PatchMeta) -> None:
        self.page.run_thread(self._remove_worker, meta)

    def _remove_worker(self, meta: PatchMeta) -> None:
        delete_patch(meta.id)
        card = self._cards.pop(meta.id, None)
        container = self._active_results_container()
        if card and card.control in container.controls:
            container.controls.remove(card.control)
        self._last_results = [m for m in self._last_results if m.id != meta.id]
        has = len(container.controls) > 0
        self._empty_state.visible = not has
        self._results_wrapper.visible = has and self._view_mode == "list"
        self._results_grid_wrapper.visible = has and self._view_mode == "grid"
        self.page.update()

    # ── Art loading ─────────────────────────────────────────────────────────
    def _load_arts(self, metas: list[PatchMeta]) -> None:
        for meta in metas:
            try:
                path = get_art_path(meta.id)
                if path is None:
                    data = resolve_art(meta) or fetch_art(meta)
                    if data:
                        save_art(meta.id, data)
                        path = get_art_path(meta.id)
                if path is None:
                    continue
                card = self._cards.get(meta.id)
                if card:
                    card.set_art(path)
            except Exception:
                log.warning("Art load failed for %s", meta.id, exc_info=True)

    # ── Background threads ──────────────────────────────────────────────────
    def _start_threads(self) -> None:
        threading.Thread(target=self._midi_monitor, daemon=True).start()
        # Sequential on one thread, not two concurrent run_thread calls: both do
        # several page.update()s in a row during the same startup window, and two
        # threads racing to mutate/serialize the control tree at once is exactly
        # what corrupted the library render before this was serialized — confirmed
        # by reverting it and reproducing the empty-library bug again.
        self.page.run_thread(self._startup_load)

    def _startup_load(self) -> None:
        self._initial_load()
        self.generate_panel.activate()

    def _midi_monitor(self) -> None:
        while True:
            self.page.run_thread(self._midi_monitor_tick)
            time.sleep(5)

    def _midi_monitor_tick(self) -> None:
        try:
            if not self.midi.is_connected():
                self.midi.connect()
            self._refresh_midi_chip()
        except Exception:
            log.debug("MIDI monitor poll failed", exc_info=True)

    def _initial_load(self) -> None:
        cached = get_cached_patches()
        if not cached:
            return
        self._render_results(cached)
        self._status_text.value = (
            f"Loaded {len(cached)} cached patch(es) — search or browse for more"
        )
        self.page.update()
        self.page.run_thread(self._load_arts, cached)

    # ── Page assembly ───────────────────────────────────────────────────────
    def _build_page(self) -> None:
        page = self.page
        page.title = "KatanaToneStream"
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed="amber")
        page.bgcolor = theme.PAGE_BG
        page.padding = 0
        page.window.width = 960
        page.window.height = 740
        page.window.min_width = 700
        page.window.min_height = 540
        if _ICON.exists():
            page.window.icon = str(_ICON)

        self._midi_dot = ft.Icon(ft.Icons.CIRCLE, size=9, color=_MIDI_BAD)
        self._midi_label = ft.Text("Disconnected", size=12, color=_MIDI_BAD)
        self._midi_chip = ft.Container(
            content=ft.Row([self._midi_dot, self._midi_label], spacing=5, tight=True),
            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
            border_radius=12,
            border=ft.Border.all(1, _MIDI_BAD),
            margin=ft.Margin.only(right=10),
        )

        settings_btn = ft.IconButton(
            ft.Icons.SETTINGS_OUTLINED,
            tooltip="Settings",
            icon_color=theme.TEXT_DIM,
            on_click=lambda e: self.settings_pane.toggle(),
        )

        page.appbar = ft.AppBar(
            leading=ft.Container(
                ft.Icon(ft.Icons.GRAPHIC_EQ, color=theme.AMBER, size=24),
                padding=ft.Padding.only(left=14),
            ),
            leading_width=52,
            title=ft.Row(
                [
                    ft.Text(
                        "KatanaToneStream", weight=ft.FontWeight.BOLD, size=17, color="#FFFFFF"
                    ),
                    self._build_tab_switcher(),
                ],
                spacing=18,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            center_title=False,
            bgcolor=theme.SURFACE_VAR,
            actions=[
                self._midi_chip,
                self.slot_picker.control,
                settings_btn,
                self.log_panel.toggle_button,
            ],
        )

        self._status_text = ft.Text("No amp connected", size=12, color=_MIDI_BAD)
        self._status_bar = ft.Container(
            ft.Row(
                [ft.Icon(ft.Icons.INFO_OUTLINE, size=13, color=theme.TEXT_DIM), self._status_text],
                spacing=6,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor="#1A1010",
        )

        self._results_list = ft.ListView(
            spacing=8, padding=ft.Padding.symmetric(horizontal=14, vertical=10)
        )
        self._empty_state = ft.Container(
            ft.Column(
                [
                    ft.Icon(ft.Icons.MUSIC_NOTE_OUTLINED, size=72, color="#374151"),
                    ft.Text(
                        "Search for a song or patch above",
                        color="#4B5563",
                        size=14,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=14,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )
        self._results_wrapper = ft.Container(content=self._results_list, expand=True, visible=False)
        self._results_grid = ft.GridView(
            max_extent=170,
            child_aspect_ratio=0.55,
            spacing=10,
            run_spacing=10,
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
        )
        self._results_grid_wrapper = ft.Container(
            content=self._results_grid, expand=True, visible=False
        )
        results_area = ft.Stack(
            [self._empty_state, self._results_wrapper, self._results_grid_wrapper], expand=True
        )

        library_tab = ft.Column(
            [
                self.search_bar.control,
                ft.Divider(height=1, color=theme.BORDER_DIM),
                results_area,
            ],
            spacing=0,
            expand=True,
        )

        # Create/Library is switched via the chips built into the AppBar title
        # (_build_tab_switcher); this stack just shows whichever is active.
        self._create_view = ft.Container(self.generate_panel.control, expand=True, visible=True)
        self._library_view = ft.Container(library_tab, expand=True, visible=False)
        tab_content = ft.Stack([self._create_view, self._library_view], expand=True)

        content_row = ft.Row(
            [tab_content, self.settings_pane.control, self.log_panel.control],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        page.add(
            ft.Column(
                [
                    content_row,
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    self._status_bar,
                ],
                spacing=0,
                expand=True,
            )
        )


def build_app(page: ft.Page, service: PatchService, ui_log: FletLogHandler) -> None:
    """Flet entry target: construct the AppShell for a freshly opened page."""
    AppShell(page, service, ui_log)
