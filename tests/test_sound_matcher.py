"""Tests del matcher de sonidos de kill: señales sintéticas, sin FFmpeg."""

import wave
from pathlib import Path

import numpy as np

from core.sound_matcher import DEFAULT_THRESHOLD, find_kills, load_templates

RATE = 16000


def write_wav(path: Path, samples: np.ndarray) -> None:
    data = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(RATE)
        wf.writeframes(data.tobytes())


def kill_sound() -> np.ndarray:
    """Sonido sintético distintivo de 0.4 s (multitono con envolvente)."""
    t = np.arange(int(0.4 * RATE)) / RATE
    tone = (
        0.6 * np.sin(2 * np.pi * 1300 * t)
        + 0.4 * np.sin(2 * np.pi * 3400 * t)
        + 0.2 * np.sin(2 * np.pi * 700 * t)
    )
    return (tone * np.hanning(len(t))).astype(np.float32)


def make_template_dir(tmp_path: Path) -> Path:
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    write_wav(template_dir / "kill.wav", kill_sound())
    return template_dir


def make_match_audio(tmp_path: Path, kill_times: list[float], seconds: float = 60.0) -> Path:
    """Ruido de fondo + el sonido de kill (atenuado) en cada timestamp,
    más un distractor fuerte no relacionado a los 25 s."""
    rng = np.random.default_rng(42)
    audio = rng.normal(0.0, 0.02, int(seconds * RATE)).astype(np.float32)
    template = kill_sound()
    for t0 in kill_times:
        i = int(t0 * RATE)
        audio[i : i + len(template)] += template * 0.5
    if seconds > 26:
        t = np.arange(int(0.4 * RATE)) / RATE
        i = int(25 * RATE)
        audio[i : i + len(t)] += (0.8 * np.sin(2 * np.pi * 400 * t)).astype(np.float32)
    path = tmp_path / "match.wav"
    write_wav(path, audio)
    return path


def test_finds_embedded_kills_and_ignores_distractor(tmp_path):
    templates = load_templates(make_template_dir(tmp_path))
    audio = make_match_audio(tmp_path, kill_times=[10.0, 40.0])

    times, scores = find_kills(audio, templates)

    assert len(times) == 2, f"esperaba 2 kills, detectó {len(times)} en {times}"
    assert abs(times[0] - 10.0) < 0.1
    assert abs(times[1] - 40.0) < 0.1
    assert all(score >= DEFAULT_THRESHOLD for score in scores)


def test_pure_noise_yields_no_matches(tmp_path):
    templates = load_templates(make_template_dir(tmp_path))
    rng = np.random.default_rng(7)
    noise_path = tmp_path / "noise.wav"
    write_wav(noise_path, rng.normal(0.0, 0.05, 30 * RATE).astype(np.float32))

    times, _scores = find_kills(noise_path, templates)
    assert len(times) == 0


def test_close_matches_collapse_into_one_event(tmp_path):
    # Dos inserciones a 0.3 s de distancia: menos que min_separation -> 1 evento
    templates = load_templates(make_template_dir(tmp_path))
    audio = make_match_audio(tmp_path, kill_times=[10.0, 10.3], seconds=20.0)

    times, _scores = find_kills(audio, templates)
    assert len(times) == 1


def test_detection_across_chunk_boundary(tmp_path):
    # CHUNK_FRAMES=16384 a 10 ms/frame => frontera de trozo en ~163.8 s
    templates = load_templates(make_template_dir(tmp_path))
    audio = make_match_audio(tmp_path, kill_times=[163.7], seconds=180.0)

    times, _scores = find_kills(audio, templates)
    assert len(times) == 1
    assert abs(times[0] - 163.7) < 0.1


def test_missing_template_dir_returns_empty(tmp_path):
    assert load_templates(tmp_path / "no_existe") == []
