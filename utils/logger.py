"""Logging a archivo (%APPDATA%/Clipper/clipper.log).

Los mensajes visibles para el usuario viajan como ProgressEvent hacia la UI;
este logger es el registro técnico persistente (stack traces, comandos ffmpeg).
"""

from __future__ import annotations

import logging

from utils.config import APP_DIR

LOG_FILE = APP_DIR / "clipper.log"
_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
            )
            root.addHandler(handler)
        except OSError:
            pass  # sin log en disco, pero la app sigue funcionando
        _configured = True
    return logging.getLogger(name)
