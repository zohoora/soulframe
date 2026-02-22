"""Shared memory struct and IPC helpers for Soul Frame.

Vision process writes a 40-byte struct at ~30 Hz.
Brain reads it to drive the state machine.
"""

import struct
import time
from multiprocessing import shared_memory
from typing import Optional

from soulframe.shared.types import FaceData
from soulframe import config

# Struct layout: see architecture spec
# Total: 40 bytes
_STRUCT_FMT = "<IIffffffQ"
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)  # 40

assert _STRUCT_SIZE == config.VISION_SHM_SIZE, (
    f"Struct size mismatch: {_STRUCT_SIZE} != {config.VISION_SHM_SIZE}"
)


class VisionShmWriter:
    """Writes vision data into shared memory (used by vision process)."""

    def __init__(self) -> None:
        try:
            # Clean up any stale segment from a previous run
            old = shared_memory.SharedMemory(name=config.VISION_SHM_NAME, create=False)
            old.close()
            old.unlink()
        except FileNotFoundError:
            pass
        self._shm = shared_memory.SharedMemory(
            name=config.VISION_SHM_NAME, create=True, size=_STRUCT_SIZE
        )

    def write(self, data: FaceData) -> None:
        packed = struct.pack(
            _STRUCT_FMT,
            data.frame_counter,
            data.num_faces,
            data.face_distance_cm,
            data.gaze_screen_x,
            data.gaze_screen_y,
            data.gaze_confidence,
            data.head_yaw,
            data.head_pitch,
            data.timestamp_ns or time.time_ns(),
        )
        self._shm.buf[:_STRUCT_SIZE] = packed

    def close(self) -> None:
        self._shm.close()
        try:
            self._shm.unlink()
        except FileNotFoundError:
            pass


class VisionShmReader:
    """Reads vision data from shared memory (used by brain process)."""

    def __init__(self) -> None:
        self._shm: Optional[shared_memory.SharedMemory] = None
        self._last_frame: int = 0

    def connect(self, timeout: float = 10.0) -> bool:
        """Attempt to attach to vision shared memory segment."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._shm = shared_memory.SharedMemory(
                    name=config.VISION_SHM_NAME, create=False
                )
                return True
            except FileNotFoundError:
                time.sleep(0.1)
        return False

    def read(self) -> Optional[FaceData]:
        """Read latest vision data. Returns None if no new frame."""
        if self._shm is None:
            return None
        values = struct.unpack(_STRUCT_FMT, bytes(self._shm.buf[:_STRUCT_SIZE]))
        frame_counter = values[0]
        if frame_counter == self._last_frame:
            return None  # no new data
        self._last_frame = frame_counter
        return FaceData(
            frame_counter=values[0],
            num_faces=values[1],
            face_distance_cm=values[2],
            gaze_screen_x=values[3],
            gaze_screen_y=values[4],
            gaze_confidence=values[5],
            head_yaw=values[6],
            head_pitch=values[7],
            timestamp_ns=values[8],
        )

    def close(self) -> None:
        if self._shm is not None:
            self._shm.close()
            self._shm = None
