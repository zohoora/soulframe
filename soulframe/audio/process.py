"""
Audio process — the main entry point that runs in a child process,
owns the sounddevice output stream, and reacts to commands arriving
on a multiprocessing queue.
"""

from __future__ import annotations

import logging
import queue
import time
from multiprocessing import Queue
from pathlib import Path
from typing import Optional

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

    # AudioStream objects are created fresh each time

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
        try:
            mixed = mixer.mix(frames, sample_rate=config.AUDIO_SAMPLE_RATE)
            outdata[:] = mixed
        except Exception:
            outdata[:] = 0.0
            logger.debug("Audio mix error, outputting silence", exc_info=True)

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
    # Helper: create a fresh AudioStream
    # ------------------------------------------------------------------
    def _create_stream(
        file_path: str,
        loop: bool = True,
        bass_boost: bool = False,
    ) -> AudioStream:
        return AudioStream(file_path, loop=loop, bass_boost=bass_boost)

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
                loop = params.get("loop", True)
                audio = _create_stream(file_path, loop=loop, bass_boost=False)
                audio.set_volume(0.0)
                mixer.add_stream("ambient", audio)
                mixer.set_stream_fade("ambient", 1.0, fade_ms)
                logger.info("Playing ambient: %s", file_path)

            # -- STOP_AMBIENT --------------------------------------------------
            elif ct == CommandType.STOP_AMBIENT:
                fade_ms = float(params.get("fade_ms", _FADE_OUT_MS))
                if mixer.set_stream_fade("ambient", 0.0, fade_ms):
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
                loop = params.get("loop", True)
                bass_boost = params.get("bass_boost", True)
                audio = _create_stream(file_path, loop=loop, bass_boost=bass_boost)
                audio.set_volume(0.0)
                mixer.add_stream(stream_name, audio)
                mixer.set_stream_fade(stream_name, 1.0, fade_ms)
                logger.info("Playing heartbeat '%s': %s", stream_name, file_path)

            # -- STOP_HEARTBEAT ------------------------------------------------
            elif ct == CommandType.STOP_HEARTBEAT:
                region_id = params.get("region_id", "default")
                fade_ms = float(params.get("fade_ms", _FADE_OUT_MS))
                stream_name = f"heartbeat_{region_id}"
                if mixer.set_stream_fade(stream_name, 0.0, fade_ms):
                    logger.info("Fading out heartbeat '%s'", stream_name)

            # -- SET_VOLUME ----------------------------------------------------
            elif ct == CommandType.SET_VOLUME:
                name = params.get("name", "")
                volume = float(params.get("volume", 1.0))
                if mixer.set_stream_volume(name, volume):
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
                logger.info("All streams stopped")

            # -- SHUTDOWN ------------------------------------------------------
            elif ct == CommandType.SHUTDOWN:
                logger.info("Shutdown command received")
                mixer.stop_all()
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
            except queue.Empty:
                pass
            except Exception:
                logger.exception("Error processing command")

            # --- Tick timing (fades advance inside mixer.mix() on the
            #     sounddevice callback thread) --------------------------------
            now = time.monotonic()
            last_time = now

            # --- Clean up silent streams that finished fading out ------------
            mixer.remove_inactive()

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
