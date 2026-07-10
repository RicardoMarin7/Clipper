"""Wrapper sobre los binarios ffmpeg/ffprobe. Sin lógica de negocio.

Diseño (SDD Fase 3):
- Todo comando corre como subproceso con -progress pipe:1 -nostats: el avance
  llega como pares clave=valor por stdout, legibles por máquina.
- stderr se fusiona en stdout para evitar deadlocks de buffers; las líneas que
  no son de progreso se retienen como "tail" para diagnosticar errores.
- La cancelación es cooperativa: se consulta el Event entre líneas de progreso
  y se termina el subproceso de forma ordenada (terminate -> kill a los 3 s).
- Corte por defecto con stream copy (-c copy, pérdida cero); modo exacto
  opcional recodificando con NVENC si existe, si no libx264.
"""

from __future__ import annotations

import collections
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from utils.logger import get_logger

logger = get_logger(__name__)

CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Líneas clave=valor emitidas por -progress (ruido para el usuario)
_PROGRESS_LINE = re.compile(r"^[A-Za-z_][\w.:]*=")

_nvenc_available: bool | None = None


class FFmpegError(RuntimeError):
    """FFmpeg/ffprobe terminó con error; el mensaje incluye su salida."""


class JobCancelled(Exception):
    """El usuario canceló el trabajo; el subproceso fue terminado limpiamente."""


def find_binary(name: str) -> str | None:
    """Busca el binario en PATH o en bin/ junto a la app.

    En desarrollo, bin/ cuelga de la raíz del proyecto; empaquetado con
    PyInstaller (sys.frozen), cuelga de la carpeta del ejecutable.
    """
    found = shutil.which(name)
    if found:
        return found
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent
    local = root / "bin" / f"{name}.exe"
    return str(local) if local.is_file() else None


def check_binaries() -> tuple[bool, str]:
    missing = [name for name in ("ffmpeg", "ffprobe") if find_binary(name) is None]
    if missing:
        return False, (
            f"No se encontró {' ni '.join(missing)} en el PATH ni en la carpeta bin/ del "
            "proyecto. Instálalo (p. ej. `winget install Gyan.FFmpeg`) y reinicia la app."
        )
    return True, "ffmpeg y ffprobe encontrados"


def probe_duration(video: Path) -> float:
    """Duración del video en segundos, vía ffprobe."""
    ffprobe = find_binary("ffprobe")
    if not ffprobe:
        raise FFmpegError("ffprobe no encontrado")
    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True, text=True, creationflags=CREATION_FLAGS,
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffprobe falló al leer el video")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise FFmpegError(f"ffprobe no devolvió una duración válida: {result.stdout!r}") from exc


def has_nvenc() -> bool:
    """True si el ffmpeg disponible incluye el encoder h264_nvenc. Cacheado."""
    global _nvenc_available
    if _nvenc_available is None:
        ffmpeg = find_binary("ffmpeg")
        if not ffmpeg:
            _nvenc_available = False
        else:
            result = subprocess.run(
                [ffmpeg, "-hide_banner", "-encoders"],
                capture_output=True, text=True, creationflags=CREATION_FLAGS,
            )
            _nvenc_available = "h264_nvenc" in result.stdout
    return _nvenc_available


def extract_audio(
    video: Path,
    wav_out: Path,
    duration: float,
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> None:
    """Extrae el audio a WAV mono 16 kHz PCM (suficiente para detección RMS)."""
    ffmpeg = find_binary("ffmpeg")
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le",
        "-progress", "pipe:1", "-nostats",
        str(wav_out),
    ]
    _run(cmd, cancel_event=cancel_event, progress_cb=progress_cb, total_duration=duration)


def cut_clip(
    video: Path,
    start: float,
    end: float,
    out_path: Path,
    *,
    exact: bool = False,
    cancel_event: threading.Event | None = None,
) -> None:
    """Corta [start, end) del video.

    Por defecto stream copy: instantáneo y sin pérdida, con el corte inicial
    ajustado al keyframe anterior (el padding del detector lo compensa).
    Con exact=True recodifica (NVENC si hay GPU NVIDIA, si no libx264) para
    un corte exacto al frame, visualmente indistinguible del original.
    """
    ffmpeg = find_binary("ffmpeg")
    duration = max(0.1, end - start)
    if exact:
        if has_nvenc():
            codec = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"]
        else:
            codec = ["-c:v", "libx264", "-preset", "fast", "-crf", "18"]
        codec += ["-c:a", "aac", "-b:a", "192k"]
    else:
        codec = ["-c", "copy", "-avoid_negative_ts", "make_zero"]

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video),
        "-t", f"{duration:.3f}",
        *codec,
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]
    try:
        _run(cmd, cancel_event=cancel_event)
    except (JobCancelled, FFmpegError):
        out_path.unlink(missing_ok=True)  # nunca dejar clips a medias en la salida
        raise


def _run(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
    total_duration: float | None = None,
) -> None:
    """Ejecuta un comando ffmpeg leyendo su progreso línea a línea.

    Bloqueante — debe llamarse solo desde el hilo worker.
    """
    logger.info("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # fusionado: evita deadlock y captura warnings
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATION_FLAGS,
    )
    tail: collections.deque[str] = collections.deque(maxlen=40)
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            if cancel_event is not None and cancel_event.is_set():
                _terminate(proc)
                raise JobCancelled()
            line = raw.strip()
            if not line:
                continue
            if line.startswith("out_time_us="):
                if progress_cb and total_duration:
                    value = line.split("=", 1)[1]
                    if value.lstrip("-").isdigit():
                        seconds = max(0, int(value)) / 1_000_000
                        progress_cb(min(seconds / total_duration, 1.0))
            elif _PROGRESS_LINE.match(line):
                continue  # resto de claves de -progress (frame=, speed=, ...)
            else:
                tail.append(line)  # warnings/errores reales de ffmpeg
    finally:
        proc.stdout.close()
    code = proc.wait()
    if code != 0:
        detail = "\n".join(tail) or f"ffmpeg terminó con código {code}"
        logger.error("ffmpeg falló (código %s): %s", code, detail)
        raise FFmpegError(detail)


def _terminate(proc: subprocess.Popen) -> None:
    """Aborto ordenado: terminate, y kill si no muere en 3 segundos."""
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
