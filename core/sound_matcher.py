"""Detección del sonido de kill por template matching espectral.

Los sonidos de confirmación de kill de Battlefield 6 son samples fijos del
juego, así que se buscan por correlación cruzada normalizada (ZNCC) entre el
espectrograma log-magnitud de cada plantilla (assets/kill_sounds/*.wav) y el
del audio de la partida. La normalización hace el match inmune al volumen de
mezcla; el umbral descarta ruido de combate no relacionado.

Todo NumPy vectorizado y procesado por trozos: 2 h de audio se barren en
segundos con memoria acotada. Lógica pura sin UI ni FFmpeg: testeable con
señales sintéticas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.audio_analyzer import load_wav

SAMPLE_RATE = 16000
N_FFT = 512                    # ventanas de 32 ms
HOP = 160                      # salto de 10 ms
HOP_SECONDS = HOP / SAMPLE_RATE
CHUNK_FRAMES = 16384           # ~2.7 min de audio por trozo (memoria acotada)
# Calibrado con gameplay real (partida BF6 de 6:43 verificada frame a frame):
# kills reales puntúan 0.45-0.75; el ruido queda por debajo de 0.45. Este es
# el umbral de CANDIDATOS: la verificación visual (video_analyzer) descarta
# los falsos. Sin plantilla visual conviene subirlo a ~0.55.
DEFAULT_THRESHOLD = 0.45       # ZNCC mínima para aceptar un match (rango -1..1)
MIN_SEPARATION_SECONDS = 0.6   # matches más cercanos son el mismo evento
_TRIM_LEVEL = 0.02             # recorte de silencio en los bordes de la plantilla


@dataclass(frozen=True)
class Template:
    name: str
    spec: np.ndarray  # espectrograma log-mag con media cero, forma (F, M)
    norm: float       # ||spec||


def load_templates(directory: Path) -> list[Template]:
    """Carga todas las plantillas *.wav (16 kHz mono) de un directorio."""
    templates: list[Template] = []
    if not directory.is_dir():
        return templates
    for path in sorted(directory.glob("*.wav")):
        rate, samples = load_wav(path)
        if rate != SAMPLE_RATE:
            raise ValueError(f"La plantilla {path.name} debe ser de {SAMPLE_RATE} Hz")
        samples = _trim_silence(samples)
        spec = _log_spectrogram(samples)
        spec = spec - spec.mean()
        norm = float(np.sqrt(np.sum(spec * spec, dtype=np.float64)))
        if norm > 0:
            templates.append(Template(name=path.stem, spec=spec, norm=norm))
    return templates


def find_kills(
    wav_path: Path,
    templates: list[Template],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_separation: float = MIN_SEPARATION_SECONDS,
) -> tuple[np.ndarray, np.ndarray]:
    """Busca todas las plantillas en el audio. Devuelve (timestamps, scores).

    Los timestamps (segundos, ordenados) marcan el inicio del sonido de kill.
    Si varios matches (de una o varias plantillas) caen a menos de
    min_separation, se conserva solo el de mayor score.
    """
    rate, samples = load_wav(wav_path)
    if rate != SAMPLE_RATE:
        raise ValueError(f"Se esperaba audio de {SAMPLE_RATE} Hz")
    if not templates:
        return np.array([]), np.array([])

    max_frames = max(t.spec.shape[1] for t in templates)
    chunk_samples = CHUNK_FRAMES * HOP
    overlap_samples = (max_frames + 1) * HOP + N_FFT
    min_sep_frames = max(1, int(round(min_separation / HOP_SECONDS)))

    events: list[tuple[float, float]] = []  # (timestamp, score)
    start = 0
    while start < len(samples):
        chunk = samples[start : start + chunk_samples + overlap_samples]
        if len(chunk) >= N_FFT:
            spec = _log_spectrogram(chunk)
            base_time = start / SAMPLE_RATE
            for template in templates:
                scores = _zncc(spec, template)
                for idx in _pick_peaks(scores, threshold, min_sep_frames):
                    events.append((base_time + idx * HOP_SECONDS, float(scores[idx])))
        start += chunk_samples
    return _suppress_nearby(events, min_separation)


# ------------------------------------------------------------------ internos

def _trim_silence(samples: np.ndarray, level: float = _TRIM_LEVEL) -> np.ndarray:
    """Recorta los bordes por debajo de level·pico (los samples traen colas)."""
    peak = float(np.abs(samples).max()) if len(samples) else 0.0
    if peak == 0.0:
        return samples
    loud = np.flatnonzero(np.abs(samples) >= peak * level)
    return samples[loud[0] : loud[-1] + 1]


def _log_spectrogram(samples: np.ndarray) -> np.ndarray:
    """Espectrograma log-magnitud (F, N)."""
    if len(samples) < N_FFT:
        samples = np.pad(samples, (0, N_FFT - len(samples)))
    frames = np.lib.stride_tricks.sliding_window_view(samples, N_FFT)[::HOP]
    window = np.hanning(N_FFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * window, axis=1)).T
    return np.log1p(spec).astype(np.float32)


def _zncc(spec: np.ndarray, template: Template) -> np.ndarray:
    """Correlación cruzada normalizada de la plantilla contra cada offset.

    La correlación 2D (frecuencia colapsada) se calcula vía FFT por fila; los
    estadísticos deslizantes de la ventana, vía sumas acumuladas. O(N log N).
    """
    n_freq, n = spec.shape
    m = template.spec.shape[1]
    if n < m:
        return np.empty(0, dtype=np.float32)

    nfft = 1 << (n + m - 1).bit_length()
    fa = np.fft.rfft(spec, nfft, axis=1)
    ft = np.fft.rfft(template.spec[:, ::-1], nfft, axis=1)
    corr = np.fft.irfft(fa * ft, nfft, axis=1)[:, m - 1 : n].sum(axis=0)

    col_sum = spec.sum(axis=0, dtype=np.float64)
    col_sq = np.sum(spec.astype(np.float64) ** 2, axis=0)
    win_sum = _sliding_sum(col_sum, m)
    win_sq = _sliding_sum(col_sq, m)
    variance = np.maximum(win_sq - win_sum**2 / (n_freq * m), 1e-12)
    return (corr / (template.norm * np.sqrt(variance))).astype(np.float32)


def _sliding_sum(x: np.ndarray, window: int) -> np.ndarray:
    cumulative = np.concatenate(([0.0], np.cumsum(x, dtype=np.float64)))
    return cumulative[window:] - cumulative[:-window]


def _pick_peaks(scores: np.ndarray, threshold: float, min_sep_frames: int) -> list[int]:
    """Índices sobre el umbral, greedy por score con supresión de vecinos."""
    candidates = np.flatnonzero(scores >= threshold)
    order = candidates[np.argsort(scores[candidates])[::-1]]
    chosen: list[int] = []
    for i in order:
        if all(abs(int(i) - j) >= min_sep_frames for j in chosen):
            chosen.append(int(i))
    return sorted(chosen)


def _suppress_nearby(
    events: list[tuple[float, float]], min_separation: float
) -> tuple[np.ndarray, np.ndarray]:
    """Entre eventos a menos de min_separation (p. ej. dos plantillas que
    matchean el mismo sonido, o el solape entre trozos) gana el mayor score."""
    kept: list[tuple[float, float]] = []
    for t, score in sorted(events, key=lambda e: -e[1]):
        if all(abs(t - k) >= min_separation for k, _ in kept):
            kept.append((t, score))
    kept.sort()
    times = np.array([t for t, _ in kept])
    scores = np.array([s for _, s in kept])
    return times, scores
