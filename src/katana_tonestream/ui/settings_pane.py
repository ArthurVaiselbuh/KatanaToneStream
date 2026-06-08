"""SettingsPane — slide-out panel for app settings (currently ToneExchange credentials)."""

import flet as ft

from ..config import (
    delete_toneexchange_credentials,
    set_toneexchange_credentials,
    toneexchange_credentials,
)
from . import theme


class SettingsPane:
    def __init__(self, page: ft.Page, on_credentials_changed: callable) -> None:
        self._page = page
        self._on_changed = on_credentials_changed

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
        self._status = ft.Text("", size=12, color=theme.AMBER)

        save_btn = ft.ElevatedButton(
            "Save",
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._save,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: theme.AMBER_DARK,
                    ft.ControlState.HOVERED: theme.AMBER,
                },
                color={ft.ControlState.DEFAULT: "#FFFFFF"},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
        )
        clear_btn = ft.TextButton(
            "Clear saved credentials",
            icon=ft.Icons.DELETE_OUTLINE,
            style=ft.ButtonStyle(color=theme.TEXT_DIM),
            on_click=self._clear,
        )

        self.control = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.SETTINGS, size=14, color=theme.AMBER),
                            ft.Text("Settings", size=13,
                                    weight=ft.FontWeight.BOLD, color="#FFFFFF"),
                        ],
                        spacing=6,
                    ),
                    ft.Divider(height=1, color=theme.BORDER_DIM),
                    ft.Text("Boss Tone Exchange", size=12, weight=ft.FontWeight.W_600,
                            color=theme.TEXT_DIM),
                    self._username,
                    self._password,
                    ft.Row([save_btn, clear_btn], spacing=8),
                    self._status,
                ],
                spacing=12,
                tight=True,
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
        self._status.value = ""

    def _save(self, e) -> None:
        username = (self._username.value or "").strip()
        password = self._password.value or ""
        if not username or not password:
            self._status.value = "Both fields are required."
            self._page.update()
            return
        ok = set_toneexchange_credentials(username, password)
        self._status.value = "Saved." if ok else "Failed to save — keyring unavailable."
        self._page.update()
        self._on_changed()

    def _clear(self, e) -> None:
        delete_toneexchange_credentials()
        self._username.value = ""
        self._password.value = ""
        self._status.value = "Credentials cleared."
        self._page.update()
        self._on_changed()
