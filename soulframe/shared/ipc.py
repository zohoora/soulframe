"""Shared memory struct and IPC helpers for Soul Frame.

Vision process writes a 40-byte struct at ~30 Hz.
Brain reads it to drive the state machine.

A simple seqlock prevents torn reads on architectures where a 40-byte
memcpy is not atomic (e.g. aarch64/Jetson).
"""

import ctypes
import struct
import time
from multiprocessing import shared_memory
from typing import Optional

from soulframe.shared.types import FaceData
from soulframe import config

# Memory fence for cross-process seqlock correctness on weakly-ordered
# architectures (e.g. aarch64/Jetson).
try:
    _libc = ctypes.CDLL(None)
    _libc.__sync_synchronize.restype = None
    _libc.__sync_synchronize.argtypes = []

    def _memory_fence() -> None:
        """Full memory barrier via GCC __sync_synchronize (dmb on ARM)."""
        _libc.__sync_synchronize()

except (OSError, AttributeError):
    _fence_loc = ctypes.c_uint32(0)

    def _memory_fence() -> None:
        """Fallback fence via volatile-style ctypes write."""
        _fence_loc.value = _fence_loc.value

# Data struct layout (unchanged from spec).
_STRUCT_FMT = "<IIffffffQ"
_STRUCT_SIZE = struct.calcsize(_STRUCT_FMT)  # 40

# Seqlock: a single uint32 counter prepended to the data.
_SEQ_FMT = "<I"
_SEQ_SIZE = struct.calcsize(_SEQ_FMT)  # 4
_TOTAL_SHM_SIZE = _SEQ_SIZE + _STRUCT_SIZE  # 44


class VisionShmWriter:
    """Writes vision data into shared memory (used by vision process)."""

    def __init__(self) -> None:
        try:
            old = shared_memory.SharedMemory(name=config.VISION_SHM_NAME, create=False)
            old.close()
            old.unlink()
        except Exception:
            pass
        self._shm = shared_memory.SharedMemory(
            name=config.VISION_SHM_NAME, create=True, size=_TOTAL_SHM_SIZE
        )
        try:
            # Initialise seqlock counter to 0 (even = no write in progress).
            struct.pack_into(_SEQ_FMT, self._shm.buf, 0, 0)
        except Exception:
            self._shm.close()
            self._shm.unlink()
            raise
        self._seq: int = 0

    def write(self, data: FaceData) -> None:
        # Mark write-in-progress (odd).
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        struct.pack_into(_SEQ_FMT, self._shm.buf, 0, self._seq)
        _memory_fence()

        try:
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
            self._shm.buf[_SEQ_SIZE:_TOTAL_SHM_SIZE] = packed
        finally:
            _memory_fence()
            # Mark write-complete (even) — even on error, to avoid
            # permanently blocking readers with a stuck odd counter.
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            struct.pack_into(_SEQ_FMT, self._shm.buf, 0, self._seq)

    def close(self) -> None:
        self._shm.close()
        try:
            self._shm.unlink()
        except Exception:
            pass


class VisionShmReader:
    """Reads vision data from shared memory (used by brain process)."""

    def __init__(self) -> None:
        self._shm: Optional[shared_memory.SharedMemory] = None
        self._last_frame: Optional[int] = None

    def connect(self, timeout: float = 10.0) -> bool:
        """Attempt to attach to vision shared memory segment."""
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None

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
        """Read latest vision data. Returns None if no new frame or torn read."""
        if self._shm is None:
            return None

        try:
            # --- Seqlock read protocol ---
            seq1 = struct.unpack_from(_SEQ_FMT, self._shm.buf, 0)[0]
            if seq1 & 1:
                # Writer is mid-update — skip this cycle.
                return None
            _memory_fence()

            raw = bytes(self._shm.buf[_SEQ_SIZE:_TOTAL_SHM_SIZE])
            _memory_fence()

            seq2 = struct.unpack_from(_SEQ_FMT, self._shm.buf, 0)[0]
            if seq1 != seq2:
                # Data was modified during our read — torn read.
                return None
        except (BufferError, ValueError, OSError):
            # Shared memory segment was deallocated (vision process crashed).
            self._shm = None
            return None

        values = struct.unpack(_STRUCT_FMT, raw)
        frame_counter = values[0]
        if self._last_frame is not None and frame_counter == self._last_frame:
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
