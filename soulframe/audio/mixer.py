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

        If a stream with the same name already exists, the old stream
        is kept under a temporary name so it can fade out gracefully
        via remove_inactive().
        """
        with self._lock:
            old = self._streams.get(name)
            if old is not None and old.is_active:
                # Move old stream to a retiring slot so it fades out
                retire_name = f"_retiring_{name}_{id(old)}"
                old.set_fade(0.0, 200.0)  # quick fade-out
                self._streams[retire_name] = old
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

    def set_stream_fade(self, name: str, target_volume: float, duration_ms: float) -> bool:
        """Set a fade on a specific stream under the mixer lock.

        Returns True if the stream was found, False otherwise.
        """
        with self._lock:
            stream = self._streams.get(name)
            if stream is not None:
                stream.set_fade(target_volume, duration_ms)
                return True
        return False

    def set_stream_volume(self, name: str, volume: float) -> bool:
        """Set volume on a specific stream under the mixer lock.

        Returns True if the stream was found, False otherwise.
        """
        with self._lock:
            stream = self._streams.get(name)
            if stream is not None:
                stream.set_volume(volume)
                return True
        return False

    # ------------------------------------------------------------------
    # Mixing
    # ------------------------------------------------------------------

    def mix(self, num_frames: int, sample_rate: int = 44100) -> np.ndarray:
        """Produce a stereo float32 buffer of *num_frames* mixed samples.

        Also advances fade animations so all state mutations happen
        atomically on the callback thread.
        """
        buf = np.zeros((num_frames, 2), dtype=np.float32)
        dt = num_frames / sample_rate

        with self._lock:
            for stream in self._streams.values():
                stream.update(dt)
                if not stream.is_active:
                    continue
                vol = stream.current_volume
                if vol <= 0.0:
                    continue
                samples = stream.get_samples(num_frames)
                buf += samples * vol

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
        """No-op — fade advancement now happens inside mix()."""
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def stream_count(self) -> int:
        with self._lock:
            return len(self._streams)

    def remove_inactive(self) -> int:
        """Remove streams that have finished fading out. Returns count removed."""
        removed = 0
        with self._lock:
            to_remove = [
                name for name, stream in self._streams.items()
                if not stream.is_active and stream.current_volume <= 0.0
            ]
            for name in to_remove:
                del self._streams[name]
                removed += 1
        if removed:
            logger.debug("Removed %d inactive stream(s)", removed)
        return removed

    def __repr__(self) -> str:
        return (
            f"<AudioMixer streams={self.stream_count} "
            f"master={self._master_volume:.2f}>"
        )
