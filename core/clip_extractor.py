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
            exact=exact, cancel_event=cancel_event,
        )
        exported.append(out_path)
        on_clip_done(index, total, out_path)
    return exported
