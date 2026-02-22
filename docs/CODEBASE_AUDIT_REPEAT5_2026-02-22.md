# Soul Frame Repeat Audit (Pass 6)

Date: 2026-02-22  
Repo: `/home/arash/soulframe`  
Scope: repeat in-depth code and runtime review after latest round of fixes

## Executive Summary

The project has improved in key runtime areas (stale vision handling, spawn startup, confidence plumbing, heartbeat lifecycle, withdraw timing).  
The main remaining risks are still concentrated in:

- Authoring API security hardening
- Metadata robustness (type safety and ID constraints)
- Spec-to-runtime contract gaps
- Deployment/test completeness

## Re-Verification Snapshot

Environment used: `.venv/bin/python` (Python 3.8.10)

- `python -m compileall soulframe authoring`: pass
- Import sweep over project modules: 31 scanned, 2 failed imports
  - `authoring.backend.app`
  - `authoring.backend.routes`
  - reason: `ModuleNotFoundError: No module named 'PIL'`
- Startup smoke checks:
  - `.venv/bin/python -m soulframe --vision`: runs (camera/model warnings in this environment)
  - `.venv/bin/python -m soulframe --display`: runs
  - `.venv/bin/python -m soulframe --audio`: runs
  - `.venv/bin/python -m soulframe --authoring`: fails (`ModuleNotFoundError: PIL`)
  - `.venv/bin/python -m soulframe`: starts, exits cleanly because gallery has no valid image assets

## Confirmed Improvements Since Earlier Passes

- Multiprocessing `spawn` method is set: `soulframe/__main__.py:19`
- Vision stale-frame timeout + child liveness checks exist: `soulframe/brain/coordinator.py:101`, `soulframe/brain/coordinator.py:180`
- Gallery scan rejects image path escapes: `soulframe/brain/image_manager.py:82`
- Dwell reset on low confidence implemented: `soulframe/brain/interaction_model.py:75`
- Per-region min confidence is propagated to state machine:
  - `soulframe/brain/interaction_model.py:109`
  - `soulframe/brain/coordinator.py:129`
  - `soulframe/brain/state_machine.py:63`
- Heartbeat lifecycle is improved:
  - start handled in continuous updates: `soulframe/brain/coordinator.py:388`
  - fade grace before volume modulation: `soulframe/brain/coordinator.py:409`
  - explicit stop command emitted: `soulframe/brain/coordinator.py:442`
- Withdraw duration now follows metadata fade-out: `soulframe/brain/coordinator.py:472`

## Findings

### Critical

1) Authoring API is still exposed and unauthenticated

- Network bind on all interfaces: `soulframe/config.py:60`
- Permissive CORS: `authoring/backend/app.py:38`
- Destructive endpoint without auth: `authoring/backend/routes.py:297`
- Impact: anyone with network reach can delete or modify gallery content.

2) Authoring file serving still allows metadata-based path traversal

- Metadata filename is trusted if `(image_dir / meta_filename).is_file()`: `authoring/backend/routes.py:105`, `authoring/backend/routes.py:106`
- Served path is directly `image_dir / image_file`: `authoring/backend/routes.py:317`
- No package-boundary check is applied to metadata-derived filename.
- Impact: crafted metadata can point outside the image directory (for example via `../` or absolute paths) and expose arbitrary readable files.
- Note: runtime exploit test was not executed in this environment because authoring import currently fails on missing `PIL`.

### High

1) Malformed ambient numeric metadata can crash coordinator loop

- Ambient distances are not numeric-sanitized during parse: `soulframe/brain/image_manager.py:203`, `soulframe/brain/image_manager.py:204`
- Runtime curve evaluation only catches `ValueError`: `soulframe/brain/coordinator.py:364`
- Invalid types can raise `TypeError` and escape.
- Repro: passing string `fade_in_distance_cm='far'` raises `TypeError: '<=' not supported between instances of 'str' and 'float'`.

2) Empty/duplicate region IDs cause dwell and heartbeat collisions

- Region IDs default to empty string when missing: `soulframe/brain/image_manager.py:186`
- Dwell timers keyed by `region.id`: `soulframe/brain/interaction_model.py:74`
- Heartbeat stream names keyed by `region.id`: `soulframe/brain/coordinator.py:385`
- Repro: two regions with `id=''` share a single dwell timer; dwell in region A carries into region B and triggers early.

3) `audio_crossfade_ms` is parsed but not applied

- Parsed into metadata: `soulframe/brain/image_manager.py:232`
- No runtime path consumes this value during audio transitions.
- Impact: authored transition intent is partially ignored.

4) Visual effect `trigger` and `fade_in_ms` are not honored semantically

- Parsed: `soulframe/brain/image_manager.py:180`, `soulframe/brain/image_manager.py:181`
- Runtime effect activation on ENGAGED does not branch by `trigger` and does not apply `fade_in_ms`: `soulframe/brain/coordinator.py:242`
- Impact: metadata contract and runtime behavior diverge.

5) Heartbeat distance map format in spec is still unsupported

- Spec uses map style (`"300": 0.0`, etc.): `docs/SPEC.md:248`
- Runtime parser expects `max_distance_cm/min_distance_cm/curve`: `soulframe/brain/image_manager.py:157`, `soulframe/brain/image_manager.py:161`, `soulframe/brain/image_manager.py:171`
- Impact: spec-valid metadata falls back to defaults rather than intended intensity mapping.

### Medium

1) Ambient `SET_VOLUME` still sent even when no ambient stream exists

- Volume command is emitted from fallback path: `soulframe/brain/coordinator.py:368`, `soulframe/brain/coordinator.py:371`
- Audio process logs missing stream warnings: `soulframe/audio/process.py:190`
- Impact: unnecessary queue traffic and noisy logs.

2) State transitions still drift from spec table

- Spec says `PRESENCE -> IDLE` on face-lost timeout and `Any -> IDLE` global timeout: `docs/SPEC.md:174`, `docs/SPEC.md:179`
- Runtime uses `PRESENCE -> WITHDRAWING` and no explicit global Any->IDLE transition: `soulframe/brain/state_machine.py:152`, `soulframe/brain/state_machine.py:153`

3) Deployment content/model completeness remains incomplete

- Gallery sample is metadata-only (no image/audio assets): `content/gallery/example_portrait/metadata.json`
- YuNet model file not provisioned in repo and not downloaded by setup:
  - expected path warning at runtime: `models/face_detection_yunet.onnx`
  - setup script only creates folder: `scripts/setup.sh:50`

4) QA tooling is still minimal in current environment

- No test files discovered in repository
- `pytest` and `ruff` are not installed in current `.venv`

## Priority Fix Order

1. Lock down authoring API (bind localhost by default, add auth, restrict CORS).
2. Add strict path-boundary checks for metadata filenames in authoring routes.
3. Harden metadata parsing and validation for all numeric fields and IDs.
4. Enforce unique non-empty region IDs (authoring + runtime guard).
5. Close spec/runtime gaps (`audio_crossfade_ms`, effect trigger/fade semantics, heartbeat map format).
6. Add baseline CI checks (unit tests for parser/state machine and lint/type checks).
