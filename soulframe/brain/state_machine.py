"""Interaction state machine for Soul Frame.

Implements a 5-state FSM: IDLE -> PRESENCE -> ENGAGED -> CLOSE_INTERACTION -> WITHDRAWING -> IDLE.
"""

import logging
import time
from typing import Callable, List, Optional

from soulframe import config
from soulframe.shared.types import InteractionState, FaceData

logger = logging.getLogger(__name__)


class InteractionStateMachine:
    """Five-state finite state machine driving Soul Frame interaction flow."""

    def __init__(self) -> None:
        self._state: InteractionState = InteractionState.IDLE
        self._state_entry_time: float = time.monotonic()

        # Timers (accumulated seconds)
        self._face_lost_timer: float = 0.0
        self._gaze_region_timer: float = 0.0
        self._gaze_away_timer: float = 0.0
        self._withdraw_timer: float = 0.0
        self._idle_image_timer: float = 0.0

        self._should_cycle_image: bool = False

        self.on_state_change: Optional[
            Callable[[InteractionState, InteractionState], None]
        ] = None

    @property
    def state(self) -> InteractionState:
        return self._state

    @property
    def should_cycle_image(self) -> bool:
        return self._should_cycle_image

    def update(
        self,
        face_data: FaceData,
        active_regions: List[str],
        dt: float,
    ) -> InteractionState:
        face_detected = face_data.num_faces > 0
        distance_cm = face_data.face_distance_cm if face_detected else float("inf")
        gaze_confidence = face_data.gaze_confidence if face_detected else 0.0

        # Update running timers
        if face_detected:
            self._face_lost_timer = 0.0
        else:
            self._face_lost_timer += dt

        if active_regions and gaze_confidence >= config.GAZE_MIN_CONFIDENCE:
            self._gaze_region_timer += dt
            self._gaze_away_timer = 0.0
        else:
            self._gaze_region_timer = 0.0
            self._gaze_away_timer += dt

        # Per-state transition logic
        if self._state == InteractionState.IDLE:
            self._update_idle(face_detected, distance_cm, dt)
        elif self._state == InteractionState.PRESENCE:
            self._update_presence(face_detected, gaze_confidence, active_regions)
        elif self._state == InteractionState.ENGAGED:
            self._update_engaged(face_detected, distance_cm)
        elif self._state == InteractionState.CLOSE_INTERACTION:
            self._update_close_interaction(face_detected, distance_cm)
        elif self._state == InteractionState.WITHDRAWING:
            self._update_withdrawing(dt)

        return self._state

    def reset(self) -> None:
        old = self._state
        self._state = InteractionState.IDLE
        self._state_entry_time = time.monotonic()
        self._face_lost_timer = 0.0
        self._gaze_region_timer = 0.0
        self._gaze_away_timer = 0.0
        self._withdraw_timer = 0.0
        self._idle_image_timer = 0.0
        self._should_cycle_image = False
        if old != InteractionState.IDLE and self.on_state_change:
            self.on_state_change(old, InteractionState.IDLE)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: InteractionState) -> None:
        old = self._state
        if old == new_state:
            return
        logger.info("State transition: %s -> %s", old.name, new_state.name)
        self._state = new_state
        self._state_entry_time = time.monotonic()
        if new_state == InteractionState.IDLE:
            self._idle_image_timer = 0.0
            self._should_cycle_image = False
        elif new_state == InteractionState.PRESENCE:
            self._gaze_region_timer = 0.0
        elif new_state in (InteractionState.ENGAGED, InteractionState.CLOSE_INTERACTION):
            self._gaze_away_timer = 0.0
        elif new_state == InteractionState.WITHDRAWING:
            self._withdraw_timer = 0.0
        if self.on_state_change:
            self.on_state_change(old, new_state)

    def _update_idle(self, face_detected: bool, distance_cm: float, dt: float) -> None:
        self._idle_image_timer += dt
        if self._idle_image_timer >= config.IDLE_IMAGE_CYCLE_SECONDS:
            self._should_cycle_image = True
            self._idle_image_timer = 0.0
        else:
            self._should_cycle_image = False

        if face_detected and distance_cm < config.PRESENCE_DISTANCE_CM:
            self._set_state(InteractionState.PRESENCE)

    def _update_presence(
        self, face_detected: bool, gaze_confidence: float, active_regions: List[str]
    ) -> None:
        if self._face_lost_timer >= config.PRESENCE_LOST_TIMEOUT_S:
            self._set_state(InteractionState.IDLE)
            return
        gaze_dwell_s = config.GAZE_DWELL_MS / 1000.0
        if (
            active_regions
            and gaze_confidence >= config.GAZE_MIN_CONFIDENCE
            and self._gaze_region_timer >= gaze_dwell_s
        ):
            self._set_state(InteractionState.ENGAGED)

    def _update_engaged(self, face_detected: bool, distance_cm: float) -> None:
        if self._face_lost_timer >= config.IDLE_FACE_LOST_TIMEOUT_S:
            self._set_state(InteractionState.WITHDRAWING)
            return
        if face_detected and distance_cm < config.CLOSE_INTERACTION_DISTANCE_CM:
            self._set_state(InteractionState.CLOSE_INTERACTION)
            return
        if self._gaze_away_timer >= config.WITHDRAW_GAZE_AWAY_TIMEOUT_S:
            self._set_state(InteractionState.WITHDRAWING)

    def _update_close_interaction(self, face_detected: bool, distance_cm: float) -> None:
        if self._face_lost_timer >= config.IDLE_FACE_LOST_TIMEOUT_S:
            self._set_state(InteractionState.WITHDRAWING)
            return
        if self._gaze_away_timer >= config.WITHDRAW_GAZE_AWAY_TIMEOUT_S:
            self._set_state(InteractionState.WITHDRAWING)
            return
        hysteresis_cm = config.CLOSE_INTERACTION_DISTANCE_CM * 1.5
        if face_detected and distance_cm > hysteresis_cm:
            self._set_state(InteractionState.ENGAGED)

    def _update_withdrawing(self, dt: float) -> None:
        self._withdraw_timer += dt
        if self._withdraw_timer >= config.WITHDRAW_FADE_DURATION_S:
            self._set_state(InteractionState.IDLE)
