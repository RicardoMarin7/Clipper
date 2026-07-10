"""Persistencia de ajustes del usuario en %APPDATA%/Clipper/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Clipper"
SETTINGS_FILE = APP_DIR / "settings.json"

DEFAULTS: dict = {
    "video_path": "",
    "output_dir": "",
    "sensitivity": 5,
    "pre_padding": 3,
    "post_padding": 5,
    "exact_cut": False,
    "detection_mode": "both",
    "kill_threshold": 0.45,
    "output_format": "horizontal",
    "vertical_style": "blur",
    "compilation_mode": "none",
}


def load_settings() -> dict:
    """Devuelve los ajustes guardados fusionados sobre los defaults."""
    settings = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            settings.update({k: stored[k] for k in DEFAULTS if k in stored})
    except (OSError, json.JSONDecodeError):
        pass  # primera ejecución o archivo corrupto: usar defaults
    return settings


def save_settings(settings: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass  # no poder guardar ajustes nunca debe tirar la app
