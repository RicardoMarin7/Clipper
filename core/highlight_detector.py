"""Detección y fusión de highlights a partir de las señales de audio.

Dos fuentes de segmentos, ambas lógica pura (arrays -> segmentos, sin I/O):
- detect(): picos de intensidad RMS con umbral estadístico (media + k·σ),
  gobernado por el slider de sensibilidad 1-10.
- build_segments(): eventos puntuales con timestamp exacto (p. ej. kills
  detectadas por sound_matcher), expandidos con el padding.

combine() fusiona los segmentos de varias fuentes en la lista final: los
solapes se unen en un solo clip y las razones se concatenan ("kill+audio-peak").
"""

from __future__ import annotations

import numpy as np

from core.models import HighlightSegment

# k alto = umbral exigente = pocos clips. Sensibilidad 1 -> K_MAX, 10 -> K_MIN.
K_MAX = 3.5
K_MIN = 0.8

# Segmentos separados por menos de este hueco se fusionan en un solo clip
MERGE_GAP_SECONDS = 1.0


def sensitivity_to_k(sensitivity: int) -> float:
    s = min(10, max(1, sensitivity))
    return K_MAX - (s - 1) * (K_MAX - K_MIN) / 9.0


def detect(
    timestamps: np.ndarray,
    rms: np.ndarray,
    *,
    sensitivity: int,
    pre_padding: float,
    post_padding: float,
    duration: float,
) -> list[HighlightSegment]:
    """Segmentos por picos de intensidad: umbraliza, expande y fusiona."""
    if len(rms) == 0:
        return []

    threshold = float(rms.mean() + sensitivity_to_k(sensitivity) * rms.std())
    peak_indices = np.flatnonzero(rms >= threshold)
    if peak_indices.size == 0:
        return []

    span = max(float(rms.max()) - threshold, 1e-9)
    intervals = [
        (
            max(0.0, float(timestamps[i]) - pre_padding),
            min(duration, float(timestamps[i]) + post_padding),
            (float(rms[i]) - threshold) / span,
            "audio-peak",
        )
        for i in peak_indices
    ]
    return _merge(intervals)


def build_segments(
    event_times: np.ndarray,
    event_scores: np.ndarray,
    *,
    pre_padding: float,
    post_padding: float,
    duration: float,
    reason: str,
) -> list[HighlightSegment]:
    """Segmentos a partir de eventos puntuales ya detectados (p. ej. kills)."""
    intervals = [
        (
            max(0.0, float(t) - pre_padding),
            min(duration, float(t) + post_padding),
            float(score),
            reason,
        )
        for t, score in zip(event_times, event_scores)
    ]
    return _merge(sorted(intervals)) if intervals else []


def combine(*segment_lists: list[HighlightSegment]) -> list[HighlightSegment]:
    """Fusiona los segmentos de varias fuentes en una sola lista ordenada."""
    intervals = sorted(
        (segment.start, segment.end, segment.score, segment.reason)
        for segments in segment_lists
        for segment in segments
    )
    return _merge(intervals) if intervals else []


def _merge(
    intervals: list[tuple[float, float, float, str]],
    gap: float = MERGE_GAP_SECONDS,
) -> list[HighlightSegment]:
    """Une intervalos ordenados que se solapan o casi se tocan."""
    merged: list[list] = [list(intervals[0])]
    for start, end, score, reason in intervals[1:]:
        current = merged[-1]
        if start <= current[1] + gap:
            current[1] = max(current[1], end)
            current[2] = max(current[2], score)
            if reason not in current[3].split("+"):
                current[3] = f"{current[3]}+{reason}"
        else:
            merged.append([start, end, score, reason])
    return [
        HighlightSegment(
            start=round(start, 3),
            end=round(end, 3),
            score=round(min(1.0, max(0.0, score)), 3),
            reason=reason,
        )
        for start, end, score, reason in merged
    ]
