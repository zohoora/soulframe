"""
AudioMixer — sums multiple AudioStreams into a single stereo output buffer.

All mutations to the internal streams dictionary are guarded by a
threading lock so that the sounddevice callback (which runs on a
separate real-time thread) can safely call :meth:`mix` while the main
thread adds / removes streams.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

import numpy as np

from soulframe.audio.audio_stream import AudioStream

logger = logging.getLogger(__name__)


class AudioMixer:
    """Mix several named :class:`AudioStream` instances into one stereo buffer."""

    def __init__(self) -> None:
        self._streams: Dict[str, AudioStream] = {}
        self._lock = threading.Lock()
        self._master_volume: float = 1.0

    # ------------------------------------------------------------------
    # Stream management
    # ------------------------------------------------------------------

    def add_stream(self, name: str, stream: AudioStream) -> None:
        """Register *stream* under the given *name*.

        If a stream with the same name already exists it is silently
        replaced.
        """
        with self._lock:
            self._streams[name] = stream
            logger.debug("Added stream '%s': %r", name, stream)

    def remove_stream(self, name: str) -> None:
        """Remove and discard the stream identified by *name*.

        No error is raised if the name does not exist.
        """
        with self._lock:
            stream = self._streams.pop(name, None)
            if stream is not None:
                logger.debug("Removed stream '%s'", name)

    def get_stream(self, name: str) -> Optional[AudioStream]:
        """Return the :class:`AudioStream` registered as *name*, or ``None``."""
        with self._lock:
            return self._streams.get(name)

    # ------------------------------------------------------------------
    # Mixing
    # ------------------------------------------------------------------

    def mix(self, num_frames: int) -> np.ndarray:
        """Produce a stereo float32 buffer of *num_frames* mixed samples.

        The output is clipped to the -1.0 .. 1.0 range.
        """
        buf = np.zeros((num_frames, 2), dtype=np.float32)

        with self._lock:
            for stream in self._streams.values():
                if not stream.is_active:
                    continue
                samples = stream.get_samples(num_frames)
                buf += samples * stream.current_volume

        buf *= self._master_volume
        np.clip(buf, -1.0, 1.0, out=buf)
        return buf

    # ------------------------------------------------------------------
    # Master volume
    # ------------------------------------------------------------------

    def set_master_volume(self, volume: float) -> None:
        """Set the master output volume (0.0 -- 1.0)."""
        self._master_volume = max(0.0, min(1.0, float(volume)))

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    def fade_all(self, target_volume: float, duration_ms: float) -> None:
        """Start a fade on every currently registered stream."""
        with self._lock:
            for stream in self._streams.values():
                stream.set_fade(target_volume, duration_ms)

    def stop_all(self) -> None:
        """Immediately stop and remove every stream."""
        with self._lock:
            self._streams.clear()
            logger.debug("All streams stopped and removed")

    def update(self, dt: float) -> None:
        """Advance fade animations on all streams by *dt* seconds."""
        with self._lock:
            for stream in self._streams.values():
                stream.update(dt)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def stream_count(self) -> int:
        with self._lock:
            return len(self._streams)

    def __repr__(self) -> str:
        return (
            f"<AudioMixer streams={self.stream_count} "
            f"master={self._master_volume:.2f}>"
        )
