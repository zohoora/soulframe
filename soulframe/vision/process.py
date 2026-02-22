"""
Vision subprocess entry-point.

``run_vision_process(cmd_queue)`` is designed to be launched via
``multiprocessing.Process(target=run_vision_process, args=(q,))``.
It captures frames, detects faces, estimates gaze and distance,
maps gaze to screen coordinates, and writes the result to shared
memory at ~30 Hz.
"""

import logging
import time
from multiprocessing import Queue
from typing import Any

import numpy as np

from soulframe.shared.ipc import VisionShmWriter
from soulframe.shared.types import FaceData
from soulframe.vision.camera import CameraCapture
from soulframe.vision.distance_estimator import DistanceEstimator
from soulframe.vision.face_detector import FaceDetector
from soulframe.vision.gaze_estimator import GazeEstimator
from soulframe.vision.screen_mapper import ScreenMapper

logger = logging.getLogger(__name__)

# Target loop period (seconds).
_TARGET_PERIOD = 1.0 / 30.0


def _select_primary_face(faces: list[dict[str, Any]]) -> dict[str, Any]:
    """Pick the largest (closest) detected face."""
    return max(faces, key=lambda f: f["bbox"][2] * f["bbox"][3])


def run_vision_process(cmd_queue: Queue) -> None:  # type: ignore[type-arg]
    """Main vision loop — intended to run in a child process.

    Parameters:
        cmd_queue: a ``multiprocessing.Queue`` through which the parent
                   process can send commands (e.g. ``"SHUTDOWN"``).
    """
    logger.info("Vision process starting.")

    # -- Initialise components -----------------------------------------
    camera: CameraCapture | None = None
    shm_writer: VisionShmWriter | None = None

    try:
        camera = CameraCapture()
        detector = FaceDetector()
        gaze_estimator = GazeEstimator()
        distance_estimator = DistanceEstimator()
        screen_mapper = ScreenMapper()
        shm_writer = VisionShmWriter()
    except Exception:
        logger.exception("Failed to initialise vision components.")
        if camera is not None:
            camera.release()
        return

    logger.info("Vision pipeline ready — entering main loop.")

    # -- Main loop -----------------------------------------------------
    try:
        while True:
            loop_start = time.monotonic()

            # Check for commands from the parent process.
            try:
                while not cmd_queue.empty():
                    cmd = cmd_queue.get_nowait()
                    if cmd == "SHUTDOWN":
                        logger.info("SHUTDOWN command received.")
                        return
            except Exception:
                pass  # queue might raise on process teardown

            # 1. Grab frame
            success, frame = camera.read()
            if not success or frame is None:
                _sleep_remaining(loop_start)
                continue

            h, w = frame.shape[:2]

            # 2. Detect faces
            try:
                faces = detector.detect(frame)
            except Exception:
                logger.debug("Face detection error.", exc_info=True)
                faces = []

            if not faces:
                # Write zero-face data so brain knows there's no detection
                fc = getattr(run_vision_process, "_fc", 0) + 1
                run_vision_process._fc = fc
                try:
                    shm_writer.write(FaceData(
                        frame_counter=fc,
                        num_faces=0,
                        timestamp_ns=time.time_ns(),
                    ))
                except Exception:
                    pass
                _sleep_remaining(loop_start)
                continue

            face = _select_primary_face(faces)
            bbox = face["bbox"]
            landmarks = face["landmarks"]

            # 3. Estimate gaze
            try:
                gaze = gaze_estimator.estimate(frame, landmarks)
            except Exception:
                logger.debug("Gaze estimation error.", exc_info=True)
                gaze = {
                    "gaze_yaw": 0.0,
                    "gaze_pitch": 0.0,
                    "gaze_vector": [0.0, 0.0, -1.0],
                    "confidence": 0.0,
                }

            # 4. Estimate distance
            try:
                distance_cm = distance_estimator.estimate(
                    landmarks, bbox, w, h
                )
            except Exception:
                logger.debug("Distance estimation error.", exc_info=True)
                distance_cm = 0.0

            # 5. Map gaze to screen coordinates
            try:
                screen_x, screen_y = screen_mapper.map_gaze(
                    gaze["gaze_yaw"],
                    gaze["gaze_pitch"],
                    head_yaw=gaze["gaze_yaw"],
                    head_pitch=gaze["gaze_pitch"],
                )
            except Exception:
                logger.debug("Screen mapping error.", exc_info=True)
                screen_x, screen_y = 0.5, 0.5

            # 6. Pack into FaceData and write to shared memory
            frame_counter = getattr(run_vision_process, "_fc", 0) + 1
            run_vision_process._fc = frame_counter
            try:
                face_data = FaceData(
                    frame_counter=frame_counter,
                    num_faces=len(faces),
                    face_distance_cm=distance_cm,
                    gaze_screen_x=screen_x,
                    gaze_screen_y=screen_y,
                    gaze_confidence=gaze["confidence"],
                    head_yaw=gaze["gaze_yaw"],
                    head_pitch=gaze["gaze_pitch"],
                    timestamp_ns=time.time_ns(),
                )
                shm_writer.write(face_data)
            except Exception:
                logger.debug("SHM write error.", exc_info=True)

            _sleep_remaining(loop_start)

    except KeyboardInterrupt:
        logger.info("Vision process interrupted.")
    except Exception:
        logger.exception("Unhandled error in vision loop.")
    finally:
        # -- Clean-up --------------------------------------------------
        logger.info("Vision process shutting down.")
        if camera is not None:
            try:
                camera.release()
            except Exception:
                pass
        if shm_writer is not None:
            try:
                shm_writer.close()
            except Exception:
                pass


def _sleep_remaining(loop_start: float) -> None:
    """Sleep for the remainder of the target frame period."""
    elapsed = time.monotonic() - loop_start
    remaining = _TARGET_PERIOD - elapsed
    if remaining > 0:
        time.sleep(remaining)
