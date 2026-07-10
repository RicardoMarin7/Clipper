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

from core import audio_analyzer, clip_extractor, highlight_detector, sound_matcher, video_analyzer
from core.models import (
    COMP_ALSO,
    COMP_ONLY,
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
                        f"{len(kill_times)} candidatos de kill por audio "
                        f"(umbral {config.kill_threshold:.2f})"
                    )
                    skull = video_analyzer.load_skull_template()
                    if skull is not None and len(kill_times):
                        # Verificación visual: mirar ~8 frames por candidato
                        # buscando la calavera de la UI de kill
                        self._log(
                            f"Verificando {len(kill_times)} candidatos en video "
                            f"(~{len(kill_times)} s)…"
                        )
                        self._stage(
                            f"Verificando kills en video (0/{len(kill_times)})",
                            EXTRACT_END,
                        )
                        span = ANALYZE_END - EXTRACT_END
                        confirmed = video_analyzer.verify_kill_events(
                            config.video_path, kill_times, kill_scores, skull,
                            cancel_event=self._cancel,
                            progress_cb=lambda i, n: self._emit(ProgressEvent(
                                EventKind.STAGE,
                                stage=f"Verificando kills en video ({i}/{n})",
                                percent=EXTRACT_END + span * i / n,
                            )),
                        )
                        kill_times = kill_times[confirmed]
                        kill_scores = kill_scores[confirmed]
                        self._log(
                            f"{len(kill_times)} kills confirmadas en video "
                            f"({int((~confirmed).sum())} candidatos descartados)",
                            "SUCCESS",
                        )
                    elif skull is None:
                        self._log(
                            "Sin plantilla de calavera (assets/kill_skull.npy): "
                            "kills solo por audio; sube el umbral a ~0.55",
                            "WARN",
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
            make_comp = config.compilation_mode in (COMP_ALSO, COMP_ONLY)
            ops_total = (
                len(segments) * (int(include_h) + int(include_v))
                + (int(include_h) + int(include_v)) * int(make_comp)
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

            is_hdr = False
            if include_v or config.exact_cut or (make_comp and config.transitions):
                encoder = "NVENC (GPU)" if ffmpeg_wrapper.has_nvenc() else "libx264 (CPU)"
                self._log(f"Recodificación con encoder: {encoder}")
                is_hdr = ffmpeg_wrapper.probe_is_hdr(config.video_path)
                if is_hdr:
                    self._log(
                        "Video HDR detectado: los clips recodificados se "
                        "convertirán a SDR (tonemapping) para máxima compatibilidad"
                    )

            exported_h: list[Path] = []
            exported_v: list[Path] = []
            files_created = 0

            if include_h:
                self._stage(f"Exportando clips (0/{len(segments)})", DETECT_END)
                exported_h = clip_extractor.export_clips(
                    config.video_path, segments, output_dir,
                    exact=config.exact_cut,
                    hdr=is_hdr,
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
                    hdr=is_hdr,
                    cancel_event=self._cancel,
                    on_clip_done=lambda i, t, p: (
                        bump(f"Exportando clips verticales ({i}/{t})"),
                        self._log(f"Clip vertical {i}/{t}: {p.name}", "SUCCESS"),
                    ),
                    on_clip_progress=op_progress,
                )
                files_created += len(exported_v)

            if make_comp:
                total_clip_seconds = sum(s.duration for s in segments)
                use_transitions = config.transitions and len(segments) > 1
                if use_transitions:
                    self._log(
                        "Compilatorio con transiciones: se recodifica "
                        "(crossfade de 0.35 s entre clips)"
                    )
                if exported_h:
                    self._check_cancel()
                    comp = output_dir / "highlights_compilation.mp4"
                    if use_transitions:
                        self._stage("Creando compilatorio con transiciones", None)
                        # los clips stream-copy conservan el HDR original;
                        # los de corte exacto ya salieron en SDR
                        ffmpeg_wrapper.concat_with_transitions(
                            exported_h, comp,
                            hdr=is_hdr and not config.exact_cut,
                            cancel_event=self._cancel, progress_cb=op_progress,
                        )
                    else:
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
                    if use_transitions:
                        self._stage("Creando compilatorio vertical con transiciones", None)
                        # los clips verticales ya son SDR
                        ffmpeg_wrapper.concat_with_transitions(
                            exported_v, comp_v, hdr=False,
                            cancel_event=self._cancel, progress_cb=op_progress,
                        )
                    else:
                        ffmpeg_wrapper.concat_clips(
                            exported_v, comp_v, cancel_event=self._cancel,
                            progress_cb=op_progress, total_duration=total_clip_seconds,
                        )
                    bump("Creando compilatorio vertical")
                    self._log(f"Compilatorio vertical: {comp_v.name}", "SUCCESS")
                    files_created += 1

            if config.compilation_mode == COMP_ONLY:
                # Los clips solo existieron para construir el compilatorio
                for clip in (*exported_h, *exported_v):
                    clip.unlink(missing_ok=True)
                files_created -= len(exported_h) + len(exported_v)
                self._log("Clips individuales eliminados (modo solo compilatorio)")

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
