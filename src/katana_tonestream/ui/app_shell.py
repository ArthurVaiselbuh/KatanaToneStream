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
from ..config import amp_model, midi_target_patch
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
        self.search_bar = SearchBar(page, self._on_search)

        self._build_page()
        self._start_threads()

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
        self._tabs.selected_index = _CREATE_TAB
        self.page.update()

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
        )
        self._cards[meta.id] = card
        card.refresh_apply_state()
        return card.control

    def _render_results(self, results: list[PatchMeta]) -> None:
        self._cards.clear()
        self._results_list.controls.clear()
        for meta in results:
            self._results_list.controls.append(self._make_card(meta))
        has = len(results) > 0
        self._empty_state.visible = not has
        self._results_wrapper.visible = has

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
        if card and card.control in self._results_list.controls:
            self._results_list.controls.remove(card.control)
        has = len(self._results_list.controls) > 0
        self._empty_state.visible = not has
        self._results_wrapper.visible = has
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
            title=ft.Text("KatanaToneStream", weight=ft.FontWeight.BOLD, size=17, color="#FFFFFF"),
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
        results_area = ft.Stack([self._empty_state, self._results_wrapper], expand=True)

        library_tab = ft.Column(
            [self.search_bar.control, ft.Divider(height=1, color=theme.BORDER_DIM), results_area],
            spacing=0,
            expand=True,
        )

        # Create lands first/selected — generation is the app's primary flow, browsing
        # existing patches is the secondary one.
        self._tabs = ft.Tabs(
            length=2,
            selected_index=_CREATE_TAB,
            expand=True,
            content=ft.Column(
                [
                    ft.TabBar(
                        tabs=[
                            ft.Tab(label="Create", icon=ft.Icons.AUTO_AWESOME),
                            ft.Tab(label="Library", icon=ft.Icons.LIBRARY_MUSIC_OUTLINED),
                        ],
                        indicator_color=theme.AMBER,
                        label_color="#FFFFFF",
                        unselected_label_color=theme.TEXT_DIM,
                    ),
                    ft.TabBarView(
                        controls=[self.generate_panel.control, library_tab],
                        expand=True,
                    ),
                ],
                spacing=0,
                expand=True,
            ),
        )

        content_row = ft.Row(
            [self._tabs, self.settings_pane.control, self.log_panel.control],
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
