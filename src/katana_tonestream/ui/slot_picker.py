"""SlotPicker — the app-bar chip + dialog for choosing the target patch slot.

``target`` is None (write to the live TONE buffer only, no program change) or an
integer 0–39 (A1…E8) which the service turns into a MIDI program change.
"""

import flet as ft

from . import theme

SLOT_NAMES = [f"{b}{n}" for b in "ABCDE" for n in range(1, 9)]  # A1…E8


class SlotPicker:
    def __init__(self, page: ft.Page, initial: int | None = None) -> None:
        self._page = page
        self.target: int | None = initial

        self._label = ft.Text(
            self._display(), size=12, color=theme.AMBER, weight=ft.FontWeight.W_600
        )
        self.control = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.PIANO, size=12, color=theme.AMBER), self._label],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=5),
            border_radius=12,
            border=ft.Border.all(1, theme.AMBER_DARK),
            margin=ft.Margin.only(right=4),
            tooltip=(
                "Target slot (click to change). 'TONE' = write to live buffer only, no slot switch."
            ),
            on_click=self._open,
        )

    def _display(self) -> str:
        return f"→ {SLOT_NAMES[self.target]}" if self.target is not None else "→ TONE"

    def _open(self, e) -> None:
        def pick(pc: int | None) -> None:
            self.target = pc
            self._label.value = self._display()
            dlg.open = False
            self._page.update()

        tone_active = self.target is None
        tone_btn = ft.TextButton(
            "TONE only (no slot switch)",
            style=ft.ButtonStyle(
                color=theme.AMBER if tone_active else theme.TEXT_DIM,
                bgcolor=theme.AMBER_DARK if tone_active else "transparent",
                padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            ),
            on_click=lambda e: pick(None),
        )
        rows = [ft.Row([tone_btn], tight=True)]
        for bank_i, bank in enumerate("ABCDE"):
            cells = []
            for slot_i in range(1, 9):
                pc = bank_i * 8 + slot_i - 1
                active = pc == self.target
                cells.append(
                    ft.TextButton(
                        f"{bank}{slot_i}",
                        style=ft.ButtonStyle(
                            color=theme.AMBER if active else "#FFFFFF",
                            bgcolor=theme.AMBER_DARK if active else "transparent",
                            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
                        ),
                        on_click=lambda e, p=pc: pick(p),
                    )
                )
            rows.append(ft.Row(cells, spacing=2, tight=True))

        dlg = ft.AlertDialog(
            title=ft.Text("Select target patch slot", size=14, weight=ft.FontWeight.BOLD),
            content=ft.Column(rows, spacing=2, tight=True),
            bgcolor=theme.CARD_BG,
        )
        self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()
