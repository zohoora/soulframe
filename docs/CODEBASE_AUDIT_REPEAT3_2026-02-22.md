# Soul Frame Repeat Audit (Pass 4)

Date: 2026-02-22  
Repo: `/home/arash/soulframe`  
Scope: fresh static review + targeted runtime checks + behavior simulations

## Executive Summary

The codebase improved meaningfully (spawn startup, stale vision expiry, child liveness checks, better dwell gating).  
But there are still serious gaps: security posture of the authoring API, metadata/spec-to-runtime drift, several interaction-logic bugs, and deployment readiness issues.

## What I Re-Verified

- `python -m compileall -q soulframe authoring` passes.
- Module import sweep: 32 modules scanned, 2 fail (`authoring.backend.app`, `authoring.backend.routes`) due missing `PIL`.
- Startup smokes:
  - `python -m soulframe --vision` runs (camera/model warnings expected in this env).
  - `python -m soulframe --display` runs.
  - `python -m soulframe --audio` runs.
  - `python -m soulframe --authoring` fails (`ModuleNotFoundError: PIL`).
  - `python -m soulframe` starts, then exits cleanly because gallery has zero valid image assets.

## Confirmed Improvements Since Earlier Audits

- Spawn start method configured: `soulframe/__main__.py:17`.
- Brain stale-frame expiry added: `soulframe/brain/coordinator.py:104`.
- Child process liveness supervision added: `soulframe/brain/coordinator.py:153`.
- ENGAGED transition now keyed off `dwell_regions`: `soulframe/brain/state_machine.py:153`.
- Region min-confidence now used in dwell accumulation: `soulframe/brain/interaction_model.py:72`.
- Runtime path-escape checks added for image/audio lookups:
  - `soulframe/brain/image_manager.py:245`
  - `soulframe/brain/image_manager.py:255`

## Findings

### Critical

1. Authoring API is network-exposed and unauthenticated (destructive operations included)
- `AUTHORING_HOST` binds all interfaces: `soulframe/config.py:60`
- CORS allows all origins: `authoring/backend/app.py:38`
- Delete endpoint has no auth: `authoring/backend/routes.py:284`
- Impact: anyone with network reach can modify/delete gallery data.

2. Authoring mode is still not runnable in this environment
- Runtime failure: `ModuleNotFoundError: No module named 'PIL'` from `authoring/backend/routes.py:17`
- Import failures: `authoring.backend.app`, `authoring.backend.routes`
- Impact: authoring workflow is down unless env is manually repaired.

3. Metadata contract remains partially non-functional at runtime
- Parsed but not effectively applied:
  - `transitions.fade_out_ms` / `transitions.audio_crossfade_ms` parsed in `soulframe/brain/image_manager.py:184` and `soulframe/brain/image_manager.py:185`, but no runtime usage paths.
  - Heartbeat distance fields parsed in `soulframe/brain/image_manager.py:124` and `soulframe/brain/image_manager.py:126`, but no heartbeat distance-volume modulation logic in coordinator/audio runtime.
  - Visual effect `trigger` / `fade_in_ms` parsed in `soulframe/brain/image_manager.py:135` and `soulframe/brain/image_manager.py:136`, but transition logic does not execute trigger/timing semantics.
- Impact: authored metadata can look valid but behavior does not match authored intent.

### High

1. Non-contiguous dwell can still trigger region activation
- Dwell timer increments only on `confidence >= min_conf`, but is not reset while gaze remains in-region at lower confidence:
  - increment: `soulframe/brain/interaction_model.py:73`
  - reset only on leaving region: `soulframe/brain/interaction_model.py:85`
- Repro: `1.4s` high confidence + long low confidence + `0.1s` high confidence can satisfy a `1.5s` dwell.

2. Confidence semantics are inconsistent between engagement and withdrawal logic
- Region dwell uses per-region threshold: `soulframe/brain/interaction_model.py:72`
- Gaze-away timer uses global threshold: `soulframe/brain/state_machine.py:70`
- Repro: region with `min_confidence=0.4` can engage at `0.5`, then transition to `WITHDRAWING` after gaze-away timeout even while still looking at the region.

3. Gallery scan accepts image packages that runtime later refuses to load
- `scan()` validates existence via resolved path without package-boundary enforcement: `soulframe/brain/image_manager.py:63`
- `get_image_path()` later blocks escapes: `soulframe/brain/image_manager.py:245`
- Repro: metadata `image.filename: "../outside.jpg"` is counted by scan, then `get_image_path()` returns `None`.
- Impact: brain can think gallery is valid while display path resolution fails.

4. Default content bundle is not runnable
- Only file present: `content/gallery/example_portrait/metadata.json`
- Referenced assets (`image.jpg`, ambient/heartbeat files) are absent.
- Impact: default `python -m soulframe` run exits with “No images found in gallery”.

5. YuNet fallback provisioning is incomplete operationally
- Face detector expects model at `models/face_detection_yunet.onnx`: `soulframe/vision/face_detector.py:59`
- `models/` currently only has `.gitkeep`; setup script does not fetch YuNet model: `scripts/setup.sh`.
- Impact: on systems without MediaPipe, fallback detector remains disabled.

### Medium

1. Spec/runtime drift remains in state transitions
- Spec says global `Any state -> IDLE` on face_lost 5s: `docs/SPEC.md:179`
- Runtime routes ENGAGED/CLOSE to `WITHDRAWING`: `soulframe/brain/state_machine.py:157` and `soulframe/brain/state_machine.py:167`

2. Effect-type and trigger support are narrower than authored/spec expectations
- Runtime explicitly handles only breathing effect in ENGAGED transition: `soulframe/brain/coordinator.py:248`
- Spec examples include additional effect types/triggers (e.g., vignette on close): `docs/SPEC.md:266` and `docs/SPEC.md:271`

3. Ambient curve name support is narrower than spec field reference
- Spec lists `linear`, `ease_in`, `ease_out`, `ease_in_out`: `docs/SPEC.md:300`
- Implemented lookup supports `linear`, `ease_in_out`/`smoothstep`, `exponential`/`exp`: `soulframe/audio/curves.py:67`
- Unknown curves currently fall back to linear in coordinator: `soulframe/brain/coordinator.py:363`

4. Automated quality gates are still effectively absent
- No test files found.
- `pytest` and `ruff` are not installed in current `.venv`.
- Impact: regressions are likely to ship unnoticed.

### Low / Operational

1. Systemd units are machine-specific and use `sudo` inside service
- Hardcoded user/path: `systemd/soulframe.service:8` and `systemd/soulframe.service:10`
- `ExecStartPre` includes `sudo`: `systemd/soulframe.service:22`
- This is brittle across deployments and often fails in non-interactive service contexts.

2. Frontend dependency is loaded from unpinned CDN URL
- `authoring/frontend/index.html:7`
- Supply-chain/version drift risk for the authoring UI.

## Residual Risk and Test Gaps

- No regression tests for the interaction edge cases above (dwell continuity, confidence threshold mismatch).
- No integration test that validates authored metadata fields actually influence runtime behavior.
- No security/authentication guardrails on authoring API.
