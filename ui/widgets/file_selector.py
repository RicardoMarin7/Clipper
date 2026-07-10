"""Selector de archivo o carpeta: label + entry editable + botón Buscar."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from ui import theme

VIDEO_FILETYPES = [
    ("Videos", "*.mp4 *.mkv *.mov *.avi"),
    ("Todos los archivos", "*.*"),
]


class FileSelector(ctk.CTkFrame):
    def __init__(
        self,
        master,
        label_text: str,
        mode: str = "file",  # "file" | "directory"
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._mode = mode
        self._var = tk.StringVar()
        if on_change is not None:
            self._var.trace_add("write", lambda *_: on_change())

        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=label_text, width=150, anchor="w", font=theme.FONT_UI).grid(
            row=0, column=0, padx=(0, 8), sticky="w"
        )
        self._entry = ctk.CTkEntry(self, textvariable=self._var, font=theme.FONT_UI)
        self._entry.grid(row=0, column=1, sticky="ew")
        self._button = ctk.CTkButton(self, text="Buscar…", width=90, command=self._browse)
        self._button.grid(row=0, column=2, padx=(8, 0))

    def _browse(self) -> None:
        if self._mode == "file":
            path = filedialog.askopenfilename(
                title="Selecciona el video de la partida", filetypes=VIDEO_FILETYPES
            )
        else:
            path = filedialog.askdirectory(title="Selecciona la carpeta de salida")
        if path:
            self._var.set(path)

    def get_path(self) -> str:
        return self._var.get().strip()

    def set_path(self, path: str) -> None:
        self._var.set(path)

    def set_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self._entry.configure(state=state)
        self._button.configure(state=state)
