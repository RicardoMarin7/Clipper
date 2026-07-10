"""Análisis de energía RMS del audio, vectorizado con NumPy.

Recibe el WAV mono 16 kHz PCM que extrajo ffmpeg_wrapper y devuelve la señal
(timestamp, nivel RMS) en ventanas de 50 ms. Una hora de audio se analiza en
~1-2 s porque no hay bucles Python: reshape en ventanas + RMS por eje.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

WINDOW_MS = 50


def load_wav(wav_path: Path) -> tuple[int, np.ndarray]:
    """Carga un WAV mono PCM 16-bit como (rate, samples float32 en [-1, 1])."""
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise ValueError(
                "Se esperaba WAV mono PCM 16-bit (lo genera ffmpeg_wrapper.extract_audio)"
            )
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return rate, samples


def compute_rms(wav_path: Path, window_ms: int = WINDOW_MS) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (timestamps, rms): arrays paralelos, uno por ventana de audio.

    RMS normalizado a [0, 1] (1.0 = onda cuadrada a fondo de escala).
    """
    rate, samples = load_wav(wav_path)
    window = max(1, int(rate * window_ms / 1000))
    usable = (len(samples) // window) * window
    if usable == 0:
        return np.array([]), np.array([])

    frames = samples[:usable].reshape(-1, window)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    timestamps = np.arange(len(rms), dtype=np.float64) * (window / rate)
    return timestamps, rms
