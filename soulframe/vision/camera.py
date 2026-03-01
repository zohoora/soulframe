"""
Camera capture module with threaded frame acquisition.

On Jetson (detected automatically), uses nvarguscamerasrc via GStreamer to
route frames through the hardware ISP — required for the GMSL2 IMX219 Bayer
sensor.  Falls back to plain V4L2 VideoCapture for USB webcams or non-Jetson
hosts.  The fallback can be forced with SOULFRAME_CAMERA_FORCE_V4L2=1.
"""

import logging
import threading
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from soulframe import config

logger = logging.getLogger(__name__)


def _is_jetson() -> bool:
    """Return True if we are running on a Jetson (L4T / Tegra)."""
    try:
        return "tegra" in Path("/proc/device-tree/compatible").read_text()
    except OSError:
        return False


def _build_gstreamer_pipeline(
    sensor_id: int,
    capture_width: int,
    capture_height: int,
    capture_fps: int,
    output_width: int,
    output_height: int,
    flip_method: int,
) -> str:
    """Build an nvarguscamerasrc GStreamer pipeline string.

    Pipeline stages:
      nvarguscamerasrc  → Argus/ISP capture into NVMM memory
      nvvidconv         → hardware resize + colorspace (NVMM → system RAM)
      videoconvert      → BGRx → BGR for OpenCV
      appsink           → delivers frames, drops stale buffers
    """
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} ! "
        f"video/x-raw(memory:NVMM), "
        f"width=(int){capture_width}, height=(int){capture_height}, "
        f"framerate=(fraction){capture_fps}/1, format=(string)NV12 ! "
        f"nvvidconv flip-method={flip_method} ! "
        f"video/x-raw, width=(int){output_width}, height=(int){output_height}, "
        f"format=(string)BGRx ! "
        f"videoconvert ! "
        f"video/x-raw, format=(string)BGR ! "
        f"appsink drop=1 max-buffers=1"
    )


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
        self._using_gstreamer = False

        self._open()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open(self) -> None:
        """Open the camera device and start the capture thread."""
        if _is_jetson() and not config.CAMERA_FORCE_V4L2:
            self._open_gstreamer()
        else:
            self._open_v4l2()

        if self._cap is None or not self._cap.isOpened():
            logger.warning("Camera could not be opened via any backend.")
            self._cap = None
            return

        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self._cap.get(cv2.CAP_PROP_FPS)
        backend = "GStreamer/nvarguscamerasrc" if self._using_gstreamer else "V4L2"
        logger.info(
            "Camera opened (%s): device=%d  output=%dx%d  fps=%.1f",
            backend, self._device_index, actual_w, actual_h, actual_fps,
        )

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def _open_gstreamer(self) -> None:
        """Try opening the camera via nvarguscamerasrc (Jetson ISP path)."""
        pipeline = _build_gstreamer_pipeline(
            sensor_id=self._device_index,
            capture_width=config.CAMERA_CAPTURE_WIDTH,
            capture_height=config.CAMERA_CAPTURE_HEIGHT,
            capture_fps=config.CAMERA_CAPTURE_FPS,
            output_width=self._width,
            output_height=self._height,
            flip_method=config.CAMERA_FLIP_METHOD,
        )
        logger.info("Opening camera via GStreamer: %s", pipeline)
        self._cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

        if self._cap.isOpened():
            self._using_gstreamer = True
        else:
            logger.warning(
                "GStreamer pipeline failed — falling back to V4L2. "
                "Is nvargus-daemon running?  (sudo systemctl restart nvargus-daemon)"
            )
            self._cap = None
            self._open_v4l2()

    def _open_v4l2(self) -> None:
        """Open the camera via plain V4L2 (USB webcam fallback)."""
        logger.info("Opening camera via V4L2: device=%d", self._device_index)
        self._cap = cv2.VideoCapture(self._device_index)

        if self._cap is not None and self._cap.isOpened():
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)
            self._using_gstreamer = False

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
