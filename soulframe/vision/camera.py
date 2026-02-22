"""
Camera capture module with threaded frame acquisition.

Wraps OpenCV VideoCapture with a daemon thread that continuously reads
frames into a single-element deque, ensuring read() never blocks.
"""

import logging
import threading
from collections import deque

import cv2
import numpy as np

from soulframe import config

logger = logging.getLogger(__name__)


class CameraCapture:
    """Threaded camera capture that keeps only the latest frame."""

    def __init__(
        self,
        device_index: int = config.CAMERA_INDEX,
        width: int = config.CAMERA_WIDTH,
        height: int = config.CAMERA_HEIGHT,
        fps: int = config.CAMERA_FPS,
    ):
        self._device_index = device_index
        self._width = width
        self._height = height
        self._fps = fps

        self._cap: cv2.VideoCapture | None = None
        self._frame_buffer: deque = deque(maxlen=1)
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        self._open()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open the camera device and start the capture thread."""
        self._cap = cv2.VideoCapture(self._device_index)

        if not self._cap.isOpened():
            logger.warning(
                "Camera device %d not found or could not be opened.",
                self._device_index,
            )
            self._cap = None
            return

        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        logger.info(
            "Camera opened: device=%d  resolution=%dx%d  fps=%.1f",
            self._device_index,
            actual_w,
            actual_h,
            actual_fps,
        )

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        """Continuously grab frames and push the latest into the buffer."""
        while self._running:
            if self._cap is None:
                break
            ret, frame = self._cap.read()
            if ret:
                self._frame_buffer.append(frame)
            else:
                logger.debug("Camera read returned False.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self) -> tuple[bool, np.ndarray | None]:
        """Return the most recent frame.

        Returns:
            (success, frame) — *success* is False when no frame is
            available (camera missing or not yet ready).
        """
        if self._cap is None:
            return False, None

        try:
            frame = self._frame_buffer.pop()
            return True, frame
        except IndexError:
            return False, None

    def release(self) -> None:
        """Stop the capture thread and release the camera."""
        self._running = False

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera released.")
