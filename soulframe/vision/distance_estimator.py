"""
Distance estimation from face features.

Two complementary strategies:
  1. Iris-diameter triangulation  (11.7 mm average human iris)
  2. Bounding-box triangulation   (14 cm average face height)
"""

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Anthropometric constants
AVERAGE_IRIS_DIAMETER_MM = 11.7
AVERAGE_FACE_HEIGHT_MM = 140.0  # ~14 cm


class DistanceEstimator:
    """Estimate subject distance from the camera in centimetres."""

    # ------------------------------------------------------------------
    # Individual strategies
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_from_iris(
        landmarks: dict[str, tuple[float, float]],
        frame_width: int,
    ) -> float | None:
        """Triangulate distance using iris diameter.

        Requires *left_iris* and *right_iris* landmarks (normalised 0-1),
        or *left_eye* and *right_eye* as a rough proxy.

        Returns distance in **centimetres**, or ``None`` when the
        required landmarks are unavailable.
        """
        # Try dedicated iris landmarks first; fall back to eye centres.
        left_key: str | None = None
        right_key: str | None = None

        if "left_iris" in landmarks and "right_iris" in landmarks:
            left_key, right_key = "left_iris", "right_iris"
        elif "left_eye" in landmarks and "right_eye" in landmarks:
            left_key, right_key = "left_eye", "right_eye"
        else:
            return None

        lx, ly = landmarks[left_key]
        rx, ry = landmarks[right_key]

        # Inter-iris (or inter-eye) distance in pixels.
        pixel_dist = np.sqrt(
            ((lx - rx) * frame_width) ** 2
            + ((ly - ry) * frame_width) ** 2  # approx aspect ratio 1
        )

        if pixel_dist < 1.0:
            return None

        # Average human inter-pupillary distance is ~63 mm.
        INTER_PUPIL_MM = 63.0
        focal_length_px = float(frame_width)  # rough estimate

        distance_mm = (INTER_PUPIL_MM * focal_length_px) / pixel_dist
        return float(distance_mm / 10.0)  # mm → cm

    @staticmethod
    def estimate_from_bbox(
        bbox: tuple[int, int, int, int],
        frame_width: int,
        frame_height: int,
    ) -> float:
        """Triangulate distance using face bounding-box height.

        Assumes average face height of ~14 cm and a focal length
        approximately equal to *frame_width* pixels.

        Returns distance in **centimetres**.
        """
        _, _, _, bh = bbox
        if bh < 1:
            bh = 1

        focal_length_px = float(frame_width)
        distance_mm = (AVERAGE_FACE_HEIGHT_MM * focal_length_px) / float(bh)
        return float(distance_mm / 10.0)  # mm → cm

    # ------------------------------------------------------------------
    # Unified estimator
    # ------------------------------------------------------------------

    def estimate(
        self,
        landmarks: dict[str, tuple[float, float]],
        bbox: tuple[int, int, int, int],
        frame_width: int,
        frame_height: int,
    ) -> float:
        """Return best-effort distance estimate in centimetres.

        Tries iris-based triangulation first; falls back to bounding-box.
        """
        iris_dist = self.estimate_from_iris(landmarks, frame_width)
        if iris_dist is not None:
            logger.debug("Distance (iris): %.1f cm", iris_dist)
            return iris_dist

        bbox_dist = self.estimate_from_bbox(bbox, frame_width, frame_height)
        logger.debug("Distance (bbox): %.1f cm", bbox_dist)
        return bbox_dist
