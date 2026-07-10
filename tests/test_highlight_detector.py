"""Tests del detector: lógica pura, sin video ni FFmpeg."""

import numpy as np
import pytest

from core.highlight_detector import K_MAX, K_MIN, detect, sensitivity_to_k

DURATION = 100.0
STEP = 0.05  # ventanas de 50 ms, como audio_analyzer


def make_signal(spikes: dict[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Señal base silenciosa (determinista, con std > 0) más picos en `spikes`."""
    timestamps = np.arange(0.0, DURATION, STEP)
    rms = np.where(np.arange(len(timestamps)) % 2 == 0, 0.009, 0.011).astype(np.float64)
    for t, level in spikes.items():
        rms[int(t / STEP)] = level
    return timestamps, rms


def test_sensitivity_mapping_extremes():
    assert sensitivity_to_k(1) == pytest.approx(K_MAX)
    assert sensitivity_to_k(10) == pytest.approx(K_MIN)
    assert sensitivity_to_k(1) > sensitivity_to_k(5) > sensitivity_to_k(10)


def test_detects_isolated_peaks():
    timestamps, rms = make_signal({20.0: 0.9, 80.0: 0.8})
    segments = detect(
        timestamps, rms, sensitivity=5, pre_padding=3, post_padding=5, duration=DURATION
    )
    assert len(segments) == 2
    first, second = segments
    assert first.start == 17.0 and first.end == 25.0
    assert second.start == 77.0 and second.end == 85.0
    assert first.score > second.score  # el pico más alto puntúa más


def test_nearby_peaks_merge_into_one_segment():
    # Picos a 20.0 y 21.0 s: sus ventanas con padding se solapan -> un solo clip
    timestamps, rms = make_signal({20.0: 0.9, 21.0: 0.85})
    segments = detect(
        timestamps, rms, sensitivity=5, pre_padding=3, post_padding=5, duration=DURATION
    )
    assert len(segments) == 1
    assert segments[0].start == 17.0
    assert segments[0].end == 26.0


def test_segments_clamped_to_video_bounds():
    timestamps, rms = make_signal({1.0: 0.9, 99.0: 0.9})
    segments = detect(
        timestamps, rms, sensitivity=5, pre_padding=3, post_padding=5, duration=DURATION
    )
    assert segments[0].start == 0.0
    assert segments[-1].end == DURATION


def test_silence_returns_no_segments():
    timestamps, rms = make_signal({})
    segments = detect(
        timestamps, rms, sensitivity=5, pre_padding=3, post_padding=5, duration=DURATION
    )
    assert segments == []


def test_empty_signal_returns_no_segments():
    assert detect(
        np.array([]), np.array([]),
        sensitivity=5, pre_padding=3, post_padding=5, duration=0.0,
    ) == []
