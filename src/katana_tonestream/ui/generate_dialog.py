"""GenerateDialog — LLM-powered tone generator modal."""

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
from ..llm_generator import PatchGenerationError, generate_patch
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


class GenerateDialog:
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
        self._confidence = ft.Container(visible=False)
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

        content = ft.Column(
            [
                ft.Row([self._provider_dd, self._model_dd], spacing=10),
                ft.Row([self._artist, self._song], spacing=10),
                self._notes,
                ft.Row([self._gen_btn], alignment=ft.MainAxisAlignment.END),
                self._progress,
                ft.Row(
                    [self._status, self._confidence],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Divider(height=1, color=theme.BORDER_DIM),
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
            width=520,
            height=560,
        )

        self._dialog = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.AUTO_AWESOME, color=theme.AMBER, size=18),
                    ft.Text("Generate Tone", size=15, weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                ],
                spacing=8,
            ),
            content=content,
            actions=[
                self._apply_btn,
                self._save_btn,
                ft.TextButton(
                    "Close",
                    style=ft.ButtonStyle(color=theme.TEXT_DIM),
                    on_click=self._on_close,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=theme.SURFACE_VAR,
            shape=ft.RoundedRectangleBorder(radius=12),
        )

    def open(self) -> None:
        self._models_cache.clear()
        self._populate_providers()
        self._page.show_dialog(self._dialog)
        if self._provider_dd.value:
            self._fetch_models_for(self._provider_dd.value)

    def _on_close(self, e=None) -> None:
        self._page.pop_dialog()

    # ── LLM engine selection ────────────────────────────────────────────────

    def _populate_providers(self) -> None:
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

    def _fetch_models_for(self, provider: str) -> None:
        self._model_dd.options = []
        self._model_dd.value = None
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
            self._model_dd.options = [ft.dropdown.Option(key=m, text=m) for m in models]
            default_m = config.default_llm_model(provider)
            selected = default_m if default_m in models else (models[0] if models else None)
            self._model_dd.value = selected
            # Editable dropdown shows .text in its field; keep it in sync with .value
            # (option key == text == model string here) so the default is visible.
            self._model_dd.text = selected or ""
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

    def _build_patch(self, name: str = "Generated") -> KatanaPatch | None:
        template = get_template()
        if template is None:
            self._set_status(
                "No base template available. Reinstall or download a patch first.", error=True
            )
            return None
        # _current_params() keys match KatanaPatch field names, so spread them directly.
        p = self._current_params()
        meta = PatchMeta(
            id=f"gen_{int(datetime.now(UTC).timestamp())}",
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
        self._gen_btn.text = "Generating…"
        self._confidence.visible = False
        self._progress.visible = True
        self._set_status("")

        def _run():
            try:
                result = generate_patch(
                    artist=artist,
                    song=song,
                    api_key=config.llm_api_key(provider),
                    model=model,
                    on_progress=self._on_progress,
                    extra=extra,
                )
                config.set_default_llm(provider, model)
                self._apply_params(result)
                conf = result.get("confidence", 0)
                self._confidence.content = ft.Row(
                    [
                        ft.Icon(ft.Icons.INSIGHTS, size=14, color=theme.AMBER),
                        ft.Text(
                            f"Confidence: {conf} / 100",
                            size=12,
                            color=theme.AMBER,
                            weight=ft.FontWeight.W_500,
                        ),
                    ],
                    spacing=6,
                )
                self._confidence.visible = True
                self._gen_btn.disabled = False
                self._gen_btn.text = "Generate"
                self._progress.visible = False
                self._set_status("")
                self._page.update()
            except PatchGenerationError as exc:
                # Already a clear, user-facing message.
                self._gen_btn.disabled = False
                self._gen_btn.text = "Generate"
                self._progress.visible = False
                self._set_status(str(exc), error=True)
                self._page.update()
            except Exception as exc:
                log.exception("Unexpected error during patch generation")
                self._gen_btn.disabled = False
                self._gen_btn.text = "Generate"
                self._progress.visible = False
                self._set_status(f"Unexpected error: {exc}", error=True)
                self._page.update()

        self._page.run_thread(_run)

    def _on_apply(self, e) -> None:
        artist = (self._artist.value or "").strip()
        song = (self._song.value or "").strip()
        name = f"{artist} - {song}" if artist and song else "Generated"

        patch = self._build_patch(name)
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
        artist = (self._artist.value or "").strip()
        song = (self._song.value or "").strip()
        name = f"{artist} - {song}" if artist and song else "Generated"

        patch = self._build_patch(name)
        if patch is None:
            return

        alb_bytes = to_alb_bytes(patch.raw_bytes)
        cache.save_patch(patch.meta, alb_bytes)
        self._set_status(f"Saved '{name}' to cache.")
        self._on_save()
