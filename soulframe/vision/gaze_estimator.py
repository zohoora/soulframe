"""
Gaze estimation with tiered backend selection.

Priority order:
  1. TensorRT engine  (config.GAZE_ENGINE_PATH)
  2. ONNX via OpenCV DNN  (config.GAZE_MODEL_PATH)
  3. Head-pose approximation via cv2.solvePnP
"""

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from soulframe import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Standard 3D face model — anthropometric points in mm, centred on the nose.
# Order: nose_tip, left_eye, right_eye, mouth_center, left_ear, right_ear
# These are the same six landmarks MediaPipe provides.
# ---------------------------------------------------------------------------
FACE_3D_MODEL = np.array(
    [
        [0.0, 0.0, 0.0],           # nose_tip
        [-65.5, -5.0, -20.0],      # left_eye
        [65.5, -5.0, -20.0],       # right_eye
        [0.0, 48.0, -10.0],        # mouth_center
        [-100.0, -15.0, -75.0],    # left_ear
        [100.0, -15.0, -75.0],     # right_ear
    ],
    dtype=np.float64,
)

# Minimal 3-point subset (nose, left_eye, right_eye) for when fewer
# landmarks are available.
FACE_3D_MODEL_MINIMAL = FACE_3D_MODEL[:3]

# Landmark names in the order that matches FACE_3D_MODEL rows.
_LANDMARK_ORDER_FULL = [
    "nose_tip",
    "left_eye",
    "right_eye",
    "mouth_center",
    "left_ear",
    "right_ear",
]

_LANDMARK_ORDER_MINIMAL = ["nose_tip", "left_eye", "right_eye"]


class GazeEstimator:
    """Estimate gaze direction from a face crop and its landmarks."""

    def __init__(self) -> None:
        self._backend: str = "none"
        self._net: Any = None

        if self._try_tensorrt():
            return
        if self._try_onnx():
            return
        self._use_headpose_fallback()

    # ------------------------------------------------------------------
    # Backend init helpers
    # ------------------------------------------------------------------

    def _try_tensorrt(self) -> bool:
        engine_path = Path(config.GAZE_ENGINE_PATH)
        if not engine_path.exists():
            return False
        try:
            import tensorrt  # noqa: F401  # type: ignore[import-untyped]

            # A real integration would deserialise the engine here.
            # Placeholder for future TensorRT runtime setup.
            self._backend = "tensorrt"
            logger.info("GazeEstimator using TensorRT backend: %s", engine_path)
            return True
        except Exception:
            logger.debug("TensorRT import/load failed.", exc_info=True)
            return False

    def _try_onnx(self) -> bool:
        model_path = Path(config.GAZE_MODEL_PATH)
        if not model_path.exists():
            return False
        try:
            self._net = cv2.dnn.readNetFromONNX(str(model_path))
            self._backend = "onnx"
            logger.info("GazeEstimator using ONNX/DNN backend: %s", model_path)
            return True
        except Exception:
            logger.debug("ONNX model load failed.", exc_info=True)
            return False

    def _use_headpose_fallback(self) -> None:
        self._backend = "headpose"
        logger.info(
            "GazeEstimator using head-pose solvePnP fallback "
            "(no TensorRT engine or ONNX model found)."
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(
        self,
        frame: np.ndarray,
        face_landmarks: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        """Estimate gaze from *frame* and normalised *face_landmarks*.

        Returns:
            dict with gaze_yaw, gaze_pitch (radians),
            gaze_vector (3-element unit vector), confidence (0-1).
        """
        if self._backend == "tensorrt":
            return self._estimate_tensorrt(frame, face_landmarks)
        elif self._backend == "onnx":
            return self._estimate_onnx(frame, face_landmarks)
        else:
            return self._estimate_headpose(frame, face_landmarks)

    # ------------------------------------------------------------------
    # TensorRT estimation (placeholder)
    # ------------------------------------------------------------------

    def _estimate_tensorrt(
        self,
        frame: np.ndarray,
        face_landmarks: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        # Placeholder — delegates to headpose until a real engine is
        # integrated.
        return self._estimate_headpose(frame, face_landmarks)

    # ------------------------------------------------------------------
    # ONNX / OpenCV DNN estimation (placeholder forward pass)
    # ------------------------------------------------------------------

    def _estimate_onnx(
        self,
        frame: np.ndarray,
        face_landmarks: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        # A real pipeline would crop the eye region, resize, normalise,
        # and run inference.  For now fall through to headpose.
        return self._estimate_headpose(frame, face_landmarks)

    # ------------------------------------------------------------------
    # Head-pose approximation via solvePnP
    # ------------------------------------------------------------------

    def _estimate_headpose(
        self,
        frame: np.ndarray,
        face_landmarks: dict[str, tuple[float, float]],
    ) -> dict[str, Any]:
        h, w = frame.shape[:2]

        # Build matching 2D / 3D point arrays from available landmarks.
        pts_2d: list[list[float]] = []
        pts_3d: list[list[float]] = []

        for order_list, model_pts in [
            (_LANDMARK_ORDER_FULL, FACE_3D_MODEL),
            (_LANDMARK_ORDER_MINIMAL, FACE_3D_MODEL_MINIMAL),
        ]:
            pts_2d_tmp: list[list[float]] = []
            pts_3d_tmp: list[list[float]] = []
            for idx, name in enumerate(order_list):
                if name in face_landmarks:
                    lx, ly = face_landmarks[name]
                    pts_2d_tmp.append([lx * w, ly * h])
                    pts_3d_tmp.append(model_pts[idx].tolist())
            if len(pts_2d_tmp) >= 3:
                pts_2d = pts_2d_tmp
                pts_3d = pts_3d_tmp
                break

        if len(pts_2d) < 3:
            return {
                "gaze_yaw": 0.0,
                "gaze_pitch": 0.0,
                "gaze_vector": [0.0, 0.0, -1.0],
                "confidence": 0.0,
            }

        image_points = np.array(pts_2d, dtype=np.float64)
        model_points = np.array(pts_3d, dtype=np.float64)

        # Approximate camera matrix (focal length ~ frame width).
        focal_length = float(w)
        cx, cy = w / 2.0, h / 2.0
        camera_matrix = np.array(
            [
                [focal_length, 0.0, cx],
                [0.0, focal_length, cy],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        dist_coeffs = np.zeros((4, 1), dtype=np.float64)

        success, rvec, tvec = cv2.solvePnP(
            model_points,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )

        if not success:
            return {
                "gaze_yaw": 0.0,
                "gaze_pitch": 0.0,
                "gaze_vector": [0.0, 0.0, -1.0],
                "confidence": 0.0,
            }

        # Convert rotation vector → rotation matrix → Euler angles.
        rmat, _ = cv2.Rodrigues(rvec)

        # Decompose to yaw / pitch / roll.
        # Using the convention: yaw around Y, pitch around X.
        sy = np.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
        if sy > 1e-6:
            pitch = float(np.arctan2(-rmat[2, 0], sy))
            yaw = float(np.arctan2(rmat[1, 0], rmat[0, 0]))
        else:
            pitch = float(np.arctan2(-rmat[2, 0], sy))
            yaw = 0.0

        # Approximate gaze as a unit vector from yaw/pitch.
        gaze_x = float(-np.sin(yaw) * np.cos(pitch))
        gaze_y = float(np.sin(pitch))
        gaze_z = float(-np.cos(yaw) * np.cos(pitch))
        norm = float(np.sqrt(gaze_x**2 + gaze_y**2 + gaze_z**2))
        if norm > 1e-9:
            gaze_x /= norm
            gaze_y /= norm
            gaze_z /= norm

        confidence = min(1.0, len(pts_2d) / 6.0)

        return {
            "gaze_yaw": yaw,
            "gaze_pitch": pitch,
            "gaze_vector": [gaze_x, gaze_y, gaze_z],
            "confidence": confidence,
        }
