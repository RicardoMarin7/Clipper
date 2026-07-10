"""Ventana principal de Clipper.

Implementa la máquina de estados de la SDD Fase 2 (§2.4) y el patrón de
concurrencia de la Fase 3: el pipeline corre en un hilo worker y la UI drena
la queue de eventos cada 100 ms con .after(). Ningún widget se toca desde
otro hilo.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from enum import Enum, auto
from pathlib import Path

import customtkinter as ctk

from core.models import (
    DETECT_BOTH,
    DETECT_INTENSITY,
    DETECT_KILLS,
    EventKind,
    JobConfig,
    ProgressEvent,
)
from core.pipeline import HighlightPipeline
from ui import theme
from ui.widgets.file_selector import FileSelector
from ui.widgets.log_console import LogConsole
from ui.widgets.progress_panel import ProgressPanel
from utils import config as config_store
from utils import ffmpeg_wrapper, file_manager
from utils.logger import get_logger

logger = get_logger(__name__)

POLL_MS = 100  # 10 Hz: fluido al ojo, costo despreciable (SDD §3.3)

# Etiquetas visibles del selector de modo <-> constantes del dominio
MODE_LABELS = {
    "Solo kills": DETECT_KILLS,
    "Kills + intensidad": DETECT_BOTH,
    "Solo intensidad": DETECT_INTENSITY,
}
MODE_BY_VALUE = {value: label for label, value in MODE_LABELS.items()}


class UIState(Enum):
    IDLE = auto()
    RUNNING = auto()
    CANCELLING = auto()
    DONE = auto()
    ERROR = auto()


class ClipperApp(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode(theme.APPEARANCE)
        ctk.set_default_color_theme(theme.COLOR_THEME)
        super().__init__()

        self.title("Clipper — Battlefield 6 Highlight Extractor")
        self.geometry("1024x1024")
        self.minsize(1024, 1024)

        self._event_queue: queue.Queue[ProgressEvent] = queue.Queue()
        self._cancel_event: threading.Event | None = None
        self._worker: threading.Thread | None = None
        self._state = UIState.IDLE
        self._last_output_dir: str | None = None
        self._ffmpeg_ok = False

        self._build_layout()
        self._restore_settings()
        self._check_ffmpeg()
        self._set_state(UIState.IDLE)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # El polling vive SIEMPRE, no solo durante RUNNING (SDD §3.3)
        self.after(POLL_MS, self._poll_queue)

    # ------------------------------------------------------------- layout
    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)  # solo el registro absorbe el resize

        ctk.CTkLabel(
            self, text="🎬 CLIPPER — Battlefield 6 Highlight Extractor",
            font=theme.FONT_TITLE,
        ).grid(row=0, column=0, padx=theme.PAD_X, pady=(theme.PAD_X, theme.PAD_Y), sticky="w")

        # ① ARCHIVOS -------------------------------------------------------
        files = ctk.CTkFrame(self)
        files.grid(row=1, column=0, padx=theme.PAD_X, pady=theme.PAD_Y, sticky="ew")
        files.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(files, text="① ARCHIVOS", font=theme.FONT_SECTION, anchor="w").grid(
            row=0, column=0, padx=theme.PAD_X, pady=(theme.PAD_Y, 0), sticky="w"
        )
        self._video_selector = FileSelector(
            files, "Video de entrada", mode="file", on_change=self._on_video_change
        )
        self._video_selector.grid(row=1, column=0, padx=theme.PAD_X, pady=4, sticky="ew")
        self._output_selector = FileSelector(
            files, "Carpeta de salida", mode="directory", on_change=self._refresh_start_button
        )
        self._output_selector.grid(
            row=2, column=0, padx=theme.PAD_X, pady=(4, theme.PAD_Y), sticky="ew"
        )

        # ② DETECCIÓN ------------------------------------------------------
        detection = ctk.CTkFrame(self)
        detection.grid(row=2, column=0, padx=theme.PAD_X, pady=theme.PAD_Y, sticky="ew")
        detection.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(detection, text="② DETECCIÓN", font=theme.FONT_SECTION, anchor="w").grid(
            row=0, column=0, columnspan=4, padx=theme.PAD_X, pady=(theme.PAD_Y, 0), sticky="w"
        )

        ctk.CTkLabel(
            detection, text="Modo de detección", width=150, anchor="w", font=theme.FONT_UI
        ).grid(row=1, column=0, padx=(theme.PAD_X, 8), pady=4, sticky="w")
        self._mode_selector = ctk.CTkSegmentedButton(
            detection, values=list(MODE_LABELS), font=theme.FONT_UI,
            command=lambda _v: self._update_sensitivity_hint(),
        )
        self._mode_selector.set(MODE_BY_VALUE[DETECT_BOTH])
        self._mode_selector.grid(row=1, column=1, columnspan=3, pady=4, sticky="w")

        ctk.CTkLabel(
            detection, text="Sensibilidad de audio", width=150, anchor="w", font=theme.FONT_UI
        ).grid(row=2, column=0, padx=(theme.PAD_X, 8), pady=4, sticky="w")
        self._sensitivity = tk.IntVar(value=5)
        self._slider = ctk.CTkSlider(
            detection, from_=1, to=10, number_of_steps=9,
            variable=self._sensitivity, command=lambda _v: self._update_sensitivity_label(),
        )
        self._slider.grid(row=2, column=1, columnspan=2, pady=4, sticky="ew")
        self._sensitivity_label = ctk.CTkLabel(detection, text="5", width=30,
                                               font=theme.FONT_UI_BOLD)
        self._sensitivity_label.grid(row=2, column=3, padx=(8, theme.PAD_X), pady=4)
        self._sensitivity_hint = ctk.CTkLabel(
            detection,
            text="Alta = más clips (más falsos positivos) · Baja = solo los momentos más intensos",
            font=("Segoe UI", 11), text_color="gray60", anchor="w",
        )
        self._sensitivity_hint.grid(row=3, column=1, columnspan=3, sticky="w")

        ctk.CTkLabel(
            detection, text="Umbral de kills", width=150, anchor="w", font=theme.FONT_UI
        ).grid(row=4, column=0, padx=(theme.PAD_X, 8), pady=4, sticky="w")
        self._kill_threshold = tk.DoubleVar(value=0.50)
        self._threshold_slider = ctk.CTkSlider(
            detection, from_=0.30, to=0.80, number_of_steps=10,
            variable=self._kill_threshold,
            command=lambda _v: self._update_threshold_label(),
        )
        self._threshold_slider.grid(row=4, column=1, columnspan=2, pady=4, sticky="ew")
        self._threshold_label = ctk.CTkLabel(detection, text="0.50", width=40,
                                             font=theme.FONT_UI_BOLD)
        self._threshold_label.grid(row=4, column=3, padx=(8, theme.PAD_X), pady=4)
        ctk.CTkLabel(
            detection,
            text="Bájalo si faltan kills · súbelo si aparecen clips falsos",
            font=("Segoe UI", 11), text_color="gray60", anchor="w",
        ).grid(row=5, column=1, columnspan=3, sticky="w")

        ctk.CTkLabel(
            detection, text="Padding pre/post (seg)", width=150, anchor="w", font=theme.FONT_UI
        ).grid(row=6, column=0, padx=(theme.PAD_X, 8), pady=4, sticky="w")
        padding_row = ctk.CTkFrame(detection, fg_color="transparent")
        padding_row.grid(row=6, column=1, columnspan=3, pady=4, sticky="w")
        self._pre_padding = tk.StringVar(value="3")
        self._post_padding = tk.StringVar(value="5")
        self._pre_entry = ctk.CTkEntry(padding_row, width=52, justify="center",
                                       textvariable=self._pre_padding)
        self._pre_entry.pack(side="left")
        ctk.CTkLabel(padding_row, text=" / ", font=theme.FONT_UI).pack(side="left")
        self._post_entry = ctk.CTkEntry(padding_row, width=52, justify="center",
                                        textvariable=self._post_padding)
        self._post_entry.pack(side="left")

        self._exact_cut = tk.BooleanVar(value=False)
        self._exact_checkbox = ctk.CTkCheckBox(
            detection, text="Corte exacto (recodifica con GPU, más lento)",
            variable=self._exact_cut, font=theme.FONT_UI,
        )
        self._exact_checkbox.grid(
            row=7, column=0, columnspan=4, padx=theme.PAD_X, pady=(4, theme.PAD_Y), sticky="w"
        )

        # ③ EJECUCIÓN ------------------------------------------------------
        execution = ctk.CTkFrame(self)
        execution.grid(row=3, column=0, padx=theme.PAD_X, pady=theme.PAD_Y, sticky="ew")
        execution.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(execution, text="③ EJECUCIÓN", font=theme.FONT_SECTION, anchor="w").grid(
            row=0, column=0, padx=theme.PAD_X, pady=(theme.PAD_Y, 0), sticky="w"
        )
        buttons = ctk.CTkFrame(execution, fg_color="transparent")
        buttons.grid(row=1, column=0, padx=theme.PAD_X, pady=4, sticky="ew")
        self._start_button = ctk.CTkButton(
            buttons, text="▶  Extraer Highlights", height=36,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            font=theme.FONT_UI_BOLD, command=self._start_job,
        )
        self._start_button.pack(side="left")
        self._cancel_button = ctk.CTkButton(
            buttons, text="✕  Cancelar", height=36, width=120,
            fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER,
            font=theme.FONT_UI, command=self._cancel_job,
        )
        self._cancel_button.pack(side="left", padx=(12, 0))
        self._open_button = ctk.CTkButton(
            buttons, text="📂  Abrir carpeta", height=36, width=140,
            fg_color="transparent", border_width=1,
            font=theme.FONT_UI, command=self._open_output_dir,
        )
        self._open_button.pack(side="left", padx=(12, 0))

        self._progress_panel = ProgressPanel(execution)
        self._progress_panel.grid(
            row=2, column=0, padx=theme.PAD_X, pady=(4, theme.PAD_Y), sticky="ew"
        )

        # ④ REGISTRO -------------------------------------------------------
        self._log_console = LogConsole(self)
        self._log_console.grid(row=4, column=0, padx=theme.PAD_X, pady=theme.PAD_Y, sticky="nsew")

        # Status bar --------------------------------------------------------
        self._status = ctk.CTkLabel(self, text="Listo", anchor="w", font=theme.FONT_UI)
        self._status.grid(row=5, column=0, padx=theme.PAD_X, pady=(0, theme.PAD_Y), sticky="ew")

    # -------------------------------------------------- máquina de estados
    def _set_state(self, state: UIState) -> None:
        """Único punto que habilita/deshabilita controles (SDD §2.4)."""
        self._state = state
        form_enabled = state in (UIState.IDLE, UIState.DONE, UIState.ERROR)

        self._video_selector.set_enabled(form_enabled)
        self._output_selector.set_enabled(form_enabled)
        widget_state = "normal" if form_enabled else "disabled"
        self._mode_selector.configure(state=widget_state)
        self._slider.configure(state=widget_state)
        self._threshold_slider.configure(state=widget_state)
        self._pre_entry.configure(state=widget_state)
        self._post_entry.configure(state=widget_state)
        self._exact_checkbox.configure(state=widget_state)

        self._start_button.configure(
            text="▶  Reintentar" if state is UIState.ERROR else "▶  Extraer Highlights"
        )
        self._refresh_start_button()

        if state is UIState.RUNNING:
            self._cancel_button.configure(state="normal", text="✕  Cancelar")
            self._cancel_button.pack(side="left", padx=(12, 0))
        elif state is UIState.CANCELLING:
            self._cancel_button.configure(state="disabled", text="Cancelando…")
        else:
            self._cancel_button.pack_forget()

        if state is UIState.DONE and self._last_output_dir:
            self._open_button.pack(side="left", padx=(12, 0))
        else:
            self._open_button.pack_forget()

    def _refresh_start_button(self) -> None:
        can_start = (
            self._state in (UIState.IDLE, UIState.DONE, UIState.ERROR)
            and self._ffmpeg_ok
            and file_manager.is_valid_video(self._video_selector.get_path())
            and bool(self._output_selector.get_path())
        )
        self._start_button.configure(state="normal" if can_start else "disabled")

    # ----------------------------------------------------- flujo del job
    def _start_job(self) -> None:
        paddings = self._parse_paddings()
        if paddings is None:
            return
        pre, post = paddings

        config = JobConfig(
            video_path=Path(self._video_selector.get_path()),
            output_dir=Path(self._output_selector.get_path()),
            sensitivity=int(self._sensitivity.get()),
            pre_padding=float(pre),
            post_padding=float(post),
            exact_cut=bool(self._exact_cut.get()),
            detection_mode=MODE_LABELS.get(self._mode_selector.get(), DETECT_BOTH),
            kill_threshold=round(float(self._kill_threshold.get()), 2),
        )

        self._progress_panel.reset()
        self._status.configure(text="Procesando…")
        self._log_console.append(f"Iniciando trabajo: {config.video_path.name}")
        self._set_state(UIState.RUNNING)

        self._cancel_event = threading.Event()
        pipeline = HighlightPipeline(self._event_queue, self._cancel_event)
        self._worker = threading.Thread(
            target=pipeline.run, args=(config,), daemon=True, name="clipper-worker"
        )
        self._worker.start()

    def _cancel_job(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()  # cooperativo: el worker aborta en la próxima frontera
        self._set_state(UIState.CANCELLING)
        self._status.configure(text="Cancelando…")

    def _parse_paddings(self) -> tuple[int, int] | None:
        try:
            pre = int(self._pre_padding.get())
            post = int(self._post_padding.get())
            if not (0 <= pre <= 30 and 0 <= post <= 30):
                raise ValueError
            return pre, post
        except ValueError:
            self._log_console.append(
                "Padding inválido: usa enteros entre 0 y 30 segundos.", "ERROR"
            )
            return None

    # ---------------------------------------------- consumo de la queue
    def _poll_queue(self) -> None:
        """Drena TODOS los eventos acumulados; coalesce los PROGRESS (SDD §3.3)."""
        pending_progress: ProgressEvent | None = None
        try:
            while True:
                event = self._event_queue.get_nowait()
                if event.kind is EventKind.PROGRESS:
                    pending_progress = event  # solo el último del lote pinta la barra
                else:
                    self._dispatch(event)
        except queue.Empty:
            pass
        finally:
            if pending_progress is not None:
                self._progress_panel.update_from_event(pending_progress)
            self.after(POLL_MS, self._poll_queue)  # re-agendar SIEMPRE

    def _dispatch(self, event: ProgressEvent) -> None:
        if event.kind is EventKind.LOG:
            self._log_console.append(event.message, event.level)
        elif event.kind is EventKind.STAGE:
            self._progress_panel.update_from_event(event)
        elif event.kind is EventKind.DONE:
            clips = event.payload.get("clips", 0)
            self._last_output_dir = event.payload.get("out_dir")
            self._log_console.append(event.message, event.level)
            self._status.configure(text=f"Listo · {clips} clips exportados")
            self._progress_panel.set_percent(100.0)
            self._finish_worker(UIState.DONE)
        elif event.kind is EventKind.ERROR:
            self._log_console.append(f"ERROR: {event.message}", "ERROR")
            self._status.configure(text="Error — revisa el registro")
            self._finish_worker(UIState.ERROR)
        elif event.kind is EventKind.CANCELLED:
            self._log_console.append(event.message, event.level)
            self._status.configure(text="Cancelado")
            self._progress_panel.reset()
            self._finish_worker(UIState.IDLE)

    def _finish_worker(self, next_state: UIState) -> None:
        if self._worker is not None:
            self._worker.join(timeout=2)  # ya emitió su evento terminal: muere enseguida
            self._worker = None
        self._cancel_event = None
        self._set_state(next_state)

    # ------------------------------------------------------------ varios
    def _on_video_change(self) -> None:
        video = self._video_selector.get_path()
        if file_manager.is_valid_video(video) and not self._output_selector.get_path():
            self._output_selector.set_path(str(file_manager.default_output_dir(video)))
        self._refresh_start_button()

    def _update_sensitivity_label(self) -> None:
        self._sensitivity_label.configure(text=str(int(self._sensitivity.get())))

    def _update_threshold_label(self) -> None:
        self._threshold_label.configure(text=f"{self._kill_threshold.get():.2f}")

    def _update_sensitivity_hint(self) -> None:
        mode = MODE_LABELS.get(self._mode_selector.get(), DETECT_BOTH)
        if mode == DETECT_KILLS:
            text = "En modo Solo kills la sensibilidad no aplica: cada kill detectada es un clip"
        else:
            text = ("Alta = más clips (más falsos positivos) · "
                    "Baja = solo los momentos más intensos")
        self._sensitivity_hint.configure(text=text)

    def _open_output_dir(self) -> None:
        if self._last_output_dir and Path(self._last_output_dir).is_dir():
            os.startfile(self._last_output_dir)  # noqa: S606 — app local de escritorio

    def _check_ffmpeg(self) -> None:
        self._ffmpeg_ok, message = ffmpeg_wrapper.check_binaries()
        if self._ffmpeg_ok:
            self._log_console.append(message)
            if ffmpeg_wrapper.has_nvenc():
                self._log_console.append("Encoder NVENC disponible para el modo de corte exacto")
        else:
            self._log_console.append(message, "ERROR")
            self._status.configure(text="FFmpeg no encontrado")
        templates = list(file_manager.kill_sounds_dir().glob("*.wav"))
        if templates:
            self._log_console.append(
                f"{len(templates)} plantillas de sonido de kill cargadas"
            )
        else:
            self._log_console.append(
                "Sin plantillas en assets/kill_sounds: los modos con kills usarán solo intensidad",
                "WARN",
            )

    def _restore_settings(self) -> None:
        settings = config_store.load_settings()
        self._video_selector.set_path(settings["video_path"])
        self._output_selector.set_path(settings["output_dir"])
        self._sensitivity.set(int(settings["sensitivity"]))
        self._update_sensitivity_label()
        self._pre_padding.set(str(settings["pre_padding"]))
        self._post_padding.set(str(settings["post_padding"]))
        self._exact_cut.set(bool(settings["exact_cut"]))
        self._mode_selector.set(
            MODE_BY_VALUE.get(settings["detection_mode"], MODE_BY_VALUE[DETECT_BOTH])
        )
        self._kill_threshold.set(
            min(0.80, max(0.30, float(settings["kill_threshold"])))
        )
        self._update_threshold_label()
        self._update_sensitivity_hint()

    def _save_settings(self) -> None:
        paddings = self._parse_paddings() or (3, 5)
        config_store.save_settings({
            "video_path": self._video_selector.get_path(),
            "output_dir": self._output_selector.get_path(),
            "sensitivity": int(self._sensitivity.get()),
            "pre_padding": paddings[0],
            "post_padding": paddings[1],
            "exact_cut": bool(self._exact_cut.get()),
            "detection_mode": MODE_LABELS.get(self._mode_selector.get(), DETECT_BOTH),
            "kill_threshold": round(float(self._kill_threshold.get()), 2),
        })

    def _on_close(self) -> None:
        if self._state in (UIState.RUNNING, UIState.CANCELLING):
            # Aborto limpio antes de salir: no dejar ffmpeg huérfano ni clips a medias
            if self._cancel_event is not None:
                self._cancel_event.set()
            if self._worker is not None:
                self._worker.join(timeout=5)
        self._save_settings()
        self.destroy()
