"""Consola de registro: textbox de solo lectura con colores por nivel,
autoscroll inteligente y botón de copiado."""

from __future__ import annotations

import time

import customtkinter as ctk

from ui import theme


class LogConsole(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="⑤ REGISTRO", font=theme.FONT_SECTION, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(
            header, text="Copiar registro", width=120, height=24,
            fg_color="transparent", border_width=1, command=self._copy_all,
        ).grid(row=0, column=1, sticky="e")

        self._box = ctk.CTkTextbox(self, font=theme.FONT_MONO, wrap="word", state="disabled")
        self._box.grid(row=1, column=0, sticky="nsew", pady=(theme.PAD_Y, 0))
        for level, color in theme.LOG_COLORS.items():
            self._box.tag_config(level, foreground=color)

    def append(self, message: str, level: str = "INFO") -> None:
        if level not in theme.LOG_COLORS:
            level = "INFO"
        # Autoscroll solo si el usuario ya estaba mirando el final
        follow = self._box.yview()[1] >= 0.999
        stamp = time.strftime("[%H:%M:%S] ")
        self._box.configure(state="normal")
        self._box.insert("end", stamp + message + "\n", level)
        self._box.configure(state="disabled")
        if follow:
            self._box.see("end")

    def _copy_all(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._box.get("1.0", "end-1c"))
