"""SettingsPane — slide-out panel for app settings (currently ToneExchange credentials)."""

import flet as ft

from ..config import (
    delete_llm_api_key,
    delete_toneexchange_credentials,
    llm_api_key,
    set_llm_api_key,
    set_toneexchange_credentials,
    toneexchange_credentials,
)
from ..llm_providers import PROVIDERS
from . import theme


class SettingsPane:
    def __init__(self, page: ft.Page, on_credentials_changed: callable) -> None:
        self._page = page
        self._on_changed = on_credentials_changed

        # ── ToneExchange ──────────────────────────────────────────────────────
        self._username = ft.TextField(
            label="ToneExchange username",
            hint_text="your@email.com",
            border_radius=8,
            focused_border_color=theme.AMBER,
            bgcolor=theme.CARD_BG,
            border_color=theme.BORDER_DIM,
            text_size=13,
        )
        self._password = ft.TextField(
            label="ToneExchange password",
            password=True,
            can_reveal_password=True,
            border_radius=8,
            focused_border_color=theme.AMBER,
            bgcolor=theme.CARD_BG,
            border_color=theme.BORDER_DIM,
            text_size=13,
        )
        self._te_status = ft.Text("", size=12, color=theme.AMBER)

        te_save_btn = ft.ElevatedButton(
            "Save",
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._te_save,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: theme.AMBER_DARK,
                    ft.ControlState.HOVERED: theme.AMBER,
                },
                color={ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        te_clear_btn = ft.TextButton(
            "Clear saved credentials",
            icon=ft.Icons.DELETE_OUTLINE,
            style=ft.ButtonStyle(color=theme.TEXT_DIM),
            on_click=self._te_clear,
        )

        # ── LLM API keys — one field per provider (Ollama is local, no key) ───
        self._llm_key_fields: dict[str, ft.TextField] = {}
        for label, key in PROVIDERS:
            if key == "ollama":
                continue
            self._llm_key_fields[key] = ft.TextField(
                label=f"{label} API key",
                password=True,
                can_reveal_password=True,
                border_radius=8,
                focused_border_color=theme.AMBER,
                bgcolor=theme.CARD_BG,
                border_color=theme.BORDER_DIM,
                text_size=13,
            )
        self._llm_status = ft.Text("", size=12, color=theme.AMBER)

        llm_save_btn = ft.ElevatedButton(
            "Save",
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._llm_save,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: theme.AMBER_DARK,
                    ft.ControlState.HOVERED: theme.AMBER,
                },
                color={ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        llm_clear_btn = ft.TextButton(
            "Clear",
            icon=ft.Icons.DELETE_OUTLINE,
            style=ft.ButtonStyle(color=theme.TEXT_DIM),
            on_click=self._llm_clear,
        )

        self.control = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SETTINGS, size=14, color=theme.AMBER),
                            ft.Text(
                                "Settings", size=13, weight=ft.FontWeight.BOLD, color="#FFFFFF"
                            ),
                        ],
                        spacing=6,
                    ),
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text(
                        "Boss Tone Exchange",
                        size=12,
                        weight=ft.FontWeight.W_600,
                        color=theme.TEXT_DIM,
                    ),
                    self._username,
                    self._password,
                    ft.Row([te_save_btn, te_clear_btn], spacing=8),
                    self._te_status,
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text(
                        "LLM API keys", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_DIM
                    ),
                    ft.Text(
                        "Add a key for each provider you want to use. Pick the provider "
                        "and model when generating.",
                        size=11,
                        color=theme.TEXT_DIM,
                    ),
                    *self._llm_key_fields.values(),
                    ft.Row([llm_save_btn, llm_clear_btn], spacing=8),
                    self._llm_status,
                ],
                spacing=12,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=300,
            bgcolor=theme.SURFACE_VAR,
            border=ft.Border(left=ft.BorderSide(1, theme.BORDER_DIM)),
            padding=ft.Padding.symmetric(horizontal=14, vertical=14),
            visible=False,
        )

    def toggle(self) -> None:
        if not self.control.visible:
            self._load_fields()
        self.control.visible = not self.control.visible
        self._page.update()

    def _load_fields(self) -> None:
        username, password = toneexchange_credentials()
        self._username.value = username
        self._password.value = password
        self._te_status.value = ""
        for provider, field in self._llm_key_fields.items():
            field.value = llm_api_key(provider)
        self._llm_status.value = ""

    def _te_save(self, e) -> None:
        username = (self._username.value or "").strip()
        password = self._password.value or ""
        if not username or not password:
            self._te_status.value = "Both fields are required."
            self._page.update()
            return
        ok = set_toneexchange_credentials(username, password)
        self._te_status.value = "Saved." if ok else "Failed to save — keyring unavailable."
        self._page.update()
        self._on_changed()

    def _te_clear(self, e) -> None:
        delete_toneexchange_credentials()
        self._username.value = ""
        self._password.value = ""
        self._te_status.value = "Credentials cleared."
        self._page.update()
        self._on_changed()

    def _llm_save(self, e) -> None:
        saved = 0
        for provider, field in self._llm_key_fields.items():
            key = (field.value or "").strip()
            if key:
                if not set_llm_api_key(provider, key):
                    self._llm_status.value = "Failed to save — keyring unavailable."
                    self._page.update()
                    return
                saved += 1
            else:
                delete_llm_api_key(provider)
        self._llm_status.value = f"Saved {saved} API key(s)." if saved else "No keys entered."
        self._page.update()

    def _llm_clear(self, e) -> None:
        for provider, field in self._llm_key_fields.items():
            delete_llm_api_key(provider)
            field.value = ""
        self._llm_status.value = "All API keys cleared."
        self._page.update()
