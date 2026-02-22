"""
Map 3D gaze direction to screen-space coordinates (0.0 -- 1.0).

Optionally loads a saved calibration from disk; otherwise falls back to
a simple linear mapping where (yaw=0, pitch=0) maps to screen centre.
"""

import json
import logging
from pathlib import Path
from typing import Any

from soulframe import config

logger = logging.getLogger(__name__)

_DEFAULT_CALIBRATION: dict[str, Any] = {
    # Scale factors: how many screen-units per radian of gaze angle.
    "gaze_yaw_scale": 1.5,
    "gaze_pitch_scale": 1.2,
    # Weight of head pose vs. pure gaze direction.
    "head_weight": 0.3,
    # Offsets — allow the centre-point to be shifted after calibration.
    "offset_x": 0.0,
    "offset_y": 0.0,
}


class ScreenMapper:
    """Convert gaze + head-pose angles to normalised screen position."""

    def __init__(self) -> None:
        self._calibration: dict[str, Any] = dict(_DEFAULT_CALIBRATION)
        self._calibration_path: Path = (
            Path(config.CALIBRATION_DIR) / "screen_calibration.json"
        )
        self.load_calibration()

    # ------------------------------------------------------------------
    # Calibration persistence
    # ------------------------------------------------------------------

    def load_calibration(self) -> None:
        """Load calibration from disk if the file exists."""
        if self._calibration_path.exists():
            try:
                with open(self._calibration_path, "r") as fh:
                    data = json.load(fh)
                self._calibration.update(data)
                logger.info(
                    "Screen calibration loaded from %s", self._calibration_path
                )
            except Exception:
                logger.warning(
                    "Failed to load calibration; using defaults.",
                    exc_info=True,
                )
        else:
            logger.info(
                "No calibration file found at %s; using defaults.",
                self._calibration_path,
            )

    def save_calibration(self, data: dict[str, Any]) -> None:
        """Persist calibration data to disk.

        *data* is merged into the current calibration before saving.
        """
        self._calibration.update(data)
        self._calibration_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._calibration_path, "w") as fh:
            json.dump(self._calibration, fh, indent=2)
        logger.info("Screen calibration saved to %s", self._calibration_path)

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    def map_gaze(
        self,
        gaze_yaw: float,
        gaze_pitch: float,
        head_yaw: float = 0.0,
        head_pitch: float = 0.0,
    ) -> tuple[float, float]:
        """Map gaze and head-pose angles to screen coordinates.

        Parameters:
            gaze_yaw   — horizontal gaze angle in radians (negative = left)
            gaze_pitch — vertical gaze angle in radians   (positive = down)
            head_yaw   — horizontal head rotation in radians
            head_pitch — vertical head rotation in radians

        Returns:
            (screen_x, screen_y) clamped to [0.0, 1.0].
        """
        cal = self._calibration
        gaze_scale_x: float = cal["gaze_yaw_scale"]
        gaze_scale_y: float = cal["gaze_pitch_scale"]
        head_w: float = cal["head_weight"]
        offset_x: float = cal["offset_x"]
        offset_y: float = cal["offset_y"]

        # Blend gaze direction with head pose.
        combined_yaw = gaze_yaw * (1.0 - head_w) + head_yaw * head_w
        combined_pitch = gaze_pitch * (1.0 - head_w) + head_pitch * head_w

        # Linear projection centred at (0.5, 0.5).
        screen_x = 0.5 - combined_yaw * gaze_scale_x + offset_x
        screen_y = 0.5 + combined_pitch * gaze_scale_y + offset_y

        # Clamp to valid screen range.
        screen_x = max(0.0, min(1.0, screen_x))
        screen_y = max(0.0, min(1.0, screen_y))

        return screen_x, screen_y
