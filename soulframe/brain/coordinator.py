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
from soulframe.audio.curves import get_curve
from soulframe.shared.types import (
    Command,
    CommandType,
    FaceData,
    ImageMetadata,
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

# Thresholds for change-detection to avoid queue flooding.
_GAZE_EPSILON = 0.005       # ~0.5% of screen
_VOLUME_EPSILON = 0.01      # ~1% volume


# ======================================================================
# Brain loop
# ======================================================================

def run_brain(display_q: Queue, audio_q: Queue, child_procs: Optional[List[Process]] = None) -> None:
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

    # 5. Load first image and apply per-image thresholds
    _send_load_image(display_q, image_mgr)
    _apply_image_thresholds(image_mgr, state_machine, interaction)

    prev_state = state_machine.state
    last_tick = time.monotonic()

    # Last valid face data — used to avoid treating SHM stalls as face-lost
    last_valid_face = FaceData()
    last_new_frame_time = time.monotonic()

    # Change-detection state to avoid queue flooding
    last_sent_gaze_x = 0.0
    last_sent_gaze_y = 0.0
    last_sent_volume = -1.0
    started_heartbeats = {}  # type: dict  # region_id -> start_time (monotonic)
    last_sent_hb_volumes = {}  # type: dict  # stream_name -> last_sent_volume
    ambient_started = False  # whether PLAY_AMBIENT has been sent for current image

    logger.info("Entering brain main loop at %d Hz", _LOOP_HZ)

    try:
        while True:
            now = time.monotonic()
            dt = now - last_tick
            last_tick = now

            # ---- Read vision data ----
            raw = shm_reader.read()
            if raw is not None:
                # Reset smoothers when recovering from stale vision data
                if now - last_new_frame_time > config.VISION_STALE_TIMEOUT_S:
                    gaze_smoother.reset()
                    distance_smoother.reset()
                face_data = _smooth(raw, gaze_smoother, distance_smoother)
                last_valid_face = face_data
                last_new_frame_time = now
            else:
                # No new frame — reuse last valid, but expire after timeout
                # to avoid freezing face-present state indefinitely.
                if now - last_new_frame_time > config.VISION_STALE_TIMEOUT_S:
                    face_data = FaceData()  # synthesize num_faces=0
                else:
                    face_data = last_valid_face

            # ---- Get current regions ----
            current_image = image_mgr.current_image
            regions = current_image.regions if current_image else []

            # ---- Update interaction model ----
            if state_machine.state == InteractionState.WITHDRAWING:
                result = interaction.update(face_data, [], dt)
            else:
                result = interaction.update(face_data, regions, dt)

            # ---- Update state machine ----
            new_state = state_machine.update(
                face_data, result.active_regions, dt,
                dwell_regions=result.dwell_regions,
                min_active_confidence=result.min_active_confidence,
            )

            # ---- React to transitions ----
            if new_state != prev_state:
                _on_transition(
                    prev_state, new_state,
                    display_q, audio_q,
                    image_mgr, result,
                )
                # Track whether ambient audio was started
                if (prev_state == InteractionState.IDLE
                        and new_state == InteractionState.PRESENCE
                        and current_image and current_image.ambient
                        and current_image.ambient.file):
                    ambient_started = True
                if prev_state == InteractionState.WITHDRAWING and new_state == InteractionState.IDLE:
                    interaction.reset()
                    started_heartbeats.clear()
                    last_sent_hb_volumes.clear()
                    ambient_started = False
                    gaze_smoother.reset()
                    distance_smoother.reset()
                prev_state = new_state

            # ---- Continuous per-frame updates (rate-limited) ----
            last_sent_gaze_x, last_sent_gaze_y, last_sent_volume = (
                _continuous_updates(
                    new_state, display_q, audio_q, face_data, result,
                    last_sent_gaze_x, last_sent_gaze_y, last_sent_volume,
                    image_metadata=current_image,
                    image_mgr=image_mgr,
                    started_heartbeats=started_heartbeats,
                    last_sent_hb_volumes=last_sent_hb_volumes,
                    ambient_started=ambient_started,
                )
            )

            # ---- Image cycling ----
            if state_machine.should_cycle_image:
                logger.info("Idle image cycle triggered")
                image_mgr.next_image()
                _send_crossfade_image(display_q, audio_q, image_mgr)
                interaction.reset()
                gaze_smoother.reset()
                distance_smoother.reset()
                started_heartbeats.clear()
                last_sent_hb_volumes.clear()
                ambient_started = False
                _apply_image_thresholds(image_mgr, state_machine, interaction)

            # ---- Sleep ----
            elapsed = time.monotonic() - now
            remaining = _FRAME_DURATION_S - elapsed
            if remaining > 0:
                time.sleep(remaining)

            # ---- Child process liveness check ----
            if child_procs:
                for proc in child_procs:
                    if not proc.is_alive():
                        logger.error(
                            "Child process %s (pid %s) died unexpectedly — shutting down",
                            proc.name, proc.pid,
                        )
                        return

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
        if current and current.ambient and current.ambient.file:
            path = image_mgr.get_audio_path(current.ambient.file)
            if path is not None and path.exists():
                audio_q.put(Command(
                    cmd_type=CommandType.PLAY_AMBIENT,
                    params={
                        "file_path": str(path),
                        "fade_ms": 1000,
                        "loop": current.ambient.loop,
                    },
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
                if region.id not in result.dwell_regions:
                    continue
                # Heartbeat start is handled in _continuous_updates to avoid duplicates
                # Enable visual effects for dwelled regions
                for ve in region.visual_effects:
                    if ve.trigger != "on_gaze_dwell":
                        continue
                    effect_params = {"effect_type": ve.effect_type}
                    effect_params.update(ve.params)
                    effect_params["intensity"] = 0.6
                    if ve.fade_in_ms > 0:
                        effect_params["fade_in_ms"] = ve.fade_in_ms
                    if ve.effect_type == "breathing":
                        effect_params.setdefault("amplitude", 0.008)
                        effect_params.setdefault("frequency", ve.params.get("frequency_hz", 0.25))
                    display_q.put(Command(
                        cmd_type=CommandType.SET_EFFECT,
                        params=effect_params,
                    ))

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

    # CLOSE_INTERACTION -> ENGAGED (viewer backed up — reduce intensity)
    elif old == InteractionState.CLOSE_INTERACTION and new == InteractionState.ENGAGED:
        logger.info("Transition: CLOSE_INTERACTION -> ENGAGED")
        display_q.put(Command(
            cmd_type=CommandType.SET_VIGNETTE,
            params={"intensity": 0.0},
        ))
        display_q.put(Command(
            cmd_type=CommandType.SET_EFFECT_INTENSITY,
            params={"effect_type": "breathing", "intensity": 0.6},
        ))

    # Any -> WITHDRAWING
    elif new == InteractionState.WITHDRAWING:
        logger.info("Transition: %s -> WITHDRAWING", old.name)
        fade_ms = int(config.WITHDRAW_FADE_DURATION_S * 1000)
        if current and current.fade_out_ms:
            fade_ms = current.fade_out_ms
        audio_q.put(Command(
            cmd_type=CommandType.FADE_ALL,
            params={
                "target_volume": 0.0,
                "fade_ms": fade_ms,
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
        display_q.put(Command(
            cmd_type=CommandType.SET_PARALLAX,
            params={"gaze_x": 0.5, "gaze_y": 0.5},
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

    else:
        logger.warning("Unhandled transition: %s -> %s", old.name, new.name)


# ======================================================================
# Continuous per-frame updates (rate-limited via change detection)
# ======================================================================

def _continuous_updates(
    state: InteractionState,
    display_q: Queue,
    audio_q: Queue,
    face_data: FaceData,
    result,
    prev_gaze_x: float,
    prev_gaze_y: float,
    prev_volume: float,
    image_metadata: Optional[ImageMetadata] = None,
    image_mgr: Optional['ImageManager'] = None,
    started_heartbeats: Optional[dict] = None,
    last_sent_hb_volumes: Optional[dict] = None,
    ambient_started: bool = False,
):
    # type: (...) -> tuple
    """Send per-frame updates only when values change meaningfully.

    Returns the updated (gaze_x, gaze_y, volume) tracking state.
    """
    if state in (InteractionState.IDLE, InteractionState.WITHDRAWING):
        return prev_gaze_x, prev_gaze_y, -1.0

    gx = face_data.gaze_screen_x
    gy = face_data.gaze_screen_y
    df = result.distance_factor

    # Only send gaze update if position moved noticeably
    if (abs(gx - prev_gaze_x) > _GAZE_EPSILON
            or abs(gy - prev_gaze_y) > _GAZE_EPSILON):
        display_q.put(Command(
            cmd_type=CommandType.SET_PARALLAX,
            params={"gaze_x": gx, "gaze_y": gy},
        ))
        prev_gaze_x = gx
        prev_gaze_y = gy

    # Compute and send ambient volume only when the stream was actually started
    if ambient_started and image_metadata and image_metadata.ambient and image_metadata.ambient.file:
        amb = image_metadata.ambient
        try:
            curve_fn = get_curve(amb.fade_curve)
            volume = curve_fn(
                face_data.face_distance_cm,
                amb.fade_in_distance_cm,
                amb.fade_in_complete_cm,
            )
        except (ValueError, TypeError):
            volume = 0.3 + 0.7 * result.distance_factor
        if abs(volume - prev_volume) > _VOLUME_EPSILON:
            audio_q.put(Command(
                cmd_type=CommandType.SET_VOLUME,
                params={"name": "ambient", "volume": volume},
            ))
            prev_volume = volume

    # -- Heartbeat lifecycle: start, stop, and distance-volume modulation --
    if state in (InteractionState.ENGAGED, InteractionState.CLOSE_INTERACTION):
        if image_metadata and image_metadata.regions and started_heartbeats is not None:
            now = time.monotonic()
            dwell_set = set(result.dwell_regions or [])

            for region in image_metadata.regions:
                if not region.heartbeat or not region.heartbeat.file:
                    continue
                stream_name = "heartbeat_" + region.id

                # Start heartbeat for newly-dwelled regions
                if (region.id in dwell_set
                        and region.id not in started_heartbeats
                        and image_mgr is not None):
                    path = image_mgr.get_audio_path(region.heartbeat.file)
                    if path is not None and path.exists():
                        audio_q.put(Command(
                            cmd_type=CommandType.PLAY_HEARTBEAT,
                            params={
                                "file_path": str(path),
                                "region_id": region.id,
                                "fade_ms": region.heartbeat.fade_in_ms,
                                "loop": region.heartbeat.loop,
                                "bass_boost": region.heartbeat.bass_boost,
                            },
                        ))
                        started_heartbeats[region.id] = now

                # Modulate heartbeat volume by distance (with change detection)
                if region.id in started_heartbeats:
                    # Grace period: don't send SET_VOLUME during fade-in
                    # to avoid canceling the fade started by PLAY_HEARTBEAT
                    fade_grace_s = region.heartbeat.fade_in_ms / 1000.0
                    if now - started_heartbeats[region.id] < fade_grace_s:
                        continue

                    hb = region.heartbeat
                    try:
                        curve_fn = get_curve(hb.curve)
                        hb_vol = curve_fn(
                            face_data.face_distance_cm,
                            hb.max_distance_cm,
                            hb.min_distance_cm,
                        )
                    except (ValueError, TypeError):
                        hb_vol = result.distance_factor

                    # Only send if volume changed meaningfully (M1 fix)
                    if last_sent_hb_volumes is not None:
                        prev_hb_vol = last_sent_hb_volumes.get(stream_name, -1.0)
                        if abs(hb_vol - prev_hb_vol) <= _VOLUME_EPSILON:
                            continue
                        last_sent_hb_volumes[stream_name] = hb_vol

                    audio_q.put(Command(
                        cmd_type=CommandType.SET_VOLUME,
                        params={"name": stream_name, "volume": hb_vol},
                    ))

            # Stop heartbeats for regions gaze has left (Audit High 5)
            stopped = []
            for rid in started_heartbeats:
                if rid not in dwell_set:
                    stream_name = "heartbeat_" + rid
                    audio_q.put(Command(
                        cmd_type=CommandType.STOP_HEARTBEAT,
                        params={"region_id": rid, "fade_ms": 800},
                    ))
                    stopped.append(rid)
                    if last_sent_hb_volumes is not None:
                        last_sent_hb_volumes.pop(stream_name, None)
            for rid in stopped:
                del started_heartbeats[rid]

    return prev_gaze_x, prev_gaze_y, prev_volume


# ======================================================================
# Helpers
# ======================================================================

def _apply_image_thresholds(
    image_mgr: ImageManager,
    state_machine: InteractionStateMachine,
    interaction: InteractionModel,
) -> None:
    """Apply per-image distance thresholds to state machine and interaction model."""
    img = image_mgr.current_image
    if img is None:
        return
    state_machine.set_distance_thresholds(
        presence_cm=img.min_interaction_distance_cm,
        close_cm=img.close_interaction_distance_cm,
    )
    state_machine.set_withdraw_duration(
        img.fade_out_ms / 1000.0 if img.fade_out_ms else config.WITHDRAW_FADE_DURATION_S
    )
    interaction.set_distance_thresholds(
        near_cm=img.close_interaction_distance_cm,
        far_cm=img.min_interaction_distance_cm,
    )


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


def _send_crossfade_image(display_q: Queue, audio_q: Queue, image_mgr: ImageManager) -> None:
    path = image_mgr.get_image_path()
    img = image_mgr.current_image
    if path is None or img is None:
        return
    display_q.put(Command(
        cmd_type=CommandType.CROSSFADE_IMAGE,
        params={"path": str(path), "duration_ms": img.fade_in_ms},
    ))
    # Fade out any active audio during the image transition
    audio_q.put(Command(
        cmd_type=CommandType.FADE_ALL,
        params={"target_volume": 0.0, "fade_ms": img.audio_crossfade_ms},
    ))
    logger.info("Sent CROSSFADE_IMAGE: %s (audio crossfade: %d ms)", img.title, img.audio_crossfade_ms)


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

    display_q = Queue()  # type: Queue
    audio_q = Queue()    # type: Queue
    vision_q = Queue()   # type: Queue
    processes = []       # type: List[Process]

    try:
        from soulframe.vision.process import run_vision_process
        vision_proc = Process(
            target=run_vision_process, args=(vision_q,),
            name="sf-vision", daemon=False,
        )
        vision_proc.start()
        processes.append(vision_proc)
        logger.info("Vision process started (pid %d)", vision_proc.pid)

        from soulframe.display.process import run_display_process
        display_proc = Process(
            target=run_display_process, args=(display_q,),
            name="sf-display", daemon=False,
        )
        display_proc.start()
        processes.append(display_proc)
        logger.info("Display process started (pid %d)", display_proc.pid)

        from soulframe.audio.process import run_audio_process
        audio_proc = Process(
            target=run_audio_process, args=(audio_q,),
            name="sf-audio", daemon=False,
        )
        audio_proc.start()
        processes.append(audio_proc)
        logger.info("Audio process started (pid %d)", audio_proc.pid)

        run_brain(display_q, audio_q, child_procs=processes)

    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received in main")
    except Exception:
        logger.exception("Fatal error in start()")
    finally:
        _shutdown(processes, display_q, audio_q, vision_q)


def _shutdown(
    processes: List[Process],
    display_q: Queue,
    audio_q: Queue,
    vision_q: Queue,
) -> None:
    logger.info("Initiating graceful shutdown")

    # Send shutdown to display and audio (Command protocol)
    for q in (display_q, audio_q):
        try:
            q.put(Command(cmd_type=CommandType.SHUTDOWN))
        except Exception:
            pass

    # Send shutdown to vision (string protocol — vision expects "SHUTDOWN")
    try:
        vision_q.put("SHUTDOWN")
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
