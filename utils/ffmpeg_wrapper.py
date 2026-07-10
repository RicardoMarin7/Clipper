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
import tempfile
import threading
from pathlib import Path
from typing import Callable

from utils.logger import get_logger

logger = get_logger(__name__)

CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# Líneas clave=valor emitidas por -progress (ruido para el usuario)
_PROGRESS_LINE = re.compile(r"^[A-Za-z_][\w.:]*=")

_nvenc_available: bool | None = None
_sdr_filter_cache: str | None = None


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


def probe_is_hdr(video: Path) -> bool:
    """True si el video es HDR (PQ/HLG). Las grabaciones HDR de ShadowPlay
    recodificadas sin tonemapping producen H.264 10-bit que Windows no
    reproduce (0x80004005) y colores lavados en pantallas SDR."""
    ffprobe = find_binary("ffprobe")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True, text=True, creationflags=CREATION_FLAGS,
    )
    return result.stdout.strip() in {"smpte2084", "arib-std-b67"}


def sdr_prep_filter(hdr: bool) -> str:
    """Filtro que normaliza el video a SDR 8-bit reproducible en cualquier sitio.

    Para HDR: tonemapping por GPU (libplacebo) o CPU (zscale); si el build de
    ffmpeg no trae ninguno, al menos se fuerza 8 bits (reproducible, aunque
    con colores apagados). Para SDR: solo asegura yuv420p.
    """
    if not hdr:
        return "format=yuv420p"
    global _sdr_filter_cache
    if _sdr_filter_cache is None:
        filters = _available_filters()
        if "libplacebo" in filters:
            _sdr_filter_cache = (
                "libplacebo=tonemapping=hable:colorspace=bt709:"
                "color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p"
            )
        elif "zscale" in filters and "tonemap" in filters:
            _sdr_filter_cache = (
                "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
            )
        else:
            logger.warning("ffmpeg sin libplacebo/zscale: HDR sin tonemapping")
            _sdr_filter_cache = "format=yuv420p"
    return _sdr_filter_cache


def _available_filters() -> set[str]:
    ffmpeg = find_binary("ffmpeg")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True, text=True, creationflags=CREATION_FLAGS,
    )
    names = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # formato: " T.C  nombre  V->V  descripción"
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names


def has_nvenc() -> bool:
    """True si h264_nvenc puede codificar de verdad en esta máquina. Cacheado.

    Prueba funcional (codificar un frame negro a null): que el encoder esté
    en la lista no garantiza que haya GPU NVIDIA con drivers.
    """
    global _nvenc_available
    if _nvenc_available is None:
        ffmpeg = find_binary("ffmpeg")
        if not ffmpeg:
            _nvenc_available = False
        else:
            result = subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                capture_output=True, text=True, creationflags=CREATION_FLAGS,
            )
            _nvenc_available = result.returncode == 0
            logger.info("NVENC disponible: %s", _nvenc_available)
    return _nvenc_available


def encode_args() -> list[str]:
    """Argumentos de recodificación de alta calidad: NVENC si hay GPU, si no x264.

    En x264 se usa veryfast: a crf 18 la diferencia visual con fast es
    imperceptible y codifica ~2x más rápido (importante sin GPU).
    """
    if has_nvenc():
        video = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19"]
    else:
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
    return [*video, "-c:a", "aac", "-b:a", "192k"]


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
    hdr: bool = False,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
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
        # al recodificar, normalizar a SDR 8-bit (Windows no reproduce
        # H.264 10-bit y el HDR sin tonemapping sale con colores lavados)
        codec = ["-vf", sdr_prep_filter(hdr), *encode_args()]
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
        _run(cmd, cancel_event=cancel_event, progress_cb=progress_cb, total_duration=duration)
    except (JobCancelled, FFmpegError):
        out_path.unlink(missing_ok=True)  # nunca dejar clips a medias en la salida
        raise


VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920

# Crossfade entre clips del compilatorio: corto para que el cambio sea
# suave sin sentirse un "efecto"
TRANSITION_SECONDS = 0.35


def vertical_filter(style: str, prep: str = "format=yuv420p") -> str:
    """Cadena de filtros 16:9 -> 9:16 (1080x1920, válido para TikTok/Reels).

    - "crop": franja central escalada a pantalla completa (máximo tamaño,
      máxima pérdida lateral).
    - "zoom": recorte 3:4 escalado a 1080x1440 (75% de la altura) sobre
      bandas difuminadas — la acción grande sin recortar tanto.
    - "blur" (default): video completo centrado sobre su propia copia
      ampliada y difuminada rellenando arriba/abajo.

    prep normaliza el origen antes de componer (tonemapping HDR->SDR y/o
    8 bits) — ver sdr_prep_filter().
    """
    if style == "crop":
        return f"{prep},crop=ih*9/16:ih,scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}"
    # Estilos con fondo: el fondo se difumina a resolución mínima (270x480)
    # y luego se amplía — mismo resultado visual que difuminar a 1080x1920
    # pero ~10x más barato.
    if style == "zoom":
        foreground = f"crop=ih*3/4:ih,scale={VERTICAL_WIDTH}:{VERTICAL_WIDTH * 4 // 3}"
    else:  # blur
        foreground = f"scale={VERTICAL_WIDTH}:-2"
    return (
        f"[0:v]{prep},split=2[srca][srcb];"
        "[srca]scale=270:480:force_original_aspect_ratio=increase,"
        "crop=270:480,boxblur=10,"
        f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},setsar=1[bg];"
        f"[srcb]{foreground}[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )


def cut_vertical_clip(
    video: Path,
    start: float,
    end: float,
    out_path: Path,
    *,
    style: str = "blur",
    hdr: bool = False,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> None:
    """Corta [start, end) y lo convierte a vertical 9:16 en una sola pasada.

    Siempre recodifica (NVENC/x264): el filtrado lo exige, y de regalo el
    corte es exacto al frame.
    """
    ffmpeg = find_binary("ffmpeg")
    duration = max(0.1, end - start)
    filt = vertical_filter(style, prep=sdr_prep_filter(hdr))
    filter_args = ["-vf", filt] if style == "crop" else ["-filter_complex", filt]
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video),
        "-t", f"{duration:.3f}",
        *filter_args,
        *encode_args(),
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]
    try:
        _run(cmd, cancel_event=cancel_event, progress_cb=progress_cb, total_duration=duration)
    except (JobCancelled, FFmpegError):
        out_path.unlink(missing_ok=True)
        raise


def build_concat_list(clips: list[Path]) -> str:
    """Contenido del archivo de lista para el concat demuxer de ffmpeg."""
    lines = []
    for clip in clips:
        path = Path(clip).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{path}'")
    return "\n".join(lines) + "\n"


def concat_clips(
    clips: list[Path],
    out_path: Path,
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
    total_duration: float | None = None,
) -> None:
    """Une clips (mismo codec/resolución) en un solo video sin recodificar."""
    ffmpeg = find_binary("ffmpeg")
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    try:
        handle.write(build_concat_list(clips))
        handle.close()
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "concat", "-safe", "0",
            "-i", handle.name,
            "-c", "copy",
            "-progress", "pipe:1", "-nostats",
            str(out_path),
        ]
        try:
            _run(cmd, cancel_event=cancel_event,
                 progress_cb=progress_cb, total_duration=total_duration)
        except (JobCancelled, FFmpegError):
            out_path.unlink(missing_ok=True)
            raise
    finally:
        Path(handle.name).unlink(missing_ok=True)


def probe_fps(video: Path) -> float:
    ffprobe = find_binary("ffprobe")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True, text=True, creationflags=CREATION_FLAGS,
    )
    try:
        num, _, den = result.stdout.strip().partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 60.0


def build_transition_graph(
    durations: list[float],
    *,
    prep: str,
    fps: float,
    transition_seconds: float = TRANSITION_SECONDS,
) -> tuple[str, str, str, float]:
    """Grafo filter_complex que encadena N clips con crossfade.

    Devuelve (grafo, etiqueta_video_final, etiqueta_audio_final,
    duración_total). Cada entrada se normaliza (prep + fps + timebase) porque
    xfade exige streams homogéneos; el audio se encadena con acrossfade.
    """
    td = min(transition_seconds, min(durations) / 2)
    parts = []
    for i in range(len(durations)):
        parts.append(f"[{i}:v]{prep},fps={fps:g},settb=AVTB[v{i}]")
        parts.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo[a{i}]"
        )
    v_prev, a_prev = "v0", "a0"
    offset = 0.0
    for i in range(1, len(durations)):
        offset += durations[i - 1] - td
        v_out, a_out = f"vx{i}", f"ax{i}"
        parts.append(
            f"[{v_prev}][v{i}]xfade=transition=fade:"
            f"duration={td:.3f}:offset={offset:.3f}[{v_out}]"
        )
        parts.append(f"[{a_prev}][a{i}]acrossfade=d={td:.3f}[{a_out}]")
        v_prev, a_prev = v_out, a_out
    # formato final explícito: sin esto el encoder puede negociar 4:4:4,
    # que muchos decodificadores hardware de móvil no soportan
    parts.append(f"[{v_prev}]format=yuv420p[vout]")
    total = sum(durations) - td * (len(durations) - 1)
    return ";".join(parts), "vout", a_prev, total


def concat_with_transitions(
    clips: list[Path],
    out_path: Path,
    *,
    hdr: bool = False,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float], None] | None = None,
) -> None:
    """Une clips con crossfade corto entre ellos. Recodifica (NVENC/x264).

    Con un solo clip cae al concat normal sin recodificar.
    """
    if len(clips) < 2:
        concat_clips(clips, out_path, cancel_event=cancel_event)
        return
    ffmpeg = find_binary("ffmpeg")
    durations = [probe_duration(clip) for clip in clips]
    graph, v_label, a_label, total = build_transition_graph(
        durations, prep=sdr_prep_filter(hdr), fps=probe_fps(clips[0])
    )
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        *(arg for clip in clips for arg in ("-i", str(clip))),
        "-filter_complex", graph,
        "-map", f"[{v_label}]", "-map", f"[{a_label}]",
        *encode_args(),
        "-progress", "pipe:1", "-nostats",
        str(out_path),
    ]
    try:
        _run(cmd, cancel_event=cancel_event, progress_cb=progress_cb, total_duration=total)
    except (JobCancelled, FFmpegError):
        out_path.unlink(missing_ok=True)
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
