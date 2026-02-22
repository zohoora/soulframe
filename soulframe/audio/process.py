"""
Audio process — the main entry point that runs in a child process,
owns the sounddevice output stream, and reacts to commands arriving
on a multiprocessing queue.
"""

from __future__ import annotations

import logging
import time
from multiprocessing import Queue
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import sounddevice as sd

from soulframe import config
from soulframe.shared.types import Command, CommandType
from soulframe.audio.audio_stream import AudioStream
from soulframe.audio.mixer import AudioMixer

logger = logging.getLogger(__name__)

# Default fade durations (milliseconds).
_FADE_IN_MS = 500.0
_FADE_OUT_MS = 800.0

# How long (seconds) the main loop blocks on the command queue each
# iteration.
_QUEUE_TIMEOUT = 0.05  # 50 ms


# ------------------------------------------------------------------
# Device discovery
# ------------------------------------------------------------------

def _find_output_device(substring: str) -> Optional[int]:
    """Return the device index whose name contains *substring* (case-
    insensitive), or ``None`` if no match is found."""
    devices = sd.query_devices()
    sub_lower = substring.lower()
    for idx, dev in enumerate(devices):
        if sub_lower in dev["name"].lower() and dev["max_output_channels"] >= 2:
            logger.info("Found audio device '%s' at index %d", dev["name"], idx)
            return idx
    return None


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

def run_audio_process(cmd_queue: Queue) -> None:
    """Main loop executed inside a child process.

    Parameters
    ----------
    cmd_queue:
        A :class:`multiprocessing.Queue` through which the parent sends
        :class:`Command` objects.
    """
    logger.info("Audio process starting")

    mixer = AudioMixer()

    # Cache of loaded AudioStream objects keyed by (file_path, bass_boost).
    _stream_cache: Dict[tuple, AudioStream] = {}

    # ------------------------------------------------------------------
    # Resolve the output device
    # ------------------------------------------------------------------
    device_index = _find_output_device(config.AUDIO_DEVICE_NAME)
    if device_index is not None:
        logger.info("Using ReSpeaker device index %d", device_index)
    else:
        logger.warning(
            "ReSpeaker device '%s' not found; falling back to default output",
            config.AUDIO_DEVICE_NAME,
        )

    # ------------------------------------------------------------------
    # sounddevice callback
    # ------------------------------------------------------------------
    def _audio_callback(
        outdata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        mixed = mixer.mix(frames)
        outdata[:] = mixed

    # ------------------------------------------------------------------
    # Open the output stream
    # ------------------------------------------------------------------
    try:
        stream = sd.OutputStream(
            samplerate=config.AUDIO_SAMPLE_RATE,
            channels=config.AUDIO_CHANNELS,
            blocksize=config.AUDIO_BLOCK_SIZE,
            dtype="float32",
            device=device_index,
            callback=_audio_callback,
        )
        stream.start()
        logger.info("Audio output stream opened and started")
    except Exception:
        logger.exception("Failed to open audio output stream")
        return

    # ------------------------------------------------------------------
    # Helper: get or create a cached AudioStream
    # ------------------------------------------------------------------
    def _get_or_create_stream(
        file_path: str,
        loop: bool = True,
        bass_boost: bool = False,
    ) -> AudioStream:
        key = (file_path, bass_boost)
        cached = _stream_cache.get(key)
        if cached is not None:
            cached.reset()
            return cached
        new_stream = AudioStream(file_path, loop=loop, bass_boost=bass_boost)
        _stream_cache[key] = new_stream
        return new_stream

    # ------------------------------------------------------------------
    # Command handling
    # ------------------------------------------------------------------
    def _handle_command(cmd: Command) -> bool:
        """Process a single command.  Returns ``False`` when the process
        should shut down."""
        try:
            ct = cmd.cmd_type
            params = cmd.params if cmd.params else {}

            # -- PLAY_AMBIENT --------------------------------------------------
            if ct == CommandType.PLAY_AMBIENT:
                file_path = params.get("file_path", "")
                if not file_path:
                    logger.error("PLAY_AMBIENT missing 'file_path' param")
                    return True
                fade_ms = float(params.get("fade_ms", _FADE_IN_MS))
                audio = _get_or_create_stream(file_path, loop=True, bass_boost=False)
                audio.set_volume(0.0)
                mixer.add_stream("ambient", audio)
                audio.set_fade(1.0, fade_ms)
                logger.info("Playing ambient: %s", file_path)

            # -- STOP_AMBIENT --------------------------------------------------
            elif ct == CommandType.STOP_AMBIENT:
                fade_ms = float(params.get("fade_ms", _FADE_OUT_MS))
                ambient = mixer.get_stream("ambient")
                if ambient is not None:
                    ambient.set_fade(0.0, fade_ms)
                    # The stream will be removed after the fade completes
                    # during the cleanup sweep (see below).
                    logger.info("Fading out ambient")

            # -- PLAY_HEARTBEAT ------------------------------------------------
            elif ct == CommandType.PLAY_HEARTBEAT:
                file_path = params.get("file_path", "")
                region_id = params.get("region_id", "default")
                if not file_path:
                    logger.error("PLAY_HEARTBEAT missing 'file_path' param")
                    return True
                fade_ms = float(params.get("fade_ms", _FADE_IN_MS))
                stream_name = f"heartbeat_{region_id}"
                audio = _get_or_create_stream(file_path, loop=True, bass_boost=True)
                audio.set_volume(0.0)
                mixer.add_stream(stream_name, audio)
                audio.set_fade(1.0, fade_ms)
                logger.info("Playing heartbeat '%s': %s", stream_name, file_path)

            # -- STOP_HEARTBEAT ------------------------------------------------
            elif ct == CommandType.STOP_HEARTBEAT:
                region_id = params.get("region_id", "default")
                fade_ms = float(params.get("fade_ms", _FADE_OUT_MS))
                stream_name = f"heartbeat_{region_id}"
                hb = mixer.get_stream(stream_name)
                if hb is not None:
                    hb.set_fade(0.0, fade_ms)
                    logger.info("Fading out heartbeat '%s'", stream_name)

            # -- SET_VOLUME ----------------------------------------------------
            elif ct == CommandType.SET_VOLUME:
                name = params.get("name", "")
                volume = float(params.get("volume", 1.0))
                s = mixer.get_stream(name)
                if s is not None:
                    s.set_volume(volume)
                    logger.debug("Set volume of '%s' to %.2f", name, volume)
                else:
                    logger.warning("SET_VOLUME: stream '%s' not found", name)

            # -- FADE_ALL ------------------------------------------------------
            elif ct == CommandType.FADE_ALL:
                target = float(params.get("target_volume", 0.0))
                fade_ms = float(params.get("fade_ms", _FADE_OUT_MS))
                mixer.fade_all(target, fade_ms)
                logger.info("Fading all streams to %.2f over %.0f ms", target, fade_ms)

            # -- STOP_ALL ------------------------------------------------------
            elif ct == CommandType.STOP_ALL:
                mixer.stop_all()
                _stream_cache.clear()
                logger.info("All streams stopped")

            # -- SHUTDOWN ------------------------------------------------------
            elif ct == CommandType.SHUTDOWN:
                logger.info("Shutdown command received")
                mixer.stop_all()
                _stream_cache.clear()
                return False

            else:
                logger.warning("Unhandled command type: %s", ct)

        except Exception:
            logger.exception("Error handling command %s", cmd)

        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    running = True
    last_time = time.monotonic()

    try:
        while running:
            # --- Process pending commands ------------------------------------
            try:
                cmd = cmd_queue.get(timeout=_QUEUE_TIMEOUT)
                running = _handle_command(cmd)
            except Exception:
                # queue.Empty is expected on timeout; any other error we log.
                pass

            # --- Advance fades -----------------------------------------------
            now = time.monotonic()
            dt = now - last_time
            last_time = now
            mixer.update(dt)

            # --- Clean up silent streams that finished fading out ------------
            # We collect names first, then remove outside the mixer lock to
            # avoid modifying the dict while iterating.
            _to_remove = []
            for name in list(mixer._streams.keys()):
                s = mixer.get_stream(name)
                if s is not None and not s.is_active and s.current_volume <= 0.0:
                    _to_remove.append(name)
            for name in _to_remove:
                mixer.remove_stream(name)
                logger.debug("Cleaned up silent stream '%s'", name)

    except KeyboardInterrupt:
        logger.info("Audio process interrupted")
    except Exception:
        logger.exception("Unhandled exception in audio process main loop")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            logger.exception("Error closing audio stream")
        logger.info("Audio process exiting")
