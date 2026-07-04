"""SlotPicker — the app-bar chip + dialog for choosing the target channel.

``target`` is None (write to the live TONE buffer only, no program change) or a
MIDI Program Change number for a real amp channel (see ``katana_channels``),
which the service turns into a MIDI program change. The available channels
depend on the configured amp model (100 W: A1-B4, 50 W: CH1-CH4).
"""

import flet as ft

from .. import katana_channels
from . import theme


class SlotPicker:
    def __init__(self, page: ft.Page, initial: int | None = None, model: str = "50") -> None:
        self._page = page
        self._bank_rows = katana_channels.channel_rows_for_model(model)  # [[(name, pc)…], …]
        self._name_by_pc = {pc: name for row in self._bank_rows for name, pc in row}
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
                "Target channel (click to change). 'TONE' = write to live buffer only, "
                "no channel switch."
            ),
            on_click=self._open,
        )

    def set_model(self, model: str) -> None:
        """Switch the amp model, rebuilding the channel map.

        Resets the target to TONE if the currently-selected channel no longer
        exists on the new model (e.g. A: CH-4 when switching 100 W → 50 W).
        """
        self._bank_rows = katana_channels.channel_rows_for_model(model)
        self._name_by_pc = {pc: name for row in self._bank_rows for name, pc in row}
        if self.target is not None and self.target not in self._name_by_pc:
            self.target = None
        self._label.value = self._display()
        self._page.update()

    def _display(self) -> str:
        if self.target is None:
            return "→ TONE"
        return f"→ {self._name_by_pc.get(self.target, f'PC{self.target}')}"

    def _open(self, e) -> None:
        def pick(pc: int | None) -> None:
            self.target = pc
            self._label.value = self._display()
            self._page.pop_dialog()
            self._page.update()

        tone_active = self.target is None
        tone_btn = ft.TextButton(
            "TONE only (no channel switch)",
            style=ft.ButtonStyle(
                color=theme.AMBER if tone_active else theme.TEXT_DIM,
                bgcolor=theme.AMBER_DARK if tone_active else "transparent",
                padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            ),
            on_click=lambda e: pick(None),
        )
        rows = [ft.Row([tone_btn], tight=True)]
        # One row per bank: bank A on the first row, bank B on the second.
        for bank in self._bank_rows:
            cells = []
            for name, pc in bank:
                active = pc == self.target
                cells.append(
                    ft.TextButton(
                        name,
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
            title=ft.Text("Select target channel", size=14, weight=ft.FontWeight.BOLD),
            content=ft.Column(rows, spacing=2, tight=True),
            bgcolor=theme.CARD_BG,
        )
        self._page.show_dialog(dlg)
