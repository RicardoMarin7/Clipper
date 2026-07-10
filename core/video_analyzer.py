"""Verificación visual de kills: busca el icono de calavera de la UI de kill.

Segunda señal del detector híbrido (activada en la iteración 2 del SDD):
el audio PROPONE candidatos con umbral bajo (recall alto) y este módulo los
CONFIRMA mirando 8 fotogramas alrededor de cada candidato. La confirmación
de kill (calavera + puntos) aparece en una zona fija centrada bajo la mira,
así que basta template matching ZNCC 2D sobre esa región en escala de grises.

Solo se decodifican ~2.5 s de video por candidato (una invocación de ffmpeg
cada uno), no el video completo. Calibrado con gameplay real verificado
frame a frame: kills reales puntúan 0.90-0.97; escenas sin kill, 0.60-0.77.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from utils import ffmpeg_wrapper as fw
from utils import file_manager
from utils.logger import get_logger

logger = get_logger(__name__)

# Región de búsqueda (coords sobre frame normalizado a 1920 de ancho, 16:9)
REGION_X, REGION_Y, REGION_W, REGION_H = 560, 600, 800, 200

BURST_SECONDS = 2.5   # ventana tras el sonido en la que aparece la UI
BURST_FPS = 3

# Regla de fusión audio+visual (calibrada con ground truth real):
VISUAL_CONFIRM = 0.80     # visual alto: kill confirmada por sí sola
VISUAL_WEAK = 0.62        # visual medio (UI breve: trade-kills, confirm tardía)
AUDIO_STRONG = 0.60       # ...aceptado solo si el audio también fue claro
_MIN_PATCH_STD = 4.0      # suelo de contraste: parches planos no puntúan

TEMPLATE_FILE = "kill_skull.npy"


def load_skull_template() -> np.ndarray | None:
    """Plantilla de la calavera (gris, float32). None si no existe el asset."""
    path = file_manager.app_root() / "assets" / TEMPLATE_FILE
    if not path.is_file():
        return None
    try:
        template = np.load(path)
        return template.astype(np.float32) if template.ndim == 2 else None
    except (OSError, ValueError):
        logger.exception("Plantilla de calavera ilegible: %s", path)
        return None


def verify_kill_events(
    video: Path,
    times: np.ndarray,
    audio_scores: np.ndarray,
    template: np.ndarray,
    *,
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
) -> np.ndarray:
    """Máscara booleana: qué candidatos de audio muestran la UI de kill."""
    from utils.ffmpeg_wrapper import JobCancelled

    confirmed = np.zeros(len(times), dtype=bool)
    for i, (t, audio_score) in enumerate(zip(times, audio_scores)):
        if cancel_event is not None and cancel_event.is_set():
            raise JobCancelled()
        visual = _visual_score(video, float(t), template)
        confirmed[i] = visual >= VISUAL_CONFIRM or (
            visual >= VISUAL_WEAK and float(audio_score) >= AUDIO_STRONG
        )
        logger.info("candidato t=%.2f audio=%.3f visual=%.3f -> %s",
                    t, audio_score, visual, "KILL" if confirmed[i] else "descartado")
        if progress_cb is not None:
            progress_cb(i + 1, len(times))
    return confirmed


# ------------------------------------------------------------------ internos

def _visual_score(video: Path, t: float, template: np.ndarray) -> float:
    best = 0.0
    for frame in _grab_burst(video, t + 0.1):
        best = max(best, zncc2d(frame, template))
        if best >= VISUAL_CONFIRM:
            break  # confirmado: no hace falta mirar más frames
    return best


def _grab_burst(video: Path, t: float) -> list[np.ndarray]:
    """Frames de la región de UI en una sola invocación de ffmpeg."""
    result = subprocess.run(
        [
            fw.find_binary("ffmpeg"), "-hide_banner", "-loglevel", "error",
            "-ss", f"{t:.2f}", "-t", f"{BURST_SECONDS:.2f}", "-i", str(video),
            "-vf", (f"fps={BURST_FPS},scale=1920:-2,"
                    f"crop={REGION_W}:{REGION_H}:{REGION_X}:{REGION_Y}"),
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        capture_output=True, creationflags=fw.CREATION_FLAGS,
    )
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    size = REGION_W * REGION_H
    count = data.size // size
    return [
        data[i * size:(i + 1) * size].reshape(REGION_H, REGION_W).astype(np.float32)
        for i in range(count)
    ]


def zncc2d(region: np.ndarray, template: np.ndarray) -> float:
    """Máxima correlación cruzada normalizada 2D de la plantilla en la región."""
    th, tw = template.shape
    rh, rw = region.shape
    if rh < th or rw < tw:
        return 0.0
    t = template - template.mean()
    t_norm = float(np.sqrt(np.sum(t * t, dtype=np.float64)))
    if t_norm == 0.0:
        return 0.0

    fa = np.fft.rfft2(region, (rh + th, rw + tw))
    ft = np.fft.rfft2(t[::-1, ::-1], (rh + th, rw + tw))
    corr = np.fft.irfft2(fa * ft)[th - 1:rh, tw - 1:rw]

    # estadísticos deslizantes con imagen integral en float64 (float32 sufre
    # cancelación catastrófica y produce varianzas negativas / scores > 1)
    r64 = region.astype(np.float64)
    ii = np.pad(np.cumsum(np.cumsum(r64, axis=0), axis=1), ((1, 0), (1, 0)))
    ii2 = np.pad(np.cumsum(np.cumsum(r64 ** 2, axis=0), axis=1), ((1, 0), (1, 0)))
    s1 = ii[th:, tw:] + ii[:-th, :-tw] - ii[th:, :-tw] - ii[:-th, tw:]
    s2 = ii2[th:, tw:] + ii2[:-th, :-tw] - ii2[th:, :-tw] - ii2[:-th, tw:]
    n = th * tw
    variance = np.maximum(s2 - s1 ** 2 / n, n * _MIN_PATCH_STD ** 2)

    return float((corr / (t_norm * np.sqrt(variance))).max())
