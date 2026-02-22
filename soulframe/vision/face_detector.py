"""
Face detection with automatic backend selection.

Preferred backend: MediaPipe face detection (6 keypoints).
Fallback backend: OpenCV YuNet face detector (5 keypoints).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from soulframe import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Try to import MediaPipe — the preferred backend.
# ---------------------------------------------------------------------------
_MEDIAPIPE_AVAILABLE = False
try:
    import mediapipe as mp  # type: ignore[import-untyped]

    _MEDIAPIPE_AVAILABLE = True
except ImportError:
    logger.info("MediaPipe not available; will fall back to YuNet.")


class FaceDetector:
    """Detect faces and extract named landmarks from a BGR frame."""

    def __init__(self, min_confidence: float = config.FACE_DETECTION_CONFIDENCE):
        self._min_confidence = min_confidence
        self._backend: str = "none"

        if _MEDIAPIPE_AVAILABLE:
            self._init_mediapipe()
        else:
            self._init_yunet()

    # ------------------------------------------------------------------
    # Backend initialisation
    # ------------------------------------------------------------------

    def _init_mediapipe(self) -> None:
        mp_face = mp.solutions.face_detection
        self._mp_detector = mp_face.FaceDetection(
            model_selection=0,
            min_detection_confidence=self._min_confidence,
        )
        self._backend = "mediapipe"
        logger.info("FaceDetector using MediaPipe backend.")

    def _init_yunet(self) -> None:
        # YuNet ships with OpenCV contrib (4.5.4+).  We create the
        # detector lazily on the first call to detect() because we need
        # the frame dimensions to initialise it.
        self._yunet_model_path = config.MODELS_DIR / "face_detection_yunet.onnx"
        if not self._yunet_model_path.exists():
            logger.warning(
                "YuNet model not found at %s — YuNet backend disabled.",
                self._yunet_model_path,
            )
            self._yunet_detector = None
            self._yunet_input_size = None
            self._backend = "none"
            return
        self._yunet_detector: Optional[cv2.FaceDetectorYN] = None
        self._yunet_input_size: Optional[Tuple[int, int]] = None
        self._backend = "yunet"
        logger.info("FaceDetector using YuNet backend.")

    def _ensure_yunet(self, width: int, height: int) -> cv2.FaceDetectorYN:
        if (
            self._yunet_detector is None
            or self._yunet_input_size != (width, height)
        ):
            self._yunet_detector = cv2.FaceDetectorYN.create(
                str(self._yunet_model_path),
                "",
                (width, height),
                self._min_confidence,
                0.3,  # NMS threshold
                5000,  # top-K
            )
            self._yunet_input_size = (width, height)
        return self._yunet_detector

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Detect faces in *frame* (BGR uint8).

        Returns a list of dicts, each with:
            bbox        — (x, y, w, h) in pixels
            confidence  — detection score 0-1
            landmarks   — dict of named points normalised to 0-1
        """
        if self._backend == "mediapipe":
            return self._detect_mediapipe(frame)
        elif self._backend == "yunet":
            return self._detect_yunet(frame)
        return []

    # ------------------------------------------------------------------
    # MediaPipe detection
    # ------------------------------------------------------------------

    def _detect_mediapipe(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._mp_detector.process(rgb)

        faces: List[Dict[str, Any]] = []
        if not results.detections:
            return faces

        for det in results.detections:
            score = det.score[0]
            if score < self._min_confidence:
                continue

            # Relative bounding box → pixel bbox
            rbb = det.location_data.relative_bounding_box
            x = max(0, int(rbb.xmin * w))
            y = max(0, int(rbb.ymin * h))
            bw = min(int(rbb.width * w), w - x)
            bh = min(int(rbb.height * h), h - y)

            # MediaPipe provides 6 keypoints (indices 0-5):
            #   0 right_eye, 1 left_eye, 2 nose_tip,
            #   3 mouth_center, 4 right_ear, 5 left_ear
            kp = det.location_data.relative_keypoints
            landmarks: Dict[str, Tuple[float, float]] = {}
            _mp_names = [
                "right_eye",
                "left_eye",
                "nose_tip",
                "mouth_center",
                "right_ear",
                "left_ear",
            ]
            for idx, name in enumerate(_mp_names):
                if idx < len(kp):
                    landmarks[name] = (float(kp[idx].x), float(kp[idx].y))

            faces.append(
                {
                    "bbox": (x, y, bw, bh),
                    "confidence": float(score),
                    "landmarks": landmarks,
                }
            )

        return faces

    # ------------------------------------------------------------------
    # YuNet detection
    # ------------------------------------------------------------------

    def _detect_yunet(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        h, w = frame.shape[:2]
        try:
            detector = self._ensure_yunet(w, h)
        except Exception:
            logger.warning("Failed to initialise YuNet detector.", exc_info=True)
            return []

        _, raw = detector.detect(frame)
        if raw is None:
            return []

        faces: List[Dict[str, Any]] = []
        for row in raw:
            if len(row) < 15:
                continue
            x = max(0, int(row[0]))
            y = max(0, int(row[1]))
            bw = min(int(row[2]), w - x)
            bh = min(int(row[3]), h - y)
            score = float(row[14])
            if score < self._min_confidence:
                continue

            # YuNet keypoints (pixel coords): right_eye, left_eye,
            # nose_tip, right_mouth, left_mouth
            landmarks: Dict[str, Tuple[float, float]] = {}
            _yunet_names = [
                "right_eye",
                "left_eye",
                "nose_tip",
                "right_mouth",
                "left_mouth",
            ]
            for idx, name in enumerate(_yunet_names):
                px = float(row[4 + idx * 2])
                py = float(row[5 + idx * 2])
                # Normalise to 0-1 range
                landmarks[name] = (px / w, py / h)

            # Synthesise mouth_center from the two mouth corners.
            if "right_mouth" in landmarks and "left_mouth" in landmarks:
                rm = landmarks["right_mouth"]
                lm = landmarks["left_mouth"]
                landmarks["mouth_center"] = ((rm[0] + lm[0]) / 2, (rm[1] + lm[1]) / 2)

            faces.append(
                {
                    "bbox": (x, y, bw, bh),
                    "confidence": score,
                    "landmarks": landmarks,
                }
            )

        return faces
