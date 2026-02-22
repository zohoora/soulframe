"""
Camera capture module with threaded frame acquisition.

Wraps OpenCV VideoCapture with a daemon thread that continuously reads
frames into a single-element deque, ensuring read() never blocks.
"""

import logging
import threading
from collections import deque
from typing import Optional, Tuple

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

        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_buffer: deque = deque(maxlen=1)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame_seq: int = 0           # incremented by capture thread
        self._last_read_seq: int = -1      # last sequence the consumer saw

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

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _capture_loop(self) -> None:
        """Continuously grab frames and push the latest into the buffer."""
        while not self._stop_event.is_set():
            if self._cap is None:
                break
            try:
                ret, frame = self._cap.read()
                if ret:
                    self._frame_buffer.append((self._frame_seq, frame))
                    self._frame_seq = (self._frame_seq + 1) & 0xFFFFFFFF
                else:
                    logger.debug("Camera read returned False.")
                    self._stop_event.wait(0.03)  # avoid CPU spin on failure
            except Exception:
                logger.exception("Error in camera capture loop")
                self._stop_event.wait(0.1)  # back off on repeated errors

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Return the most recent frame without removing it from the buffer.

        Returns:
            (success, frame) — *success* is False when no frame is
            available (camera missing or not yet ready).
        """
        if self._cap is None:
            return False, None

        try:
            seq, frame = self._frame_buffer[-1]
            if seq == self._last_read_seq:
                return False, None  # same frame already processed
            self._last_read_seq = seq
            return True, frame
        except IndexError:
            return False, None

    def release(self) -> None:
        """Stop the capture thread and release the camera."""
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Camera released.")
