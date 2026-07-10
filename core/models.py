"""Contratos compartidos entre capas.

Todo lo que cruza una frontera de capa (UI <-> pipeline <-> analizadores)
es un tipo explícito definido aquí. La UI solo consume estos tipos; nunca
importa analizadores ni wrappers directamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path


# Modos de detección (JobConfig.detection_mode)
DETECT_KILLS = "kills"          # solo sonidos de kill (template matching)
DETECT_BOTH = "both"            # kills + picos de intensidad
DETECT_INTENSITY = "intensity"  # solo picos de intensidad (RMS)


class EventKind(Enum):
    STAGE = auto()      # cambio de etapa del pipeline
    PROGRESS = auto()   # avance porcentual (coalescible en la UI)
    LOG = auto()        # línea para la consola de registro
    DONE = auto()       # terminal: trabajo completado
    ERROR = auto()      # terminal: fallo irrecuperable
    CANCELLED = auto()  # terminal: aborto limpio pedido por el usuario


@dataclass(frozen=True)
class ProgressEvent:
    """Mensaje que viaja del hilo worker a la UI por la queue."""

    kind: EventKind
    stage: str = ""
    percent: float | None = None  # 0.0-100.0; None = sin cambio
    message: str = ""
    level: str = "INFO"  # INFO | SUCCESS | WARN | ERROR
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JobConfig:
    """Configuración completa de un trabajo, construida por la UI."""

    video_path: Path
    output_dir: Path
    sensitivity: int = 5        # 1 (pocos clips) .. 10 (muchos clips)
    pre_padding: float = 3.0    # segundos antes del pico
    post_padding: float = 5.0   # segundos después del pico
    exact_cut: bool = False     # False = stream copy, True = recodificar (NVENC/x264)
    detection_mode: str = DETECT_BOTH  # DETECT_KILLS | DETECT_BOTH | DETECT_INTENSITY
    kill_threshold: float = 0.50  # ZNCC mínima para aceptar un match de kill (0.30-0.80)


@dataclass(frozen=True)
class HighlightSegment:
    """Segmento detectado, en segundos desde el inicio del video."""

    start: float
    end: float
    score: float = 0.0  # 0-1, intensidad relativa del pico
    reason: str = "audio-peak"

    @property
    def duration(self) -> float:
        return self.end - self.start
