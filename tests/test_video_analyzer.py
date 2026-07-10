"""Tests del matcher visual (ZNCC 2D pura, sin video real)."""

import numpy as np

from core.video_analyzer import zncc2d


def make_template() -> np.ndarray:
    rng = np.random.default_rng(3)
    return (rng.uniform(0, 255, (40, 36))).astype(np.float32)


def test_finds_template_embedded_in_noise():
    rng = np.random.default_rng(7)
    region = rng.uniform(0, 255, (200, 800)).astype(np.float32)
    template = make_template()
    region[80:120, 300:336] = template  # incrustada exacta
    assert zncc2d(region, template) > 0.95


def test_absent_template_scores_low():
    rng = np.random.default_rng(11)
    region = rng.uniform(0, 255, (200, 800)).astype(np.float32)
    assert zncc2d(region, make_template()) < 0.5


def test_flat_region_does_not_blow_up():
    # Parche plano: sin suelo de contraste la varianza ~0 dispara el score
    region = np.full((200, 800), 17.0, dtype=np.float32)
    score = zncc2d(region, make_template())
    assert -1.0 <= score <= 1.0


def test_region_smaller_than_template_returns_zero():
    region = np.zeros((20, 20), dtype=np.float32)
    assert zncc2d(region, make_template()) == 0.0
