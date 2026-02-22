"""Brain coordinator for Soul Frame.

Spawns vision, display, and audio processes, then runs the brain loop that
reads shared-memory vision data, drives the interaction state machine, and
dispatches commands to the display and audio subsystems.
"""

import logging
import time
from multiprocessing import Process, Queue
from typing import List, Optional

from soulframe import config
from soulframe.shared.types import (
    Command,
    CommandType,
    FaceData,
    InteractionState,
)
from soulframe.shared.ipc import VisionShmReader
from soulframe.shared.smoothing import GazeSmoother, DistanceSmoother
from soulframe.brain.state_machine import InteractionStateMachine
from soulframe.brain.image_manager import ImageManager
from soulframe.brain.interaction_model import InteractionModel

logger = logging.getLogger(__name__)

_LOOP_HZ = 30
_FRAME_DURATION_S = 1.0 / _LOOP_HZ
_SHM_CONNECT_TIMEOUT_S = 10.0
_JOIN_TIMEOUT_S = 5.0


# ======================================================================
# Brain loop
# ======================================================================

def run_brain(display_q: Queue, audio_q: Queue) -> None:
    """Main brain loop — reads vision data, drives state machine, sends commands."""
    logger.info("Brain starting up")

    # 1. Connect to vision shared memory
    shm_reader = VisionShmReader()
    if not shm_reader.connect(timeout=_SHM_CONNECT_TIMEOUT_S):
        logger.error("Timed out waiting for vision shared memory")
        return
    logger.info("Connected to vision shared memory")

    # 2. Image manager
    image_mgr = ImageManager()
    count = image_mgr.scan()
    if count == 0:
        logger.error("No images found in gallery — nothing to display")
        return
    logger.info("Gallery loaded with %d image(s)", count)

    # 3. State machine and interaction model
    state_machine = InteractionStateMachine()
    interaction = InteractionModel()

    # 4. Smoothers
    gaze_smoother = GazeSmoother()
    distance_smoother = DistanceSmoother()

    # 5. Load first image
    _send_load_image(display_q, image_mgr)

    prev_state = state_machine.state
    last_tick = time.monotonic()

    logger.info("Entering brain main loop at %d Hz", _LOOP_HZ)

    try:
        while True:
            now = time.monotonic()
            dt = now - last_tick
            last_tick = now

            # ---- Read vision data ----
            raw = shm_reader.read()
            if raw is not None:
                face_data = _smooth(raw, gaze_smoother, distance_smoother)
            else:
                face_data = FaceData()  # defaults: num_faces=0

            # ---- Get current regions ----
            current_image = image_mgr.current_image
            regions = current_image.regions if current_image else []

            # ---- Update interaction model ----
            result = interaction.update(face_data, regions, dt)

            # ---- Update state machine ----
            new_state = state_machine.update(face_data, result.active_regions, dt)

            # ---- React to transitions ----
            if new_state != prev_state:
                _on_transition(
                    prev_state, new_state,
                    display_q, audio_q,
                    image_mgr, result,
                )
                prev_state = new_state

            # ---- Continuous per-frame updates ----
            _continuous_updates(new_state, display_q, audio_q, face_data, result)

            # ---- Image cycling ----
            if state_machine.should_cycle_image:
                logger.info("Idle image cycle triggered")
                image_mgr.next_image()
                _send_crossfade_image(display_q, image_mgr)
                interaction.reset()

            # ---- Sleep ----
            elapsed = time.monotonic() - now
            remaining = _FRAME_DURATION_S - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        logger.info("Brain received KeyboardInterrupt")
    except Exception:
        logger.exception("Unhandled exception in brain loop")
    finally:
        shm_reader.close()
        logger.info("Brain shut down")


# ======================================================================
# State transition handlers
# ======================================================================

def _on_transition(
    old: InteractionState,
    new: InteractionState,
    display_q: Queue,
    audio_q: Queue,
    image_mgr: ImageManager,
    result,
) -> None:
    current = image_mgr.current_image

    # IDLE -> PRESENCE
    if old == InteractionState.IDLE and new == InteractionState.PRESENCE:
        logger.info("Transition: IDLE -> PRESENCE")
        if current and current.ambient:
            path = image_mgr.get_audio_path(current.ambient.file)
            audio_q.put(Command(
                cmd_type=CommandType.PLAY_AMBIENT,
                params={"file_path": str(path), "fade_ms": 1000},
            ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT,
            params={"effect_type": "kenburns", "intensity": 0.3},
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT,
            params={"effect_type": "parallax", "intensity": 0.2},
        ))

    # PRESENCE -> ENGAGED
    elif old == InteractionState.PRESENCE and new == InteractionState.ENGAGED:
        logger.info("Transition: PRESENCE -> ENGAGED")
        if result.dwell_regions and current:
            for region in current.regions:
                if region.id in result.dwell_regions and region.heartbeat:
                    path = image_mgr.get_audio_path(region.heartbeat.file)
                    audio_q.put(Command(
                        cmd_type=CommandType.PLAY_HEARTBEAT,
                        params={
                            "file_path": str(path),
                            "region_id": region.id,
                            "fade_ms": region.heartbeat.fade_in_ms,
                        },
                    ))
                    # Enable breathing for this region
                    for ve in region.visual_effects:
                        if ve.effect_type == "breathing":
                            display_q.put(Command(
                                cmd_type=CommandType.SET_EFFECT,
                                params={
                                    "effect_type": "breathing",
                                    "intensity": 0.6,
                                    "amplitude": ve.params.get("amplitude", 0.008),
                                    "frequency": ve.params.get("frequency_hz", 0.25),
                                },
                            ))
                    break

    # ENGAGED -> CLOSE_INTERACTION
    elif old == InteractionState.ENGAGED and new == InteractionState.CLOSE_INTERACTION:
        logger.info("Transition: ENGAGED -> CLOSE_INTERACTION")
        display_q.put(Command(
            cmd_type=CommandType.SET_VIGNETTE,
            params={"intensity": 0.8},
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT_INTENSITY,
            params={"effect_type": "breathing", "intensity": 1.0},
        ))

    # Any -> WITHDRAWING
    elif new == InteractionState.WITHDRAWING:
        logger.info("Transition: %s -> WITHDRAWING", old.name)
        audio_q.put(Command(
            cmd_type=CommandType.FADE_ALL,
            params={
                "target_volume": 0.0,
                "fade_ms": config.WITHDRAW_FADE_DURATION_S * 1000,
            },
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT_INTENSITY,
            params={"effect_type": "breathing", "intensity": 0.0},
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_VIGNETTE,
            params={"intensity": 0.0},
        ))

    # WITHDRAWING -> IDLE
    elif old == InteractionState.WITHDRAWING and new == InteractionState.IDLE:
        logger.info("Transition: WITHDRAWING -> IDLE")
        audio_q.put(Command(cmd_type=CommandType.STOP_ALL))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT_INTENSITY,
            params={"effect_type": "kenburns", "intensity": 0.0},
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT_INTENSITY,
            params={"effect_type": "parallax", "intensity": 0.0},
        ))


# ======================================================================
# Continuous per-frame updates
# ======================================================================

def _continuous_updates(
    state: InteractionState,
    display_q: Queue,
    audio_q: Queue,
    face_data: FaceData,
    result,
) -> None:
    if state in (InteractionState.IDLE, InteractionState.WITHDRAWING):
        return

    df = result.distance_factor

    # Update gaze position for parallax
    display_q.put(Command(
        cmd_type=CommandType.SET_PARALLAX,
        params={
            "gaze_x": face_data.gaze_screen_x,
            "gaze_y": face_data.gaze_screen_y,
        },
    ))

    # Scale ambient volume by distance
    audio_q.put(Command(
        cmd_type=CommandType.SET_VOLUME,
        params={"name": "ambient", "volume": 0.3 + 0.7 * df},
    ))


# ======================================================================
# Helpers
# ======================================================================

def _send_load_image(display_q: Queue, image_mgr: ImageManager) -> None:
    path = image_mgr.get_image_path()
    img = image_mgr.current_image
    if path is None or img is None:
        return
    display_q.put(Command(
        cmd_type=CommandType.LOAD_IMAGE,
        params={"path": str(path)},
    ))
    logger.info("Sent LOAD_IMAGE: %s", img.title)


def _send_crossfade_image(display_q: Queue, image_mgr: ImageManager) -> None:
    path = image_mgr.get_image_path()
    img = image_mgr.current_image
    if path is None or img is None:
        return
    display_q.put(Command(
        cmd_type=CommandType.CROSSFADE_IMAGE,
        params={"path": str(path), "duration_ms": img.fade_in_ms},
    ))
    logger.info("Sent CROSSFADE_IMAGE: %s", img.title)


def _smooth(raw: FaceData, gaze_s: GazeSmoother, dist_s: DistanceSmoother) -> FaceData:
    if raw.num_faces == 0:
        return raw
    sx, sy = gaze_s.update(raw.gaze_screen_x, raw.gaze_screen_y)
    sd = dist_s.update(raw.face_distance_cm)
    return FaceData(
        frame_counter=raw.frame_counter,
        num_faces=raw.num_faces,
        face_distance_cm=sd,
        gaze_screen_x=sx,
        gaze_screen_y=sy,
        gaze_confidence=raw.gaze_confidence,
        head_yaw=raw.head_yaw,
        head_pitch=raw.head_pitch,
        timestamp_ns=raw.timestamp_ns,
    )


# ======================================================================
# Top-level entry point
# ======================================================================

def start() -> None:
    """Spawns all child processes and runs the brain loop in the main process."""
    logger.info("Soul Frame starting")

    display_q: Queue = Queue()
    audio_q: Queue = Queue()
    vision_q: Queue = Queue()
    processes: List[Process] = []

    try:
        from soulframe.vision.process import run_vision_process
        vision_proc = Process(
            target=run_vision_process, args=(vision_q,),
            name="sf-vision", daemon=True,
        )
        vision_proc.start()
        processes.append(vision_proc)
        logger.info("Vision process started (pid %d)", vision_proc.pid)

        from soulframe.display.process import run_display_process
        display_proc = Process(
            target=run_display_process, args=(display_q,),
            name="sf-display", daemon=True,
        )
        display_proc.start()
        processes.append(display_proc)
        logger.info("Display process started (pid %d)", display_proc.pid)

        from soulframe.audio.process import run_audio_process
        audio_proc = Process(
            target=run_audio_process, args=(audio_q,),
            name="sf-audio", daemon=True,
        )
        audio_proc.start()
        processes.append(audio_proc)
        logger.info("Audio process started (pid %d)", audio_proc.pid)

        run_brain(display_q, audio_q)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received in main")
    except Exception:
        logger.exception("Fatal error in start()")
    finally:
        _shutdown(processes, display_q, audio_q)


def _shutdown(processes: List[Process], display_q: Queue, audio_q: Queue) -> None:
    logger.info("Initiating graceful shutdown")
    for q in (display_q, audio_q):
        try:
            q.put(Command(cmd_type=CommandType.SHUTDOWN))
        except Exception:
            pass

    for proc in processes:
        logger.info("Joining %s (pid %s)...", proc.name, proc.pid)
        proc.join(timeout=_JOIN_TIMEOUT_S)
        if proc.is_alive():
            logger.warning("%s did not exit — terminating", proc.name)
            proc.terminate()
            proc.join(timeout=2)

    logger.info("All processes joined. Soul Frame shut down.")
