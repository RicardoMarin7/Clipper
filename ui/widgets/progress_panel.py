"""Panel de progreso: etapa actual + porcentaje + barra determinada.

La UI no calcula nada: solo pinta lo que el pipeline reporta vía eventos.
"""

from __future__ import annotations

import customtkinter as ctk

from core.models import ProgressEvent
from ui import theme


class ProgressPanel(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        self._stage_label = ctk.CTkLabel(self, text="—", anchor="w", font=theme.FONT_UI)
        self._stage_label.grid(row=0, column=0, sticky="w")
        self._percent_label = ctk.CTkLabel(self, text="0 %", anchor="e", font=theme.FONT_UI_BOLD)
        self._percent_label.grid(row=0, column=1, sticky="e")

        self._bar = ctk.CTkProgressBar(self, progress_color=theme.ACCENT)
        self._bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._bar.set(0)

    def update_from_event(self, event: ProgressEvent) -> None:
        if event.stage:
            self.set_stage(event.stage)
        if event.percent is not None:
            self.set_percent(event.percent)

    def set_stage(self, text: str) -> None:
        self._stage_label.configure(text=f"Etapa: {text}")

    def set_percent(self, percent: float) -> None:
        percent = min(100.0, max(0.0, percent))
        self._bar.set(percent / 100.0)
        self._percent_label.configure(text=f"{percent:.0f} %")

    def reset(self) -> None:
        self._stage_label.configure(text="—")
        self.set_percent(0.0)
