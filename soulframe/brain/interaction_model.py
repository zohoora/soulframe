"""Interaction model for Soul Frame.

Performs gaze hit-testing against image regions, tracks per-region dwell
times, and computes a distance-based intensity factor.
"""

import logging
from typing import Dict, List

from soulframe import config
from soulframe.shared.types import FaceData, Region
from soulframe.shared.geometry import region_hit_test

logger = logging.getLogger(__name__)


class InteractionResult:
    """Output of a single InteractionModel.update call."""

    __slots__ = ("active_regions", "dwell_regions", "distance_factor", "min_active_confidence")

    def __init__(
        self,
        active_regions: List[str],
        dwell_regions: List[str],
        distance_factor: float,
        min_active_confidence: float = 0.0,
    ) -> None:
        self.active_regions = active_regions
        self.dwell_regions = dwell_regions
        self.distance_factor = distance_factor
        self.min_active_confidence = min_active_confidence


class InteractionModel:
    """Gaze hit-testing, dwell tracking, and distance-based modifiers."""

    def __init__(self) -> None:
        self._dwell_timers: Dict[str, float] = {}
        self._prev_active: set = set()
        # Per-image distance thresholds (overridden by image metadata)
        self._near_cm: float = config.CLOSE_INTERACTION_DISTANCE_CM
        self._far_cm: float = config.PRESENCE_DISTANCE_CM

    def set_distance_thresholds(self, near_cm: float, far_cm: float) -> None:
        """Set per-image distance thresholds for intensity calculation."""
        self._near_cm = near_cm
        self._far_cm = far_cm

    def update(
        self,
        face_data: FaceData,
        regions: List[Region],
        dt: float,
    ) -> InteractionResult:
        active_ids: List[str] = []
        dwell_ids: List[str] = []

        face_detected = face_data.num_faces > 0
        gx = face_data.gaze_screen_x
        gy = face_data.gaze_screen_y
        confidence = face_data.gaze_confidence

        if face_detected and confidence > 0:
            for region in regions:
                rid = region.id
                points = region.shape.points_normalized
                if not points:
                    continue
                if region_hit_test(gx, gy, points):
                    active_ids.append(rid)
                    min_conf = region.gaze_trigger.min_confidence
                    if confidence >= min_conf:
                        self._dwell_timers[rid] = self._dwell_timers.get(rid, 0.0) + dt
                    else:
                        self._dwell_timers[rid] = 0.0

                    dwell_threshold_s = region.gaze_trigger.dwell_time_ms / 1000.0
                    if (
                        self._dwell_timers.get(rid, 0.0) >= dwell_threshold_s
                        and confidence >= min_conf
                    ):
                        dwell_ids.append(rid)

        # Reset dwell timers for regions the gaze has left
        current_active_set = set(active_ids)
        for rid in self._prev_active - current_active_set:
            self._dwell_timers.pop(rid, None)
        self._prev_active = current_active_set

        distance_factor = self._compute_distance_factor(face_data)

        # Compute minimum confidence threshold of dwelled regions.
        # Used by state machine for gaze-away detection so it uses
        # the per-region threshold instead of the global default.
        min_conf = 0.0
        if dwell_ids:
            confs = []
            for region in regions:
                if region.id in dwell_ids:
                    confs.append(region.gaze_trigger.min_confidence)
            if confs:
                min_conf = min(confs)

        return InteractionResult(
            active_regions=active_ids,
            dwell_regions=dwell_ids,
            distance_factor=distance_factor,
            min_active_confidence=min_conf,
        )

    def reset(self) -> None:
        self._dwell_timers.clear()
        self._prev_active.clear()

    def _compute_distance_factor(self, face_data: FaceData) -> float:
        """0.0 = far, 1.0 = very close."""
        if face_data.num_faces == 0:
            return 0.0
        d = face_data.face_distance_cm
        near = self._near_cm
        far = self._far_cm
        if near >= far:
            # Guard against division by zero when near == far
            return 1.0 if d <= near else 0.0
        if d <= near:
            return 1.0
        if d >= far:
            return 0.0
        return 1.0 - (d - near) / (far - near)
