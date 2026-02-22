# Soul Frame Codebase Audit

Date: 2026-02-22
Audited branch: current working tree at `/home/arash/soulframe`
Method: spec-vs-code review + runtime smoke tests

## Executive Summary

The repository is not currently deployable as documented. In this environment (`Python 3.8.10`), `--vision`, `--display`, and `--authoring` fail to start, and the default `python -m soulframe` launch path exits after a fatal startup error. Several metadata fields and spec features are parsed but not enforced at runtime.

## Startup Status

| Mode | Result | Notes |
|---|---|---|
| `python -m soulframe --vision` | Fail | Python typing syntax crash in vision modules |
| `python -m soulframe --display` | Fail | `pyglet.canvas` API mismatch |
| `python -m soulframe --authoring` | Fail | `PIL` missing |
| `python -m soulframe` | Fail (logs fatal, exits) | Vision import crash in coordinator startup |
| `python -m soulframe --audio` | Starts | Audio stream opens in this environment |

## Critical Findings

### C1. Python version contract is broken
- Declared support: `>=3.8` in `pyproject.toml:9`.
- Vision modules use Python 3.9+/3.10+ annotation syntax (`list[...]`, `dict[...]`, `X | None`) that fails on 3.8 at import time.
- Examples: `soulframe/vision/camera.py:35`, `soulframe/vision/camera.py:93`, `soulframe/vision/process.py:32`, `soulframe/vision/face_detector.py:84`, `soulframe/vision/distance_estimator.py:30`, `soulframe/vision/gaze_estimator.py:115`, `soulframe/vision/screen_mapper.py:17`.
- Impact: vision and full app cannot start on declared runtime.

### C2. Authoring hard dependency missing from package deps
- `PIL` imported in `authoring/backend/routes.py:16`.
- `Pillow` is not listed in `requirements.txt` or `pyproject.toml` dependencies (`requirements.txt:1`, `pyproject.toml:10`).
- Impact: `--authoring` mode fails immediately.

### C3. Display process incompatible with installed pyglet major behavior
- Display code calls `pyglet.canvas.get_display()` in `soulframe/display/process.py:26`.
- Installed version in environment is `pyglet 2.1.13`; this symbol is unavailable.
- Dependency spec currently allows broad `pyglet>=2.0` in `pyproject.toml:13`.
- Impact: `--display` mode fails immediately.

### C4. YuNet fallback path is not usable as implemented
- Spec expects MediaPipe/YuNet fallback (`docs/SPEC.md:320`).
- YuNet is initialized with empty model paths in `soulframe/vision/face_detector.py:69`.
- OpenCV call with empty paths raises runtime error in this environment.
- Impact: if MediaPipe is absent, fallback detection is effectively dead.

## High Findings

### H1. Full app startup masks fatal errors with clean exit path
- Fatal startup error caught in `soulframe/brain/coordinator.py:359`.
- App still reaches graceful shutdown path in `soulframe/brain/coordinator.py:362`.
- Impact: process can appear "handled" while functionally dead.

### H2. Major metadata fields are parsed but not actually used for behavior
- Parsed: `min_interaction_distance_cm`, `close_interaction_distance_cm`, `audio_crossfade_ms`, ambient fade fields in `soulframe/brain/image_manager.py:161`, `soulframe/brain/image_manager.py:169`, `soulframe/brain/image_manager.py:142`.
- Runtime still uses global constants/hardcoded formulas in `soulframe/brain/state_machine.py:125`, `soulframe/brain/interaction_model.py:97`, `soulframe/brain/coordinator.py:264`, `soulframe/brain/coordinator.py:291`.
- Impact: per-image tuning from authoring/spec does not take effect.

### H3. State machine does not implement spec global timeout semantics
- Spec requires `Any -> IDLE` after `face_lost` 5s (`docs/SPEC.md:179`).
- Current code routes ENGAGED/CLOSE to WITHDRAWING on face-lost timeout (`soulframe/brain/state_machine.py:143`, `soulframe/brain/state_machine.py:153`).
- Impact: behavior diverges from authoritative spec.

### H4. ENGAGED transition can happen without any region-level dwell activation
- State transition checks global active region dwell timer (`soulframe/brain/state_machine.py:134`).
- Region effects/heartbeat require `dwell_regions` in transition handler (`soulframe/brain/coordinator.py:165`).
- Impact: enters ENGAGED with no region-specific activation in some cases.

### H5. Shared memory "no new frame" treated as "no face"
- Brain uses default `FaceData()` when reader has no new frame: `soulframe/brain/coordinator.py:84`.
- Impact: transient frame stalls can trigger face-lost timing and incorrect state transitions.

### H6. Vision shutdown is not wired for graceful command shutdown
- Vision queue created in `soulframe/brain/coordinator.py:324`.
- Shutdown sends command only to display/audio in `soulframe/brain/coordinator.py:367`.
- Vision loop listens for literal `"SHUTDOWN"` message in `soulframe/vision/process.py:74`.
- Impact: vision process often force-terminated instead of cleanly stopped.

### H7. Authoring delete path has traversal/destructive risk
- Path builder does unsanitized join in `authoring/backend/routes.py:35`.
- Delete endpoint performs `shutil.rmtree(image_dir)` in `authoring/backend/routes.py:252`.
- Impact: crafted IDs may target unintended directories.

## Medium Findings

### M1. Region effects are not truly region-local by default
- Breathing center/radius defaults are static in `soulframe/display/effects.py:58`, `soulframe/display/effects.py:59`.
- Transition handler sets amplitude/frequency but not center/radius in `soulframe/brain/coordinator.py:183`.
- Impact: "region-specific" visual behavior is weaker than spec implies.

### M2. Sample content is incomplete and cannot run out of box
- `content/gallery/example_portrait/metadata.json` references missing assets:
  - `image.jpg` (`content/gallery/example_portrait/metadata.json:7`)
  - `audio/ambient.wav` (`content/gallery/example_portrait/metadata.json:14`)
  - `audio/heartbeat.wav` (`content/gallery/example_portrait/metadata.json:35`)
- Directory currently has no files under `content/gallery/example_portrait/audio`.

### M3. Shared dataclass default construction bug
- `RegionShape` requires `shape_type` (`soulframe/shared/types.py:38`).
- `Region` default factory calls `RegionShape()` without required arg (`soulframe/shared/types.py:71`).
- Impact: `Region()` raises `TypeError`; fragile default construction.

### M4. Authoring polygon points are not clamped to [0,1]
- Conversion in `authoring/frontend/index.html:782` returns raw normalized values.
- Impact: invalid coordinates can be persisted when drawing outside image bounds.

### M5. Spec/schema drift across docs, authoring, and runtime
- Spec example uses string `version` and distance-map `intensity_by_distance` keys (`docs/SPEC.md:206`, `docs/SPEC.md:248`) plus `trigger: "gaze_dwell"` (`docs/SPEC.md:262`).
- Code/sample use integer version, `{max_distance_cm,min_distance_cm,curve}`, and `trigger: "on_gaze_dwell"` (`content/gallery/example_portrait/metadata.json:2`, `content/gallery/example_portrait/metadata.json:39`, `content/gallery/example_portrait/metadata.json:49`, `authoring/backend/models.py:35`).
- Impact: inconsistent contracts across tooling and docs.

### M6. Systemd units are hardcoded and depend on sudo in service context
- Hardcoded user/path in `systemd/soulframe.service:8`, `systemd/soulframe.service:10`, `systemd/soulframe-authoring.service:7`, `systemd/soulframe-authoring.service:9`.
- Startup invokes `sudo` in unit `ExecStartPre` (`systemd/soulframe.service:22`, `systemd/soulframe.service:23`).
- Impact: fragile deployment portability and boot reliability.

## Verification Performed

### Runtime checks
- `.venv/bin/python -V` -> `Python 3.8.10`
- `.venv/bin/python -m soulframe --vision` -> import-time typing crash.
- `.venv/bin/python -m soulframe --display` -> `AttributeError: module 'pyglet' has no attribute 'canvas'`.
- `.venv/bin/python -m soulframe --authoring` -> `ModuleNotFoundError: No module named 'PIL'`.
- `.venv/bin/python -m soulframe` -> fatal startup error from vision import.
- `timeout 4s .venv/bin/python -m soulframe --audio` -> starts and opens stream.

### Static/runtime sweep
- Module import sweep found 8 failing modules (vision typing compatibility + missing PIL).
- `python -m compileall -q soulframe authoring` passed (note: this does not catch all annotation/runtime import failures).
- No test suite found in repository; `pytest` unavailable in current venv.

## Suggested Fix Order

1. Resolve boot blockers:
   - Align Python requirement with code, or make code 3.8-compatible.
   - Add missing `Pillow` dependency.
   - Pin/fix pyglet API compatibility.
2. Fix detection fallback reliability:
   - Correct YuNet model loading strategy or package model assets.
3. Close behavior/spec gaps:
   - Enforce metadata-driven thresholds/curves/transitions in runtime.
   - Reconcile state machine with spec global timeout semantics.
4. Harden safety and process lifecycle:
   - Sanitize `image_id` paths in authoring.
   - Wire clean vision shutdown command path.
5. Add minimal CI guardrails:
   - Import smoke test for all modules.
   - One startup test per mode.
   - Schema round-trip tests for authoring metadata.

