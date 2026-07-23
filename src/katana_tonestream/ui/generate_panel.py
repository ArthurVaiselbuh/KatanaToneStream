"""GeneratePanel — LLM-powered tone generator; the Create tab's content."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import flet as ft

from .. import cache, config
from ..katana_catalog import (
    DELAY_TYPES,
    FX_TYPES,
    OD_TYPES,
    PREAMP_TYPES,
    REVERB_TYPES,
    variation_for_preamp,
)
from ..llm_generator import ChatEntry, PatchGenerationError, ToneSession
from ..llm_providers import configured_providers, list_models
from ..models import KatanaPatch, PatchMeta
from ..patch_builder import build_raw_bytes, get_template, to_alb_bytes
from . import theme

log = logging.getLogger(__name__)


def _dropdown_options(mapping: dict) -> list[ft.dropdown.Option]:
    return [ft.dropdown.Option(key=str(k), text=v) for k, v in mapping.items()]


def _slider_row(label: str, lo: int, hi: int) -> tuple[ft.Slider, ft.TextField, ft.Row]:
    """Returns (slider, textfield, row_control) with bidirectional sync."""
    mid = (lo + hi) // 2
    field = theme.text_field(
        value=str(mid),
        width=56,
        text_align=ft.TextAlign.CENTER,
        border_radius=6,
        text_size=12,
        content_padding=ft.Padding.symmetric(horizontal=6, vertical=8),
    )
    slider = ft.Slider(
        min=lo,
        max=hi,
        value=mid,
        active_color=theme.AMBER,
        inactive_color=theme.BORDER_DIM,
        expand=True,
    )

    def _slider_changed(e):
        field.value = str(int(slider.value))
        field.update()

    def _field_changed(e):
        try:
            v = max(lo, min(hi, int(field.value or mid)))
            slider.value = v
            field.value = str(v)
        except ValueError:
            pass
        slider.update()

    slider.on_change = _slider_changed
    field.on_blur = _field_changed
    field.on_submit = _field_changed

    row = ft.Row(
        [
            ft.Text(label, size=12, color=theme.TEXT_DIM, width=56),
            slider,
            field,
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return slider, field, row


class GeneratePanel:
    def __init__(
        self,
        page: ft.Page,
        midi,
        on_save: Callable[[], None],
    ) -> None:
        self._page = page
        self._midi = midi
        self._on_save = on_save
        self._models_cache: dict[str, list[str]] = {}
        self._session: ToneSession | None = None
        self._editing_meta: PatchMeta | None = None
        self._pending_row: ft.Row | None = None
        self._pending_spinner: ft.ProgressRing | None = None

        # ── LLM engine pickers ────────────────────────────────────────────────
        self._provider_dd = theme.dropdown("LLM provider", on_select=self._on_provider_change)
        # Editable + enable_filter turns this into a type-to-search box: typing a
        # substring (e.g. "flash") filters the menu case-insensitively.
        self._model_dd = theme.dropdown(
            "Model",
            editable=True,
            enable_filter=True,
            hint_text="Type to filter, e.g. flash",
        )

        # ── Input fields ──────────────────────────────────────────────────────
        self._artist = theme.text_field("Artist", hint_text="e.g. Steve Vai", expand=True)
        self._song = theme.text_field("Song", hint_text="e.g. For the Love of God", expand=True)
        self._notes = theme.text_field(
            "Additional requests (optional)",
            hint_text="e.g. brighter, less gain, add a slapback delay",
            multiline=True,
            min_lines=1,
            max_lines=3,
        )

        # Patch name shown to Apply/Save; defaults to "Artist - Song" but is always
        # user-editable, so a generated or edited patch can be renamed before saving.
        self._patch_name = theme.text_field(
            "Patch name",
            expand=True,
        )

        # ── Refinement chat ───────────────────────────────────────────────────
        self._chat_list = ft.Column(
            [], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO, auto_scroll=True
        )
        self._chat_input = theme.text_field(
            hint_text="Ask for a tweak",
            multiline=True,
            shift_enter=True,
            min_lines=1,
            max_lines=4,
            on_submit=self._on_send,
            expand=True,
        )
        self._send_btn = ft.IconButton(
            ft.Icons.SEND,
            icon_color=theme.AMBER,
            tooltip="Send",
            on_click=self._on_send,
        )
        self._new_btn = ft.IconButton(
            ft.Icons.ADD_COMMENT_OUTLINED,
            icon_color=theme.TEXT_DIM,
            tooltip="New tone",
            on_click=lambda e: self._show_form(),
        )
        self._back_to_chat_btn = ft.IconButton(
            ft.Icons.FORUM_OUTLINED,
            icon_color=theme.AMBER,
            tooltip="Back to the conversation",
            visible=False,
            on_click=lambda e: self._show_chat(),
        )

        # ── Amp section ───────────────────────────────────────────────────────
        self._preamp_type = theme.dropdown("Preamp type", _dropdown_options(PREAMP_TYPES), "0")
        self._gain_slider, self._gain_field, gain_row = _slider_row("Gain", 0, 120)
        self._bass_slider, self._bass_field, bass_row = _slider_row("Bass", 0, 100)
        self._mid_slider, self._mid_field, mid_row = _slider_row("Mid", 0, 100)
        self._treble_slider, self._treble_field, treble_row = _slider_row("Treble", 0, 100)
        self._presence_slider, self._presence_field, presence_row = _slider_row("Presence", 0, 100)

        # ── OD/DS section ─────────────────────────────────────────────────────
        self._od_type = theme.dropdown("OD/DS type", _dropdown_options(OD_TYPES), "0")
        self._od_on = ft.Switch(label="OD/DS on", active_color=theme.AMBER, value=False)
        self._od_drive_slider, self._od_drive_field, od_drive_row = _slider_row("Drive", 0, 120)
        self._od_level_slider, self._od_level_field, od_level_row = _slider_row("Level", 0, 100)

        # ── FX section ────────────────────────────────────────────────────────
        self._fx1_type = theme.dropdown("FX1 type", _dropdown_options(FX_TYPES), "0")
        self._fx1_on = ft.Switch(label="FX1 on", active_color=theme.AMBER, value=False)
        self._fx2_type = theme.dropdown("FX2 type", _dropdown_options(FX_TYPES), "0")
        self._fx2_on = ft.Switch(label="FX2 on", active_color=theme.AMBER, value=False)

        # ── Delay section ─────────────────────────────────────────────────────
        self._delay_type = theme.dropdown("Delay type", _dropdown_options(DELAY_TYPES), "0")
        self._delay_on = ft.Switch(label="Delay on", active_color=theme.AMBER, value=False)
        self._delay_level_slider, self._delay_level_field, delay_level_row = _slider_row(
            "Level", 0, 120
        )

        # ── Reverb section ────────────────────────────────────────────────────
        self._reverb_type = theme.dropdown("Reverb type", _dropdown_options(REVERB_TYPES), "0")
        self._reverb_on = ft.Switch(label="Reverb on", active_color=theme.AMBER, value=False)
        self._reverb_level_slider, self._reverb_level_field, reverb_level_row = _slider_row(
            "Level", 0, 100
        )

        # ── Status / progress ─────────────────────────────────────────────────
        self._status = ft.Text("", size=12, color=theme.AMBER, expand=True, no_wrap=False)
        self._progress = ft.ProgressBar(
            visible=False,
            color=theme.AMBER,
            bgcolor=theme.BORDER_DIM,
        )

        # ── Buttons ───────────────────────────────────────────────────────────
        self._gen_btn = theme.amber_button(
            "Generate",
            self._on_generate,
            icon=ft.Icons.AUTO_AWESOME,
        )
        self._free_btn = ft.OutlinedButton(
            "Free chat",
            icon=ft.Icons.CHAT_OUTLINED,
            on_click=self._on_free_chat,
            tooltip="Skip artist/song — describe the tone you want directly",
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: theme.AMBER},
                shape=ft.RoundedRectangleBorder(radius=20),
                padding=ft.Padding.symmetric(horizontal=20, vertical=14),
            ),
        )
        self._apply_btn = ft.ElevatedButton(
            "Apply to amp",
            icon=ft.Icons.SEND,
            on_click=self._on_apply,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.DEFAULT: "#1D4ED8", ft.ControlState.HOVERED: "#2563EB"},
                color={ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=20),
                padding=ft.Padding.symmetric(horizontal=20, vertical=14),
            ),
        )
        self._save_btn = ft.ElevatedButton(
            "Save to cache",
            icon=ft.Icons.BOOKMARK_OUTLINED,
            on_click=self._on_save_patch,
            style=ft.ButtonStyle(
                bgcolor={ft.ControlState.DEFAULT: "#065F46", ft.ControlState.HOVERED: "#059669"},
                color={ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=20),
                padding=ft.Padding.symmetric(horizontal=20, vertical=14),
            ),
        )
        self._save_new_btn = ft.OutlinedButton(
            "Save as new",
            icon=ft.Icons.BOOKMARK_ADD_OUTLINED,
            visible=False,
            on_click=self._on_save_as_new,
            style=ft.ButtonStyle(
                color={ft.ControlState.DEFAULT: theme.AMBER},
                shape=ft.RoundedRectangleBorder(radius=20),
                padding=ft.Padding.symmetric(horizontal=20, vertical=14),
            ),
        )

        self._form_view = ft.Column(
            [
                ft.Row([self._artist, self._song], spacing=10),
                self._notes,
                ft.Row(
                    [self._back_to_chat_btn, self._free_btn, self._gen_btn],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=8,
                ),
            ],
            spacing=10,
            tight=True,
        )
        # Centered, narrow — there's nothing to show a sidebar of dials for until a
        # conversation has actually produced settings.
        self._form_body = ft.Container(
            ft.Column(
                [
                    ft.Text(
                        "Describe the tone you want, or name an artist and song to recreate",
                        size=13,
                        color=theme.TEXT_DIM,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    self._form_view,
                ],
                spacing=16,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                width=520,
            ),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

        self._chat_panel = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            "Refine the tone",
                            size=11,
                            weight=ft.FontWeight.W_600,
                            color=theme.TEXT_DIM,
                            expand=True,
                        ),
                        self._new_btn,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    self._chat_list,
                    expand=True,
                    padding=ft.Padding.all(8),
                    border=ft.Border.all(1, theme.BORDER_DIM),
                    border_radius=8,
                ),
                ft.Row(
                    [self._chat_input, self._send_btn],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
            ],
            spacing=8,
            tight=True,
            expand=True,
        )
        # The amp/EQ/FX dials only matter once a conversation has actually set them,
        # so they live in a sidebar next to the chat rather than dominating the view.
        self._dial_sidebar = ft.Container(
            ft.Column(
                [
                    ft.Text("Amp", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM),
                    self._preamp_type,
                    gain_row,
                    ft.Text("EQ", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM),
                    bass_row,
                    mid_row,
                    treble_row,
                    presence_row,
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text("OD/DS", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM),
                    ft.Row(
                        [self._od_type, self._od_on],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    od_drive_row,
                    od_level_row,
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text("FX", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM),
                    ft.Row(
                        [self._fx1_type, self._fx1_on],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    ft.Row(
                        [self._fx2_type, self._fx2_on],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text(
                        "Delay / Reverb", size=11, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM
                    ),
                    ft.Row(
                        [self._delay_type, self._delay_on],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    delay_level_row,
                    ft.Row(
                        [self._reverb_type, self._reverb_on],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                    ),
                    reverb_level_row,
                ],
                spacing=10,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            width=config.dial_sidebar_width(),
            padding=ft.Padding.only(left=10),
        )
        self._chat_body = ft.Row(
            [
                self._chat_panel,
                theme.resize_handle(
                    self._dial_sidebar,
                    min_width=200,
                    on_change=config.set_dial_sidebar_width,
                ),
                self._dial_sidebar,
            ],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            visible=False,
        )

        self.control = ft.Container(
            ft.Column(
                [
                    ft.Row([self._provider_dd, self._model_dd], spacing=10),
                    self._form_body,
                    self._chat_body,
                    self._progress,
                    ft.Row(
                        [self._status],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Row(
                        [self._patch_name, self._apply_btn, self._save_btn, self._save_new_btn],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=10,
                expand=True,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=14),
            expand=True,
        )

    def refresh_apply_state(self) -> None:
        """Dim/enable the apply button based on MIDI connection (same rule as cards)."""
        connected = self._midi.is_connected()
        self._apply_btn.disabled = not connected
        self._apply_btn.opacity = 1.0 if connected else 0.4
        self._apply_btn.tooltip = None if connected else "Connect the Katana to apply"

    def activate(self) -> None:
        """One-time setup once the panel is mounted at app startup."""
        self._populate_providers()
        self.refresh_apply_state()
        self._show_form()
        if self._provider_dd.value:
            self._fetch_models_for(self._provider_dd.value)

    def edit(self, meta: PatchMeta) -> None:
        """Load a previously saved generated patch, resuming its full LLM conversation."""
        data = cache.get_generation_session(meta.id)
        if data is None:
            log.warning("Edit requested for %s but no generation session was saved", meta.id)
            return

        self._editing_meta = meta
        self._models_cache.clear()
        self._populate_providers()

        provider = data.get("provider") or ""
        configured_keys = [o.key for o in self._provider_dd.options]
        if provider in configured_keys:
            self._provider_dd.value = provider
        self.refresh_apply_state()

        self._artist.value = data.get("artist", "")
        self._song.value = data.get("song", "")
        self._notes.value = data.get("notes", "")
        self._patch_name.value = meta.name

        session = ToneSession(
            api_key=config.llm_api_key(self._provider_dd.value) if self._provider_dd.value else "",
            model=data.get("model") or "openai/gpt-4o",
            timeout=config.llm_timeout(),
            on_entry=self._on_chat_entry,
            on_request=self._on_chat_request,
        )
        session.load_history(data.get("history", []))
        self._session = session

        self._chat_list.controls.clear()
        self._pending_row = None
        self._pending_spinner = None
        for entry in session.history:
            self._add_bubble(entry)

        params = data.get("params")
        if params:
            self._apply_params(params)

        self._update_save_buttons()
        self._set_status(f"Editing '{meta.name}'")
        self._show_chat()
        if self._provider_dd.value:
            self._fetch_models_for(self._provider_dd.value, preferred_model=data.get("model"))

    # ── LLM engine selection ────────────────────────────────────────────────

    def _populate_providers(self) -> None:
        # Keep Generate disabled until a model is actually available (set in
        # _fetch_models_for) so it can't be fired before the model list loads.
        # Also reset a stale "Generating…" label left by a prior hung run.
        self._gen_btn.disabled = True
        self._gen_btn.content = "Generate"
        self._free_btn.disabled = True
        configured = configured_providers()
        self._provider_dd.options = [
            ft.dropdown.Option(key=k, text=label) for label, k in configured
        ]
        self._model_dd.options = []
        self._model_dd.value = None
        if not configured:
            self._provider_dd.value = None
            self._set_status(
                "No LLM providers configured — add an API key in Settings.", error=True
            )
            return
        keys = [k for _, k in configured]
        default_p = config.default_llm_provider()
        self._provider_dd.value = default_p if default_p in keys else keys[0]
        self._set_status("")

    def _on_provider_change(self, e) -> None:
        if self._provider_dd.value:
            self._fetch_models_for(self._provider_dd.value)

    def _fetch_models_for(self, provider: str, preferred_model: str | None = None) -> None:
        self._model_dd.options = []
        self._model_dd.value = None
        # Block Generate while the list loads (also covers switching provider).
        self._gen_btn.disabled = True
        self._free_btn.disabled = True
        self._set_status(f"Fetching models for {provider}…")

        def _run():
            models = self._models_cache.get(provider)
            if models is None:
                try:
                    models = list_models(provider, config.llm_api_key(provider))
                except Exception as exc:
                    log.warning("Model fetch failed for %s: %s", provider, exc)
                    models = []
                self._models_cache[provider] = models
            if self._provider_dd.value != provider:
                # The user switched providers again while this fetch was in flight
                # (e.g. Ollama's discovery call is slow) — a later fetch for the
                # now-current provider owns the dropdown; applying this one would
                # clobber it with stale data for a provider that's no longer selected.
                return
            self._model_dd.options = [ft.dropdown.Option(key=m, text=m) for m in models]
            default_m = preferred_model or config.default_llm_model(provider)
            selected = default_m if default_m in models else (models[0] if models else None)
            self._model_dd.value = selected
            # Editable dropdown shows .text in its field; keep it in sync with .value
            # (option key == text == model string here) so the default is visible.
            self._model_dd.text = selected or ""
            # Only allow Generate once a model is actually selectable.
            self._gen_btn.disabled = selected is None
            self._free_btn.disabled = selected is None
            if models:
                self._set_status("")
            else:
                self._set_status(
                    f"No models for {provider} — check the API key in Settings.", error=True
                )
            self._page.update()

        self._page.run_thread(_run)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, error: bool = False) -> None:
        self._status.value = msg
        self._status.color = "#EF4444" if error else theme.AMBER
        self._page.update()

    def _on_progress(self, msg: str) -> None:
        self._status.value = msg
        self._status.color = theme.AMBER
        self._page.update()

    def _update_save_buttons(self) -> None:
        editing = self._editing_meta is not None
        self._save_btn.content = "Update patch" if editing else "Save to cache"
        self._save_new_btn.visible = editing

    # ── Refinement chat ───────────────────────────────────────────────────────

    def _show_form(self) -> None:
        self._form_body.visible = True
        self._chat_body.visible = False
        self._back_to_chat_btn.visible = self._session is not None
        self._page.update()

    def _show_chat(self) -> None:
        self._form_body.visible = False
        self._chat_body.visible = True
        self._page.update()

    def _on_chat_request(self, entry: ChatEntry) -> None:
        """Session callback — a user turn was just dispatched to the provider."""
        self._add_pending_bubble(entry)
        self._page.update()

    def _on_chat_entry(self, entry: ChatEntry) -> None:
        """Session callback — runs on the generate/refine worker thread."""
        # The user turn is already on screen as the pending bubble — keep it
        # (spinner off) rather than adding a duplicate.
        if entry.role == "user" and self._pending_row is not None:
            self._resolve_pending_bubble(keep=True)
        else:
            self._add_bubble(entry)
        self._page.update()

    def _add_pending_bubble(self, entry: ChatEntry) -> None:
        """User bubble shown the moment a turn is sent, with a spinner while waiting."""
        spinner = ft.ProgressRing(width=14, height=14, stroke_width=2, color=theme.AMBER)
        if entry.auto:
            bubble = self._collapsed_bubble(entry)
        else:
            text = entry.display or entry.content
            bubble = ft.Container(
                ft.Text(text, size=12, color="#E5E7EB", selectable=True),
                bgcolor="#3B2A12",
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                width=420 if (len(text) > 55 or "\n" in text) else None,
            )
        self._pending_spinner = spinner
        self._pending_row = ft.Row(
            [spinner, bubble],
            alignment=ft.MainAxisAlignment.END,
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._chat_list.controls.append(self._pending_row)

    def _resolve_pending_bubble(self, keep: bool) -> None:
        if self._pending_row is None:
            return
        if keep:
            self._pending_spinner.visible = False
        elif self._pending_row in self._chat_list.controls:
            self._chat_list.controls.remove(self._pending_row)
        self._pending_row = None
        self._pending_spinner = None

    def _add_bubble(self, entry: ChatEntry) -> None:
        is_user = entry.role == "user"
        if entry.auto:
            bubble = self._collapsed_bubble(entry)
        else:
            text = entry.display or entry.content
            body = [ft.Text(text, size=12, color="#E5E7EB", selectable=True)]
            # An assistant reply whose display (the "message") differs from its
            # raw content is a refine JSON reply — offer to reveal the JSON.
            if not is_user and entry.display and entry.display != entry.content:
                body.append(self._json_expander(entry.content))
            bubble = ft.Container(
                ft.Column(body, spacing=6, tight=True),
                bgcolor="#3B2A12" if is_user else theme.CARD_BG,
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border_radius=10,
                # Fixed width forces long text to wrap; short messages size to fit.
                width=420 if (len(text) > 55 or "\n" in text or len(body) > 1) else None,
            )
        self._chat_list.controls.append(
            ft.Row(
                [bubble],
                alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START,
            )
        )

    def _json_expander(self, raw: str) -> ft.Column:
        """A 'Show JSON' toggle revealing the model's raw reply, pretty-printed."""
        try:
            pretty = json.dumps(json.loads(raw), indent=2)
        except (json.JSONDecodeError, TypeError):
            pretty = raw
        json_text = ft.Text(
            pretty,
            size=11,
            color=theme.TEXT_DIM,
            selectable=True,
            font_family="monospace",
            visible=False,
        )
        label = ft.Text("Show JSON", size=11, italic=True, color=theme.AMBER)
        toggle = ft.Row(
            [ft.Icon(ft.Icons.DATA_OBJECT, size=12, color=theme.AMBER), label],
            spacing=4,
            tight=True,
        )

        def _toggle(e):
            json_text.visible = not json_text.visible
            label.value = "Hide JSON" if json_text.visible else "Show JSON"
            self._page.update()

        return ft.Column(
            [ft.GestureDetector(toggle, on_tap=_toggle), json_text], spacing=6, tight=True
        )

    def _collapsed_bubble(self, entry: ChatEntry) -> ft.Container:
        """Automatic prompt as a compact bubble; tapping toggles the full text."""
        full = ft.Text(entry.content, size=11, color=theme.TEXT_DIM, selectable=True, visible=False)
        header = ft.Row(
            [
                ft.Icon(ft.Icons.UNFOLD_MORE, size=12, color=theme.TEXT_DIM),
                ft.Text(
                    entry.label or "Automatic prompt",
                    size=11,
                    italic=True,
                    color=theme.TEXT_DIM,
                ),
            ],
            spacing=4,
            tight=True,
        )
        container = ft.Container(
            ft.Column([header, full], spacing=6, tight=True),
            bgcolor="#2A2114",
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border_radius=10,
        )

        def _toggle(e):
            full.visible = not full.visible
            container.width = 440 if full.visible else None
            self._page.update()

        container.on_click = _toggle
        return container

    def _add_chat_note(self, text: str) -> None:
        self._chat_list.controls.append(
            ft.Row(
                [ft.Text(text, size=11, italic=True, color=theme.AMBER)],
                alignment=ft.MainAxisAlignment.CENTER,
            )
        )

    def _on_send(self, e=None) -> None:
        text = (self._chat_input.value or "").strip()
        if not text or self._session is None:
            return
        provider = self._provider_dd.value or ""
        model = self._model_dd.value or ""
        if not provider or not model:
            self._set_status("Pick an LLM provider and model.", error=True)
            return
        # The conversation follows the dropdowns, so switching provider/model
        # mid-chat (e.g. away from a rate-limited one) takes effect on this send.
        self._session.set_engine(config.llm_api_key(provider), model)
        self._chat_input.value = ""
        self._chat_input.disabled = True
        self._send_btn.disabled = True
        self._set_status("")

        def _run():
            try:
                # The pending user bubble arrives via on_request as soon as refine
                # dispatches; the assistant bubble via on_entry on success.
                _, partial = self._session.refine(text, self._current_params())
                if partial:
                    self._apply_params({**self._current_params(), **partial})
                    self._add_chat_note("Updated: " + ", ".join(partial))
            except PatchGenerationError as exc:
                self._resolve_pending_bubble(keep=False)
                self._chat_input.value = text  # let the user retry the same message
                self._set_status(str(exc), error=True)
            except Exception as exc:
                log.exception("Unexpected error during refinement")
                self._resolve_pending_bubble(keep=False)
                self._chat_input.value = text
                self._set_status(f"Unexpected error: {exc}", error=True)
            finally:
                self._chat_input.disabled = False
                self._send_btn.disabled = False
                self._page.update()

        self._page.run_thread(_run)

    def _current_params(self) -> dict:
        return {
            "preamp_type": int(self._preamp_type.value or 0),
            "preamp_gain": int(self._gain_slider.value),
            "bass": int(self._bass_slider.value),
            "mid": int(self._mid_slider.value),
            "treble": int(self._treble_slider.value),
            "presence": int(self._presence_slider.value),
            "od_type": int(self._od_type.value or 0),
            "od_on": self._od_on.value,
            "od_drive": int(self._od_drive_slider.value),
            "od_level": int(self._od_level_slider.value),
            "fx1_type": int(self._fx1_type.value or 0),
            "fx1_on": self._fx1_on.value,
            "fx2_type": int(self._fx2_type.value or 0),
            "fx2_on": self._fx2_on.value,
            "delay_type": int(self._delay_type.value or 0),
            "delay_on": self._delay_on.value,
            "delay_level": int(self._delay_level_slider.value),
            "reverb_type": int(self._reverb_type.value or 0),
            "reverb_on": self._reverb_on.value,
            "reverb_level": int(self._reverb_level_slider.value),
        }

    def _apply_params(self, params: dict) -> None:
        self._preamp_type.value = str(params["preamp_type"])
        self._gain_slider.value = params["preamp_gain"]
        self._gain_field.value = str(params["preamp_gain"])
        self._bass_slider.value = params["bass"]
        self._bass_field.value = str(params["bass"])
        self._mid_slider.value = params["mid"]
        self._mid_field.value = str(params["mid"])
        self._treble_slider.value = params["treble"]
        self._treble_field.value = str(params["treble"])
        self._presence_slider.value = params["presence"]
        self._presence_field.value = str(params["presence"])
        self._od_type.value = str(params["od_type"])
        self._od_on.value = params["od_on"]
        self._od_drive_slider.value = params["od_drive"]
        self._od_drive_field.value = str(params["od_drive"])
        self._od_level_slider.value = params["od_level"]
        self._od_level_field.value = str(params["od_level"])
        self._fx1_type.value = str(params["fx1_type"])
        self._fx1_on.value = params["fx1_on"]
        self._fx2_type.value = str(params["fx2_type"])
        self._fx2_on.value = params["fx2_on"]
        self._delay_type.value = str(params["delay_type"])
        self._delay_on.value = params["delay_on"]
        self._delay_level_slider.value = params["delay_level"]
        self._delay_level_field.value = str(params["delay_level"])
        self._reverb_type.value = str(params["reverb_type"])
        self._reverb_on.value = params["reverb_on"]
        self._reverb_level_slider.value = params["reverb_level"]
        self._reverb_level_field.value = str(params["reverb_level"])

    def _build_patch(
        self, name: str = "Generated", patch_id: str | None = None
    ) -> KatanaPatch | None:
        template = get_template()
        if template is None:
            self._set_status(
                "No base template available. Reinstall or download a patch first.", error=True
            )
            return None
        # _current_params() keys match KatanaPatch field names, so spread them directly.
        p = self._current_params()
        meta = PatchMeta(
            id=patch_id or f"gen_{int(datetime.now(UTC).timestamp())}",
            name=name,
            author="LLM",
            source="generated",
            rating=0.0,
            download_url="",
        )
        patch = KatanaPatch(
            meta=meta,
            raw_params=p,
            patch_name=name,
            variation=variation_for_preamp(p["preamp_type"]),
            **p,
        )
        patch.raw_bytes = build_raw_bytes(patch, template)
        return patch

    def _save_generation_session(self, patch_id: str) -> None:
        if self._session is None:
            return
        cache.save_generation_session(
            patch_id,
            {
                "provider": self._provider_dd.value or "",
                "model": self._session.model,
                "artist": self._artist.value or "",
                "song": self._song.value or "",
                "notes": self._notes.value or "",
                "params": self._current_params(),
                "history": self._session.history_dicts(),
            },
        )

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_generate(self, e) -> None:
        artist = (self._artist.value or "").strip()
        song = (self._song.value or "").strip()
        extra = (self._notes.value or "").strip()
        provider = self._provider_dd.value or ""
        model = self._model_dd.value or ""
        if not artist or not song:
            self._set_status("Enter both artist and song name.", error=True)
            return
        if not provider or not model:
            self._set_status("Pick an LLM provider and model.", error=True)
            return

        self._gen_btn.disabled = True
        self._gen_btn.content = "Generating…"
        self._free_btn.disabled = True
        self._progress.visible = True
        self._set_status("")

        # Generate starts a fresh conversation — this is the point where any
        # previous session's history is discarded.
        self._chat_list.controls.clear()
        self._pending_row = None
        self._pending_spinner = None
        self._editing_meta = None
        self._update_save_buttons()
        self._patch_name.value = f"{artist} - {song}"
        session = ToneSession(
            api_key=config.llm_api_key(provider),
            model=model,
            timeout=config.llm_timeout(),
            on_entry=self._on_chat_entry,
            on_request=self._on_chat_request,
        )
        self._session = session
        self._show_chat()

        def _run():
            try:
                result = session.generate(
                    artist=artist,
                    song=song,
                    extra=extra,
                    on_progress=self._on_progress,
                )
                config.set_default_llm(provider, model)
                self._apply_params(result)
                self._gen_btn.disabled = False
                self._gen_btn.content = "Generate"
                self._free_btn.disabled = False
                self._progress.visible = False
                self._set_status("")
                self._page.update()
            except PatchGenerationError as exc:
                # Already a clear, user-facing message.
                self._resolve_pending_bubble(keep=False)
                self._gen_btn.disabled = False
                self._gen_btn.content = "Generate"
                self._free_btn.disabled = False
                self._progress.visible = False
                self._set_status(str(exc), error=True)
                self._page.update()
            except Exception as exc:
                log.exception("Unexpected error during patch generation")
                self._resolve_pending_bubble(keep=False)
                self._gen_btn.disabled = False
                self._gen_btn.content = "Generate"
                self._free_btn.disabled = False
                self._progress.visible = False
                self._set_status(f"Unexpected error: {exc}", error=True)
                self._page.update()

        self._page.run_thread(_run)

    def _on_free_chat(self, e) -> None:
        """Start a chat-first session: no generation phases, describe the tone directly."""
        provider = self._provider_dd.value or ""
        model = self._model_dd.value or ""
        if not provider or not model:
            self._set_status("Pick an LLM provider and model.", error=True)
            return

        self._set_status("")
        # Like Generate, this is the point where any previous conversation is discarded.
        self._chat_list.controls.clear()
        self._pending_row = None
        self._pending_spinner = None
        self._editing_meta = None
        self._update_save_buttons()
        self._patch_name.value = ""
        self._session = ToneSession(
            api_key=config.llm_api_key(provider),
            model=model,
            timeout=config.llm_timeout(),
            on_entry=self._on_chat_entry,
            on_request=self._on_chat_request,
        )
        config.set_default_llm(provider, model)
        self._add_chat_note('Describe the tone you want — e.g. "warm blues lead, light reverb"')
        self._show_chat()

    def _current_patch_name(self) -> str:
        """The name to save/apply under — the editable field, falling back to
        "Artist - Song" (or "Generated") only while it's still untouched."""
        typed = (self._patch_name.value or "").strip()
        if typed:
            return typed
        artist = (self._artist.value or "").strip()
        song = (self._song.value or "").strip()
        return f"{artist} - {song}" if artist and song else "Generated"

    def _on_apply(self, e) -> None:
        patch = self._build_patch(self._current_patch_name())
        if patch is None:
            return

        self._set_status("Connecting…")
        try:
            if not self._midi.is_connected():
                self._midi.connect()
            self._midi.send_patch(patch)
            self._set_status(f"Applied '{patch.display_name}' to TONE buffer.")
        except Exception as exc:
            log.warning("Apply failed: %s", exc)
            self._set_status(f"Apply failed: {exc}", error=True)

    def _on_save_patch(self, e) -> None:
        self._save_patch(overwrite=self._editing_meta is not None)

    def _on_save_as_new(self, e) -> None:
        self._save_patch(overwrite=False)

    def _save_patch(self, overwrite: bool) -> None:
        name = self._current_patch_name()

        existing_id = self._editing_meta.id if overwrite and self._editing_meta else None
        patch = self._build_patch(name, patch_id=existing_id)
        if patch is None:
            return

        alb_bytes = to_alb_bytes(patch.raw_bytes)
        cache.save_patch(patch.meta, alb_bytes)
        self._save_generation_session(patch.meta.id)
        self._editing_meta = patch.meta
        self._update_save_buttons()
        self._set_status(f"{'Updated' if overwrite else 'Saved'} '{name}' in cache.")
        self._on_save()
