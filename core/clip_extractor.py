"""Exportación de segmentos detectados a archivos de clip individuales."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from core.models import HighlightSegment
from utils import ffmpeg_wrapper, file_manager
from utils.ffmpeg_wrapper import JobCancelled


def export_clips(
    video: Path,
    segments: list[HighlightSegment],
    output_dir: Path,
    *,
    exact: bool,
    cancel_event: threading.Event,
    on_clip_done: Callable[[int, int, Path], None],
    on_clip_progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Corta cada segmento a un .mp4 en output_dir. Devuelve las rutas creadas.

    on_clip_done(indice_1_based, total, ruta) se invoca tras cada clip para
    que el pipeline reporte progreso. Lanza JobCancelled si el usuario aborta;
    los clips ya exportados quedan (son íntegros), el parcial se borra en el
    wrapper.
    """
    exported: list[Path] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        if cancel_event.is_set():
            raise JobCancelled()
        out_path = output_dir / file_manager.clip_filename(index, segment.start)
        ffmpeg_wrapper.cut_clip(
            video, segment.start, segment.end, out_path,
            exact=exact, cancel_event=cancel_event, progress_cb=on_clip_progress,
        )
        exported.append(out_path)
        on_clip_done(index, total, out_path)
    return exported


def export_vertical_clips(
    video: Path,
    segments: list[HighlightSegment],
    vertical_dir: Path,
    *,
    style: str,
    cancel_event: threading.Event,
    on_clip_done: Callable[[int, int, Path], None],
    on_clip_progress: Callable[[float], None] | None = None,
) -> list[Path]:
    """Corta cada segmento directamente del video fuente a vertical 9:16.

    Una sola pasada por clip (corte + filtro + encode); no depende de que
    existan los clips horizontales.
    """
    vertical_dir.mkdir(parents=True, exist_ok=True)
    exported: list[Path] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        if cancel_event.is_set():
            raise JobCancelled()
        out_path = vertical_dir / file_manager.clip_filename(
            index, segment.start, suffix="_vertical"
        )
        ffmpeg_wrapper.cut_vertical_clip(
            video, segment.start, segment.end, out_path,
            style=style, cancel_event=cancel_event, progress_cb=on_clip_progress,
        )
        exported.append(out_path)
        on_clip_done(index, total, out_path)
    return exported
