"""Shared dataclasses, enums, and command types for Soul Frame."""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


# ── Interaction States ─────────────────────────────────────────────────────

class InteractionState(Enum):
    IDLE = auto()
    PRESENCE = auto()
    ENGAGED = auto()
    CLOSE_INTERACTION = auto()
    WITHDRAWING = auto()


# ── Vision Data ────────────────────────────────────────────────────────────

@dataclass
class FaceData:
    """Snapshot of vision pipeline output."""
    frame_counter: int = 0
    num_faces: int = 0
    face_distance_cm: float = 0.0
    gaze_screen_x: float = 0.0  # normalized 0.0–1.0
    gaze_screen_y: float = 0.0
    gaze_confidence: float = 0.0
    head_yaw: float = 0.0
    head_pitch: float = 0.0
    timestamp_ns: int = 0


# ── Region Geometry ────────────────────────────────────────────────────────

@dataclass
class RegionShape:
    shape_type: str  # "polygon"
    points_normalized: List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class GazeTrigger:
    dwell_time_ms: int = 1500
    min_confidence: float = 0.6


@dataclass
class HeartbeatConfig:
    file: str = ""
    loop: bool = True
    bass_boost: bool = True
    fade_in_ms: int = 2000
    max_distance_cm: float = 150.0
    min_distance_cm: float = 30.0
    curve: str = "exponential"


@dataclass
class VisualEffect:
    effect_type: str = "breathing"
    params: Dict[str, Any] = field(default_factory=dict)
    trigger: str = "on_gaze_dwell"
    fade_in_ms: int = 3000


@dataclass
class Region:
    id: str = ""
    label: str = ""
    shape: RegionShape = field(default_factory=RegionShape)
    gaze_trigger: GazeTrigger = field(default_factory=GazeTrigger)
    heartbeat: Optional[HeartbeatConfig] = None
    visual_effects: List[VisualEffect] = field(default_factory=list)


# ── Image Metadata ─────────────────────────────────────────────────────────

@dataclass
class AmbientAudioConfig:
    file: str = ""
    loop: bool = True
    fade_in_distance_cm: float = 200.0
    fade_in_complete_cm: float = 100.0
    fade_curve: str = "ease_in_out"


@dataclass
class ImageMetadata:
    version: int = 1
    id: str = ""
    title: str = ""
    image_filename: str = "image.jpg"
    image_width: int = 1920
    image_height: int = 1080
    ambient: Optional[AmbientAudioConfig] = None
    regions: List[Region] = field(default_factory=list)
    min_interaction_distance_cm: float = 300.0
    close_interaction_distance_cm: float = 80.0
    fade_in_ms: int = 2000
    fade_out_ms: int = 2000
    audio_crossfade_ms: int = 3000


# ── IPC Commands ───────────────────────────────────────────────────────────

class CommandType(Enum):
    # Display commands
    LOAD_IMAGE = auto()
    SET_EFFECT = auto()
    SET_EFFECT_INTENSITY = auto()
    CROSSFADE_IMAGE = auto()
    SET_VIGNETTE = auto()
    SET_PARALLAX = auto()

    # Audio commands
    PLAY_AMBIENT = auto()
    STOP_AMBIENT = auto()
    PLAY_HEARTBEAT = auto()
    STOP_HEARTBEAT = auto()
    SET_VOLUME = auto()
    FADE_ALL = auto()
    STOP_ALL = auto()

    # System commands
    SHUTDOWN = auto()


@dataclass
class Command:
    cmd_type: CommandType
    params: Dict[str, Any] = field(default_factory=dict)
