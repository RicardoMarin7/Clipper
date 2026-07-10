"""Validación de rutas, carpeta de salida y nombres de archivo."""

from __future__ import annotations

import sys
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi"}


def app_root() -> Path:
    """Raíz de la app: la del proyecto en desarrollo, la del exe empaquetado."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def kill_sounds_dir() -> Path:
    return app_root() / "assets" / "kill_sounds"


def is_valid_video(path: str | Path) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS


def default_output_dir(video_path: str | Path) -> Path:
    """Carpeta de salida sugerida: <carpeta_del_video>/highlights/."""
    return Path(video_path).parent / "highlights"


def ensure_output_dir(path: str | Path) -> Path:
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out


def clip_filename(index: int, start_seconds: float, suffix: str = "") -> str:
    """Nombre estable y ordenable: highlight_03_00-17-42.mp4 (hh-mm-ss del video)."""
    total = int(start_seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"highlight_{index:02d}_{h:02d}-{m:02d}-{s:02d}{suffix}.mp4"


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"
