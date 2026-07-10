"""Orquestador del flujo completo: extraer audio -> analizar -> detectar -> cortar.

Se ejecuta íntegro en el hilo worker (SDD Fase 3). Toda comunicación con la
UI es unidireccional vía queue.Queue (ProgressEvent); la cancelación llega
por threading.Event. Este módulo no importa nada de /ui.
"""

from __future__ import annotations

import queue
import tempfile
import threading
from pathlib import Path

from core import audio_analyzer, clip_extractor, highlight_detector, sound_matcher
from core.models import (
    DETECT_BOTH,
    DETECT_INTENSITY,
    DETECT_KILLS,
    FORMAT_BOTH,
    FORMAT_HORIZONTAL,
    FORMAT_VERTICAL,
    EventKind,
    JobConfig,
    ProgressEvent,
)
from utils import ffmpeg_wrapper, file_manager
from utils.ffmpeg_wrapper import JobCancelled
from utils.logger import get_logger

logger = get_logger(__name__)

# Pesos de cada etapa sobre la barra global (SDD Fase 3, §3.5)
EXTRACT_END = 60.0
ANALYZE_END = 75.0
DETECT_END = 80.0


class HighlightPipeline:
    def __init__(self, event_queue: queue.Queue, cancel_event: threading.Event) -> None:
        self._queue = event_queue
        self._cancel = cancel_event

    # ------------------------------------------------------------------ run
    def run(self, config: JobConfig) -> None:
        """Punto de entrada del hilo worker. Nunca deja escapar excepciones."""
        wav_path: Path | None = None
        try:
            self._stage("Preparando", 0.0)
            duration = ffmpeg_wrapper.probe_duration(config.video_path)
            self._log(
                f"Video: {config.video_path.name} · duración "
                f"{file_manager.format_timestamp(duration)}"
            )
            output_dir = file_manager.ensure_output_dir(config.output_dir)
            self._check_cancel()

            # 1. Extraer audio (0 -> 60 %)
            self._stage("Extrayendo audio", 0.0)
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            handle.close()
            wav_path = Path(handle.name)
            ffmpeg_wrapper.extract_audio(
                config.video_path, wav_path, duration,
                cancel_event=self._cancel,
                progress_cb=lambda fraction: self._progress(fraction * EXTRACT_END),
            )
            size_mb = wav_path.stat().st_size / 1_048_576
            self._log(f"Audio extraído ({size_mb:.0f} MB)", "SUCCESS")

            # 2. Analizar audio (60 -> 75 %): kills por template matching
            #    y/o picos de intensidad RMS, según el modo elegido
            self._stage("Analizando audio", EXTRACT_END)
            mode = config.detection_mode
            sources: list[list] = []

            if mode in (DETECT_KILLS, DETECT_BOTH):
                templates = sound_matcher.load_templates(file_manager.kill_sounds_dir())
                if not templates:
                    self._log(
                        "Sin plantillas en assets/kill_sounds; se usará solo intensidad",
                        "WARN",
                    )
                    mode = DETECT_INTENSITY
                else:
                    kill_times, kill_scores = sound_matcher.find_kills(
                        wav_path, templates, threshold=config.kill_threshold
                    )
                    self._log(
                        f"{len(kill_times)} kills detectadas por sonido "
                        f"(umbral {config.kill_threshold:.2f})"
                    )
                    sources.append(highlight_detector.build_segments(
                        kill_times, kill_scores,
                        pre_padding=config.pre_padding,
                        post_padding=config.post_padding,
                        duration=duration,
                        reason="kill",
                    ))
            self._check_cancel()

            if mode in (DETECT_INTENSITY, DETECT_BOTH):
                timestamps, rms = audio_analyzer.compute_rms(wav_path)
                intensity_segments = highlight_detector.detect(
                    timestamps, rms,
                    sensitivity=config.sensitivity,
                    pre_padding=config.pre_padding,
                    post_padding=config.post_padding,
                    duration=duration,
                )
                self._log(
                    f"{len(intensity_segments)} momentos intensos detectados "
                    f"(sensibilidad {config.sensitivity}/10)"
                )
                sources.append(intensity_segments)
            self._progress(ANALYZE_END)
            self._check_cancel()

            # 3. Fusionar señales (75 -> 80 %)
            self._stage("Detectando highlights", ANALYZE_END)
            segments = highlight_detector.combine(*sources)
            self._progress(DETECT_END)
            self._log(f"{len(segments)} highlights tras fusionar solapes")
            if not segments:
                self._log(
                    "Ningún pico superó el umbral. Prueba con una sensibilidad mayor.",
                    "WARN",
                )
                self._done(0, output_dir)
                return

            # 4. Exportar (80 -> 100 %): clips por formato + compilatorios.
            #    El progreso se reparte entre el total de operaciones ffmpeg.
            include_h = config.output_format in (FORMAT_HORIZONTAL, FORMAT_BOTH)
            include_v = config.output_format in (FORMAT_VERTICAL, FORMAT_BOTH)
            ops_total = (
                len(segments) * (int(include_h) + int(include_v))
                + (int(include_h) + int(include_v)) * int(config.make_compilation)
            )
            ops_done = 0

            def bump(stage: str) -> None:
                nonlocal ops_done
                ops_done += 1
                percent = DETECT_END + (100.0 - DETECT_END) * ops_done / ops_total
                self._emit(ProgressEvent(EventKind.STAGE, stage=stage, percent=percent))

            def op_progress(fraction: float) -> None:
                # avance DENTRO de la operación en curso: la barra nunca se congela
                percent = (
                    DETECT_END
                    + (100.0 - DETECT_END) * (ops_done + min(1.0, fraction)) / ops_total
                )
                self._progress(percent)

            if include_v or config.exact_cut:
                encoder = "NVENC (GPU)" if ffmpeg_wrapper.has_nvenc() else "libx264 (CPU)"
                self._log(f"Recodificación con encoder: {encoder}")

            exported_h: list[Path] = []
            exported_v: list[Path] = []
            files_created = 0

            if include_h:
                self._stage(f"Exportando clips (0/{len(segments)})", DETECT_END)
                exported_h = clip_extractor.export_clips(
                    config.video_path, segments, output_dir,
                    exact=config.exact_cut,
                    cancel_event=self._cancel,
                    on_clip_done=lambda i, t, p: (
                        bump(f"Exportando clips ({i}/{t})"),
                        self._log(f"Clip {i}/{t}: {p.name}", "SUCCESS"),
                    ),
                    on_clip_progress=op_progress,
                )
                files_created += len(exported_h)

            if include_v:
                vertical_dir = output_dir / "vertical"
                self._stage(f"Exportando clips verticales (0/{len(segments)})", None)
                exported_v = clip_extractor.export_vertical_clips(
                    config.video_path, segments, vertical_dir,
                    style=config.vertical_style,
                    cancel_event=self._cancel,
                    on_clip_done=lambda i, t, p: (
                        bump(f"Exportando clips verticales ({i}/{t})"),
                        self._log(f"Clip vertical {i}/{t}: {p.name}", "SUCCESS"),
                    ),
                    on_clip_progress=op_progress,
                )
                files_created += len(exported_v)

            if config.make_compilation:
                total_clip_seconds = sum(s.duration for s in segments)
                if exported_h:
                    self._check_cancel()
                    comp = output_dir / "highlights_compilation.mp4"
                    ffmpeg_wrapper.concat_clips(
                        exported_h, comp, cancel_event=self._cancel,
                        progress_cb=op_progress, total_duration=total_clip_seconds,
                    )
                    bump("Creando video compilatorio")
                    self._log(f"Compilatorio: {comp.name}", "SUCCESS")
                    files_created += 1
                if exported_v:
                    self._check_cancel()
                    comp_v = output_dir / "vertical" / "highlights_compilation_vertical.mp4"
                    ffmpeg_wrapper.concat_clips(
                        exported_v, comp_v, cancel_event=self._cancel,
                        progress_cb=op_progress, total_duration=total_clip_seconds,
                    )
                    bump("Creando compilatorio vertical")
                    self._log(f"Compilatorio vertical: {comp_v.name}", "SUCCESS")
                    files_created += 1

            self._done(files_created, output_dir)

        except JobCancelled:
            self._emit(ProgressEvent(
                EventKind.CANCELLED,
                message="Proceso cancelado. La carpeta de salida solo contiene clips íntegros.",
                level="WARN",
            ))
        except Exception as exc:  # frontera de errores del worker (SDD §3.7)
            logger.exception("Fallo en el pipeline")
            self._emit(ProgressEvent(EventKind.ERROR, message=str(exc), level="ERROR"))
        finally:
            if wav_path is not None:
                wav_path.unlink(missing_ok=True)

    # -------------------------------------------------------------- helpers
    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def _emit(self, event: ProgressEvent) -> None:
        self._queue.put(event)

    def _stage(self, stage: str, percent: float) -> None:
        self._emit(ProgressEvent(EventKind.STAGE, stage=stage, percent=percent))

    def _progress(self, percent: float) -> None:
        self._emit(ProgressEvent(EventKind.PROGRESS, percent=percent))

    def _log(self, message: str, level: str = "INFO") -> None:
        self._emit(ProgressEvent(EventKind.LOG, message=message, level=level))

    def _done(self, clips: int, output_dir: Path) -> None:
        self._emit(ProgressEvent(
            EventKind.DONE,
            percent=100.0,
            message=f"Listo: {clips} archivos exportados en {output_dir}",
            level="SUCCESS",
            payload={"clips": clips, "out_dir": str(output_dir)},
        ))
