# Soul Frame Repeat Codebase Audit (Pass 2)

Date: 2026-02-22  
Repo: `/home/arash/soulframe` (current working tree)  
Method: static code review + targeted runtime smoke tests + focused behavioral simulations

## Executive Summary

The project improved materially since the prior pass (Python 3.8 typing breakage fixed, display API fix, path traversal hardening, dependency declarations improved).  
However, there are still major functional and architecture gaps:

1. The default multiprocessing mode (`fork`) breaks integrated startup for display/audio child processes in this environment.
2. Brain continues running even after child process failure (no child liveness supervision).
3. Stale vision data can keep face-present state indefinitely.
4. Region dwell/engagement logic remains inconsistent and can trigger false ENGAGED transitions.
5. Important metadata fields are still parsed but not applied at runtime.

## What Was Verified As Improved

- Python 3.8-incompatible type syntax in vision modules is fixed.
- Display startup now uses `pyglet.display.get_display()` in `soulframe/display/process.py:26`.
- Dependency declarations now include Pillow and pydantic v2:
  - `pyproject.toml:17`
  - `pyproject.toml:22`
  - `requirements.txt:7`
  - `requirements.txt:12`
- Vision shutdown signal wiring now exists in `_shutdown()`:
  - `soulframe/brain/coordinator.py:468`
  - `soulframe/brain/coordinator.py:470`
- Path traversal mitigation added for authoring image IDs:
  - `authoring/backend/routes.py:35`
  - `authoring/backend/routes.py:49`

## Findings

### Critical

#### C1) Integrated startup relies on `fork`; display/audio children fail under fork
- Evidence:
  - `soulframe/brain/coordinator.py:10` imports `Process`/`Queue` from default multiprocessing context.
  - No explicit start-method configuration in `soulframe/__main__.py:16`.
  - `multiprocessing.get_start_method()` returns `fork` on this platform.
  - Repro: forked child of `run_display_process()` fails with GL context error; spawn-based child succeeds.
  - Repro: forked child of `run_audio_process()` fails to open stream in this environment; spawn-based child stays alive and cleanly shuts down.
- Impact:
  - Full app startup can be partially dead on Linux despite individual mode checks passing.
  - Process behavior differs sharply between debug single-process mode and actual multi-process mode.
- Recommendation:
  - Explicitly use `spawn` for child processes (or `get_context("spawn")` in coordinator).
  - Validate all startup modes under the same start method used in production.

#### C2) Brain does not supervise child process health
- Evidence:
  - Main loop in `run_brain()` has no liveness checks for display/audio/vision child processes: `soulframe/brain/coordinator.py:86`.
  - `start()` calls `run_brain(display_q, audio_q)` without passing process handles for supervision: `soulframe/brain/coordinator.py:443`.
  - During full startup smoke, display/audio children crashed while brain continued running.
- Impact:
  - System can run in degraded or dead state without fail-fast behavior.
  - Operationally hard to detect silent installation failure.
- Recommendation:
  - Pass child process handles into brain loop and enforce liveness checks.
  - Trigger coordinated shutdown/restart when any critical child exits unexpectedly.

#### C3) Stale SHM data can freeze face-presence semantics indefinitely
- Evidence:
  - On SHM read stall (`raw is None`), brain reuses last face forever: `soulframe/brain/coordinator.py:97`.
  - Reuse path explicitly keeps previous face data: `soulframe/brain/coordinator.py:101`.
  - Simulation: after entering PRESENCE, feeding same stale face for 10s keeps state PRESENCE; face-lost timers never advance.
- Impact:
  - If vision publisher stalls after a detected face, system can remain “occupied” indefinitely.
- Recommendation:
  - Track `last_new_frame_time`; expire stale data after a short threshold and synthesize `num_faces=0`.

### High

#### H1) ENGAGED transition can occur without region dwell satisfaction
- Evidence:
  - State machine uses global dwell/confidence thresholds, not per-region thresholds:
    - `soulframe/brain/state_machine.py:145`
    - `soulframe/brain/state_machine.py:149`
  - Transition depends on `active_regions`, not `dwell_regions`: `soulframe/brain/state_machine.py:147`.
  - Transition handler expects `dwell_regions` to start heartbeat/effects: `soulframe/brain/coordinator.py:206`.
  - Repro simulation: state reached `ENGAGED` while `dwell_regions` was empty.
- Impact:
  - User can enter ENGAGED state without any region-specific interaction being truly activated.
- Recommendation:
  - Use region-level dwell results (or selected target region state) as the FSM ENGAGED trigger.

#### H2) Region dwell timer accumulates even when confidence is below region minimum
- Evidence:
  - Dwell timer increments whenever `confidence > 0`: `soulframe/brain/interaction_model.py:62`.
  - Region min-confidence is only checked at trigger time: `soulframe/brain/interaction_model.py:77`.
  - Repro simulation: long low-confidence gaze then short high-confidence tick immediately produced dwell trigger.
- Impact:
  - False positives; dwell can trigger faster than intended once confidence briefly rises.
- Recommendation:
  - Accumulate dwell only when `confidence >= region.gaze_trigger.min_confidence`.

#### H3) Metadata-driven audio behavior still mostly not wired
- Evidence:
  - Parsed fields:
    - `fade_in_distance_cm`, `fade_in_complete_cm`, `fade_curve` in `soulframe/brain/image_manager.py:142`.
    - heartbeat distance config in `soulframe/brain/image_manager.py:108`.
    - `audio_crossfade_ms` in `soulframe/brain/image_manager.py:169`.
  - Runtime ambient volume still hardcoded formula `0.3 + 0.7 * distance_factor`: `soulframe/brain/coordinator.py:326`.
  - `soulframe/audio/curves.py` has no callsites in runtime path.
- Impact:
  - Authoring-configured distance curves and transition timing do not control behavior as schema implies.
- Recommendation:
  - Centralize audio volume mapping through metadata-configured curves.
  - Apply `audio_crossfade_ms` in image transition handling.
  - Apply heartbeat distance intensity mapping continuously.

#### H4) Ambient volume change detection can suppress needed updates after state cycles
- Evidence:
  - In IDLE/WITHDRAWING, `_continuous_updates` returns prior volume tracker without reset: `soulframe/brain/coordinator.py:308`.
  - New update only sent on significant delta: `soulframe/brain/coordinator.py:327`.
  - Repro simulation: second PRESENCE cycle at same distance emitted zero new `SET_VOLUME` commands.
- Impact:
  - Ambient stream may remain at stale level after re-entry depending on prior state/commands.
- Recommendation:
  - Reset `prev_volume` (and optionally gaze trackers) on state transitions into active states.

#### H5) Vision detector fallback is not operational out of the box
- Evidence:
  - MediaPipe is preferred but not declared as dependency in project requirements.
  - YuNet requires model file at `models/face_detection_yunet.onnx`: `soulframe/vision/face_detector.py:59`.
  - If missing, backend is disabled (`none`): `soulframe/vision/face_detector.py:67`.
  - Repository model dir contains only `.gitkeep` (`models/.gitkeep`).
  - Setup script creates `models/` but does not provision detector model: `scripts/setup.sh:49`.
- Impact:
  - On a clean setup with no MediaPipe and no YuNet file, no face detection occurs.
- Recommendation:
  - Provision YuNet model in setup (or package it), or include/test a guaranteed detector dependency path.

### Medium

#### M1) Authoring/frontend allows invalid normalized polygon coordinates
- Evidence:
  - Frontend conversion returns raw normalized values without clamping: `authoring/frontend/index.html:799`.
  - Backend model accepts unconstrained point values: `authoring/backend/models.py:12`.
  - Quick model check accepted out-of-range points.
- Impact:
  - Invalid region geometry can be persisted and cause unpredictable hit testing.
- Recommendation:
  - Clamp in frontend and enforce `[0.0, 1.0]` constraints in backend schema validation.

#### M2) Gallery scan does not validate asset existence; broken package is accepted
- Evidence:
  - Scan marks package as loaded after metadata parse only: `soulframe/brain/image_manager.py:61`.
  - No checks for image/audio path existence before load.
  - Sample package references missing files:
    - `content/gallery/example_portrait/metadata.json:7`
    - `content/gallery/example_portrait/metadata.json:14`
    - `content/gallery/example_portrait/metadata.json:35`
  - Gallery file listing currently includes only metadata file.
- Impact:
  - App can boot with unusable content and fail at render/audio load time.
- Recommendation:
  - Add package validation pass (required file existence + optional warnings/errors).

#### M3) Dependency lower bounds can resolve to incompatible combos
- Evidence:
  - `pydantic>=2.0` with `fastapi>=0.68` in:
    - `pyproject.toml:17`
    - `pyproject.toml:18`
    - `requirements.txt:7`
    - `requirements.txt:8`
- Impact:
  - Fresh installs on permissive solvers could select pydantic-v2 + old fastapi combinations that do not interoperate.
- Recommendation:
  - Raise FastAPI minimum to a pydantic-v2-compatible floor.

#### M4) Authoring backend is unauthenticated and network-exposed by default
- Evidence:
  - Host binds all interfaces: `soulframe/config.py:59`.
  - CORS allows all origins: `authoring/backend/app.py:38`.
  - Destructive endpoints exist (e.g., delete): `authoring/backend/routes.py:262`.
  - No auth/authorization layer in backend routes.
- Impact:
  - On non-isolated networks, remote clients can modify/delete gallery content.
- Recommendation:
  - Restrict bind host for local mode or add authentication and deployment hardening guidance.

#### M5) Production path still runs uvicorn with reload enabled
- Evidence:
  - `uvicorn.run(... reload=True)` in `authoring/backend/app.py:63`.
- Impact:
  - Auto-reloader adds extra process behavior unsuitable for production service operation.
- Recommendation:
  - Gate reload by environment flag and default to `False` for production/service use.

#### M6) Spec/code drift remains in transition semantics and trigger naming
- Evidence:
  - Spec says global “Any state -> IDLE on face_lost 5s”: `docs/SPEC.md:179`.
  - Code sends ENGAGED/CLOSE to WITHDRAWING on face-lost timeout:
    - `soulframe/brain/state_machine.py:154`
    - `soulframe/brain/state_machine.py:165`
  - Spec trigger enum uses `gaze_dwell`: `docs/SPEC.md:262`.
  - Runtime defaults use `on_gaze_dwell`:
    - `soulframe/shared/types.py:63`
    - `authoring/backend/models.py:35`
- Impact:
  - Maintainers and tooling can implement against divergent contracts.
- Recommendation:
  - Choose source-of-truth behavior and align docs/models/runtime together.

### Low / Operational Gaps

#### L1) Current local venv is not in sync with declared requirements
- Evidence:
  - Import sweep failed for authoring modules with `ModuleNotFoundError: PIL`.
  - `--authoring` startup fails in current venv.
  - Declared deps now include `Pillow`, but installed env currently does not.
- Impact:
  - Repeat audit and local runs can misrepresent fixed code if environment is stale.
- Recommendation:
  - Recreate or sync venv as part of verification workflow; add CI import smoke checks.

#### L2) No runnable automated tests/lint tooling in current venv
- Evidence:
  - `python -m pytest -q` -> `No module named pytest`.
  - `python -m ruff check ...` -> `No module named ruff`.
  - No test files discovered in repository scan.
- Impact:
  - Regression risk remains high for cross-process and behavior-level logic.
- Recommendation:
  - Add at least smoke tests for process startup and deterministic state-machine/interaction behavior.

## Verification Commands Run (selected)

- `.venv/bin/python -m compileall -q soulframe authoring`
- `.venv/bin/python -m soulframe --vision` (timeout smoke)
- `.venv/bin/python -m soulframe --display` (timeout smoke)
- `.venv/bin/python -m soulframe --audio` (timeout smoke)
- `.venv/bin/python -m soulframe` (timeout full-mode smoke)
- Focused simulation scripts for:
  - stale face handling
  - ENGAGED without dwell regions
  - low-confidence dwell accumulation
  - volume update suppression across state cycles
  - fork vs spawn child process behavior

## Suggested Fix Order

1. Fix process model reliability:
   - switch to spawn context for child processes
   - add child liveness supervision/fail-fast behavior
2. Fix interaction correctness:
   - stale frame expiry
   - unify FSM engagement with region dwell semantics
   - confidence-gated dwell accumulation
3. Wire metadata semantics fully:
   - ambient/heartbeat distance curves
   - transition crossfade parameters
4. Harden content and authoring:
   - asset existence validation
   - coordinate constraints and backend validation
   - auth/bind defaults for authoring
5. Add CI guardrails:
   - import sweep
   - startup smoke tests per mode
   - deterministic unit tests for state machine + interaction model
