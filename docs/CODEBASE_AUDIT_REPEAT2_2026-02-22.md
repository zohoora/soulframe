# Soul Frame Repeat Audit (Pass 3)

Date: 2026-02-22  
Scope: Fresh post-fix deep audit of current working tree at `/home/arash/soulframe`

## Executive Summary

This pass confirms several major improvements landed (spawn startup stability, child liveness fail-fast, stale vision expiry, dwell gating fixes).  
However, there are still significant functional, security, and spec-drift gaps.

## Confirmed Improvements Since Prior Pass

- Multiprocessing spawn is now set at startup:
  - `soulframe/__main__.py:17`
- Brain now expires stale vision frames and supervises child process liveness:
  - `soulframe/brain/coordinator.py:102`
  - `soulframe/brain/coordinator.py:151`
- State transition to `ENGAGED` now uses `dwell_regions`:
  - `soulframe/brain/state_machine.py:149`
- Dwell accumulation now respects per-region confidence:
  - `soulframe/brain/interaction_model.py:73`
- Volume update tracker reset across idle/withdrawing now works:
  - `soulframe/brain/coordinator.py:324`

## Findings

### Critical / High

#### 1) Metadata path traversal remains possible via `metadata.json` fields
- `update_image` writes client-provided metadata directly:
  - `authoring/backend/routes.py:159`
  - `authoring/backend/routes.py:165`
- Runtime path join does not constrain to image package:
  - `soulframe/brain/image_manager.py:236`
  - `soulframe/brain/image_manager.py:242`
- Impact:
  - A crafted `image.filename` / `audio.*.file` can escape the package directory and target unintended local paths.

#### 2) Authoring API is network-exposed and unauthenticated
- Authoring binds all interfaces:
  - `soulframe/config.py:60`
- CORS is fully open:
  - `authoring/backend/app.py:38`
- Destructive endpoint exposed without auth:
  - `authoring/backend/routes.py:269`
- Impact:
  - On a non-isolated network, anyone with access can modify/delete gallery content.

#### 3) Metadata-driven audio semantics are still not actually honored
- Ambient and heartbeat curve/timing fields are parsed:
  - `soulframe/brain/image_manager.py:150`
  - `soulframe/brain/image_manager.py:116`
  - `soulframe/brain/image_manager.py:177`
- Runtime still uses hardcoded ambient volume mapping:
  - `soulframe/brain/coordinator.py:342`
- Curve utilities remain unused by runtime:
  - `soulframe/audio/curves.py:74`
- Impact:
  - Authoring parameters like `fade_curve`, `fade_in_complete_cm`, heartbeat distance curve, and `audio_crossfade_ms` do not control behavior.

#### 4) Audio loop/bass metadata flags are ignored
- Coordinator does not send loop/bass metadata in audio commands:
  - `soulframe/brain/coordinator.py:189`
  - `soulframe/brain/coordinator.py:226`
- Audio process hardcodes behavior:
  - ambient always `loop=True`: `soulframe/audio/process.py:141`
  - heartbeat always `loop=True`, `bass_boost=True`: `soulframe/audio/process.py:162`
- Impact:
  - Metadata contract for loop/bass behavior is not respected.

#### 5) Visual effects are incorrectly coupled to heartbeat presence and ignore trigger metadata
- `PRESENCE -> ENGAGED` effect activation requires heartbeat object+file:
  - `soulframe/brain/coordinator.py:223`
- Visual effect `trigger` / `fade_in_ms` parsed but not used in runtime selection/timing:
  - `soulframe/brain/image_manager.py:127`
  - `soulframe/brain/image_manager.py:128`
  - `soulframe/brain/coordinator.py:236`
- Impact:
  - Region visual effects can fail to activate if heartbeat audio is absent, and trigger semantics are not enforced.

#### 6) Confidence semantics mismatch still causes false withdrawals
- Interaction model uses per-region min confidence:
  - `soulframe/brain/interaction_model.py:72`
- State machine gaze-away timer uses global confidence threshold:
  - `soulframe/brain/state_machine.py:70`
  - `soulframe/brain/state_machine.py:74`
- Impact:
  - Regions with `min_confidence < config.GAZE_MIN_CONFIDENCE` can still enter `ENGAGED` then be treated as gaze-away and withdraw unexpectedly.

#### 7) Detection fallback still not provisioned out-of-box
- YuNet expected at:
  - `soulframe/vision/face_detector.py:59`
- Setup script does not download/provision YuNet model:
  - `scripts/setup.sh:50`
- `models/` currently contains only:
  - `models/.gitkeep`
- Impact:
  - If MediaPipe is unavailable, detection backend becomes effectively disabled.

#### 8) Bundled sample content is still unusable
- Metadata references missing files:
  - `content/gallery/example_portrait/metadata.json:7`
  - `content/gallery/example_portrait/metadata.json:14`
  - `content/gallery/example_portrait/metadata.json:35`
- Runtime confirms image load failure in display process.
- Impact:
  - Out-of-box run does not render intended sample content.

### Medium

#### 9) Image package scanning still accepts broken/incomplete packages
- Package is accepted after metadata parse only:
  - `soulframe/brain/image_manager.py:61`
- No required asset existence validation before runtime load.
- Impact:
  - Broken packages are loaded and only fail later in render/audio paths.

#### 10) Dependency bounds can still resolve to incompatible combos
- Constraints:
  - `pydantic>=2.0,<2.6`: `pyproject.toml:17`, `requirements.txt:7`
  - `fastapi>=0.93`: `pyproject.toml:18`, `requirements.txt:8`
- Impact:
  - Lower bound permits FastAPI versions predating pydantic-v2 support, creating solver-dependent install risk.

#### 11) Authoring model validation is still too permissive for coordinates
- Points are unbounded float lists:
  - `authoring/backend/models.py:12`
- Frontend clamps draw input, but API can still accept out-of-range points.
- Impact:
  - Invalid region geometry can enter metadata via direct API writes.

#### 12) Spec drift still unresolved in state semantics
- Spec requires global `Any -> IDLE` on face-lost timeout:
  - `docs/SPEC.md:179`
- Runtime sends ENGAGED/CLOSE to WITHDRAWING instead:
  - `soulframe/brain/state_machine.py:153`
  - `soulframe/brain/state_machine.py:164`

### Low / Operational

#### 13) Authoring still fails in the current local venv (dependency sync gap)
- Import error at runtime:
  - `authoring/backend/routes.py:17`
- Current `.venv` is missing Pillow despite updated requirements.

#### 14) Seqlock counter can overflow on long uptime
- Writer sequence increments without wrap handling:
  - `soulframe/shared/ipc.py:47`
  - `soulframe/shared/ipc.py:65`
- Packed as uint32:
  - `soulframe/shared/ipc.py:23`
- Impact:
  - At ~30Hz writes, overflow is possible after ~828 days of continuous operation.

#### 15) Interactive shutdown prints noisy display traceback on Ctrl+C
- Display process catches `Exception` but not `KeyboardInterrupt`:
  - `soulframe/display/process.py:129`
- Impact:
  - Cosmetic/noise issue during interactive termination.

#### 16) No automated tests discovered; pytest/ruff not installed in current venv
- Increases regression risk for cross-process/state behavior.

## Suggested Next Fix Order

1. Security hardening:
   - Constrain metadata file paths to package root.
   - Add auth and safer defaults for authoring bind/CORS.
2. Behavior parity:
   - Wire metadata-driven audio curves, heartbeat intensity, loop/bass flags, and transition timings.
   - Decouple visual effects from heartbeat dependency and honor effect trigger metadata.
3. Content/runtime robustness:
   - Validate package assets at scan time and fail/skip broken packages clearly.
   - Provision YuNet model (or guarantee MediaPipe path) in setup.
4. Consistency and reliability:
   - Align spec/runtime on global timeout behavior.
   - Tighten dependency bounds for FastAPI+pydantic compatibility.
   - Add long-run counter wrap handling in SHM seqlock.
