"""
Pydantic models for Soul Frame image metadata.
Matches the metadata.json schema from the Soul Frame spec.
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Tuple


class RegionShapeModel(BaseModel):
    type: str = "polygon"
    points_normalized: List[List[float]] = []


class GazeTriggerModel(BaseModel):
    dwell_time_ms: int = 1500
    min_confidence: float = 0.6


class HeartbeatModel(BaseModel):
    file: str = ""
    loop: bool = True
    bass_boost: bool = True
    fade_in_ms: int = 2000
    intensity_by_distance: Dict[str, Any] = {
        "max_distance_cm": 150,
        "min_distance_cm": 30,
        "curve": "exponential"
    }


class VisualEffectModel(BaseModel):
    type: str = "breathing"
    params: Dict[str, Any] = {}
    trigger: str = "on_gaze_dwell"
    fade_in_ms: int = 3000


class RegionModel(BaseModel):
    id: str = ""
    label: str = ""
    shape: RegionShapeModel = RegionShapeModel()
    gaze_trigger: GazeTriggerModel = GazeTriggerModel()
    heartbeat: Optional[HeartbeatModel] = None
    visual_effects: List[VisualEffectModel] = []


class AmbientAudioModel(BaseModel):
    file: str = ""
    loop: bool = True
    fade_in_distance_cm: float = 200.0
    fade_in_complete_cm: float = 100.0
    fade_curve: str = "ease_in_out"


class AudioModel(BaseModel):
    ambient: Optional[AmbientAudioModel] = None


class ImageInfoModel(BaseModel):
    filename: str = "image.jpg"
    width: int = 1920
    height: int = 1080


class InteractionModel(BaseModel):
    min_interaction_distance_cm: float = 300.0
    close_interaction_distance_cm: float = 80.0


class TransitionsModel(BaseModel):
    fade_in_ms: int = 2000
    fade_out_ms: int = 2000
    audio_crossfade_ms: int = 3000


class ImageMetadataModel(BaseModel):
    version: int = 1
    id: str = ""
    title: str = ""
    image: ImageInfoModel = ImageInfoModel()
    audio: AudioModel = AudioModel()
    regions: List[RegionModel] = []
    interaction: InteractionModel = InteractionModel()
    transitions: TransitionsModel = TransitionsModel()
