"""
AudioStream — a single loopable audio source with volume fading and
optional bass-boost EQ.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

import numpy as np
import soundfile as sf
from scipy.signal import sosfilt

from soulframe import config

logger = logging.getLogger(__name__)


def _design_bass_boost_filter(
    center_hz: float,
    q: float,
    gain_db: float,
    sample_rate: int,
) -> np.ndarray:
    """Design a parametric peak (bell) EQ filter using Audio EQ Cookbook biquads.

    Returns second-order sections (SOS) array for use with sosfilt.
    """
    A = 10.0 ** (gain_db / 40.0)  # amplitude
    w0 = 2.0 * np.pi * center_hz / sample_rate
    sin_w0 = np.sin(w0)
    cos_w0 = np.cos(w0)
    alpha = sin_w0 / (2.0 * q)

    b0 = 1.0 + alpha * A
    b1 = -2.0 * cos_w0
    b2 = 1.0 - alpha * A
    a0 = 1.0 + alpha / A
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha / A

    # Normalize by a0 and pack into SOS format: [b0, b1, b2, 1, a1/a0, a2/a0]
    sos = np.array([[b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]], dtype=np.float64)
    return sos


class AudioStream:
    """A single audio source that can loop, fade, and optionally apply a
    bass-boost EQ filter to its sample data."""

    def __init__(
        self,
        file_path: Union[str, Path],
        loop: bool = True,
        bass_boost: bool = False,
    ) -> None:
        self._file_path = Path(file_path)
        self._loop = loop

        # -- Load audio data --------------------------------------------------
        data, sr = sf.read(str(self._file_path), dtype="float32", always_2d=True)

        # Resample warning (actual resampling is out of scope; we just log).
        if sr != config.AUDIO_SAMPLE_RATE:
            logger.warning(
                "Sample-rate mismatch: file %s is %d Hz, output is %d Hz — "
                "playback will be pitch-shifted",
                self._file_path.name, sr, config.AUDIO_SAMPLE_RATE,
            )

        # Ensure stereo.
        if data.shape[1] == 1:
            data = np.column_stack((data[:, 0], data[:, 0]))
        elif data.shape[1] > 2:
            data = data[:, :2]

        # -- Optional bass boost -----------------------------------------------
        if bass_boost:
            try:
                sos = _design_bass_boost_filter(
                    center_hz=config.HEARTBEAT_BASS_CENTER_HZ,
                    q=config.HEARTBEAT_BASS_Q,
                    gain_db=config.HEARTBEAT_BASS_GAIN_DB,
                    sample_rate=sr,  # Use actual file sample rate, not config
                )
                for ch in range(data.shape[1]):
                    data[:, ch] = sosfilt(sos, data[:, ch]).astype(np.float32)
                logger.debug("Bass boost applied to %s", self._file_path.name)
            except Exception:
                logger.exception("Failed to apply bass boost to %s", self._file_path.name)

        self._data: np.ndarray = data  # shape (N, 2), float32
        self._num_frames: int = data.shape[0]

        # -- Playback state ----------------------------------------------------
        self._position: int = 0
        self._finished: bool = self._num_frames == 0

        if self._num_frames == 0:
            logger.warning("Audio file has zero frames: %s", self._file_path.name)

        # -- Volume / fade state -----------------------------------------------
        self._volume: float = 0.0
        self._fade_target: float = 0.0
        self._fade_rate: float = 0.0  # volume units per second
        self._fading: bool = False

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def get_samples(self, num_frames: int) -> np.ndarray:
        """Return the next *num_frames* of stereo audio data.

        Returns a float32 array of shape ``(num_frames, 2)``.
        """
        out = np.zeros((num_frames, 2), dtype=np.float32)

        # Guard: zero-frame files can never produce samples
        if self._num_frames == 0:
            self._finished = True
            return out

        remaining = num_frames
        write_pos = 0

        while remaining > 0:
            available = self._num_frames - self._position
            if available <= 0:
                if self._loop:
                    self._position = 0
                    available = self._num_frames
                else:
                    self._finished = True
                    break  # not looping — leave the rest as zeros

            chunk = min(remaining, available)
            out[write_pos: write_pos + chunk] = self._data[
                self._position: self._position + chunk
            ]
            self._position += chunk
            write_pos += chunk
            remaining -= chunk

            # Wrap if looping and we've exhausted the buffer.
            if self._position >= self._num_frames and self._loop:
                self._position = 0

        return out

    # ------------------------------------------------------------------
    # Volume / Fade
    # ------------------------------------------------------------------

    def set_volume(self, volume: float) -> None:
        """Immediately set the playback volume (0.0 -- 1.0)."""
        self._volume = max(0.0, min(1.0, float(volume)))
        self._fade_target = self._volume
        self._fading = False

    def set_fade(self, target_volume: float, duration_ms: float) -> None:
        """Begin a smooth volume transition over *duration_ms* milliseconds."""
        target_volume = max(0.0, min(1.0, float(target_volume)))
        if duration_ms <= 0:
            self.set_volume(target_volume)
            return
        # Already at target — no fade needed.
        if abs(self._volume - target_volume) < 1e-6:
            self._volume = target_volume
            self._fade_target = target_volume
            self._fading = False
            return
        self._fade_target = target_volume
        duration_s = duration_ms / 1000.0
        self._fade_rate = (self._fade_target - self._volume) / duration_s
        self._fading = True

    def update(self, dt: float) -> None:
        """Advance the fade animation by *dt* seconds."""
        if not self._fading:
            return
        self._volume += self._fade_rate * dt
        # Check if we've reached (or overshot) the target.
        if self._fade_rate > 0 and self._volume >= self._fade_target:
            self._volume = self._fade_target
            self._fading = False
        elif self._fade_rate < 0 and self._volume <= self._fade_target:
            self._volume = self._fade_target
            self._fading = False
        # Safety clamp.
        self._volume = max(0.0, min(1.0, self._volume))

    @property
    def current_volume(self) -> float:
        """The current effective volume, including fade state."""
        return self._volume

    @property
    def is_fading(self) -> bool:
        """``True`` if a fade animation is currently in progress."""
        return self._fading

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Restart playback from the beginning of the audio data."""
        self._position = 0
        self._finished = self._num_frames == 0

    @property
    def is_active(self) -> bool:
        """``True`` if the stream is audible or in the process of fading in."""
        if self._finished:
            return False
        if self._volume > 0.0:
            return True
        if self._fading and self._fade_target > 0.0:
            return True
        return False

    def __repr__(self) -> str:
        return (
            f"<AudioStream '{self._file_path.name}' "
            f"loop={self._loop} vol={self._volume:.2f} "
            f"pos={self._position}/{self._num_frames}>"
        )
