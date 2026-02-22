"""
AudioStream — a single loopable audio source with volume fading and
optional bass-boost EQ.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import sosfilt, iirpeak

from soulframe import config

logger = logging.getLogger(__name__)


def _design_bass_boost_filter(
    center_hz: float,
    q: float,
    gain_db: float,
    sample_rate: int,
) -> np.ndarray:
    """Design a second-order peak (bell) filter and return it as SOS.

    Uses ``scipy.signal.iirpeak`` to build a narrow peak centred on
    *center_hz*.  The resulting filter is scaled so that it adds
    *gain_db* of boost at the centre frequency.
    """
    # iirpeak returns (b, a) for a notch-style peak; we convert to SOS.
    w0 = center_hz / (sample_rate / 2.0)  # normalised frequency 0..1
    # Clamp to valid range for the filter design.
    w0 = float(np.clip(w0, 1e-6, 1.0 - 1e-6))
    b, a = iirpeak(w0, q)

    # iirpeak produces a unit-gain resonance.  Scale numerator to reach
    # the desired boost.  linear gain = 10^(dB/20).
    linear_gain = 10.0 ** (gain_db / 20.0)
    # The peak filter has unity gain at DC; we want *additional* boost at
    # the centre.  A simple approach: blend the dry (pass-through) signal
    # with the peak-filtered signal so that the centre frequency gets the
    # full gain_db boost.
    #   out = dry + (gain - 1) * peaked
    # This is baked into b so we can apply once with sosfilt.
    b_boosted = np.array([1.0, 0.0, 0.0]) + (linear_gain - 1.0) * b
    # Pack into a single SOS section: [b0, b1, b2, a0, a1, a2]
    sos = np.array([[b_boosted[0], b_boosted[1], b_boosted[2],
                     a[0], a[1], a[2]]], dtype=np.float64)
    return sos


class AudioStream:
    """A single audio source that can loop, fade, and optionally apply a
    bass-boost EQ filter to its sample data."""

    def __init__(
        self,
        file_path: str | Path,
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
                "Sample-rate mismatch: file %s is %d Hz, expected %d Hz",
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
                    sample_rate=config.AUDIO_SAMPLE_RATE,
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
        remaining = num_frames
        write_pos = 0

        while remaining > 0:
            available = self._num_frames - self._position
            if available <= 0:
                if self._loop:
                    self._position = 0
                    available = self._num_frames
                else:
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

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Restart playback from the beginning of the audio data."""
        self._position = 0

    @property
    def is_active(self) -> bool:
        """``True`` if the stream is audible or in the process of fading in."""
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
