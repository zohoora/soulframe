# Soul Frame Repeat Audit (Pass 5)

Date: 2026-02-22  
Repo: `/home/arash/soulframe`  
Scope: fresh end-to-end review after latest fixes (static review + runtime smoke + targeted simulations)

## Executive Summary

There are meaningful improvements (spawn start method, stale-frame handling, path hardening, dwell reset fix, heartbeat distance-curve plumbing).  
However, the codebase still has major security, reliability, and behavior-contract gaps.

## Re-Verification Snapshot

- `compileall` passes for `soulframe` and `authoring`.
- Import sweep: 32 modules scanned; 2 import failures remain:
  - `authoring.backend.app`
  - `authoring.backend.routes`
  - Cause: missing `PIL` in current `.venv`.
- Startup smokes:
  - `--vision`, `--display`, `--audio`: run.
  - `--authoring`: fails at import due missing `PIL`.
  - full app: starts, then exits because gallery has no valid image asset files.

## Confirmed Fixes Since Earlier Passes

- Spawn startup configured: `soulframe/__main__.py:17`
- Stale frame expiration and child liveness checks:
  - `soulframe/brain/coordinator.py:109`
  - `soulframe/brain/coordinator.py:168`
- Scan now rejects image path escapes:
  - `soulframe/brain/image_manager.py:64`
- Non-contiguous dwell bug fixed (timer resets on low confidence):
  - `soulframe/brain/interaction_model.py:73`
  - `soulframe/brain/interaction_model.py:74`
- Ambient/heartbeat curve utility support expanded (`ease_in`, `ease_out` added):
  - `soulframe/audio/curves.py:94`

## Findings

### Critical

1) Authoring API remains network-exposed and unauthenticated
- Binds all interfaces: `soulframe/config.py:60`
- CORS wide open: `authoring/backend/app.py:38`
- Destructive delete endpoint has no auth: `authoring/backend/routes.py:290`
- Impact: anyone with network reach can modify/delete gallery content.

2) Runtime can still hard-fail from malformed metadata types
- Metadata parser does not enforce numeric types for dwell/confidence:
  - `soulframe/brain/image_manager.py:113`
  - `soulframe/brain/image_manager.py:116`
- Runtime assumes numeric operations:
  - compare confidence vs min threshold: `soulframe/brain/interaction_model.py:71`
  - divide dwell ms by `1000.0`: `soulframe/brain/interaction_model.py:76`
- Repro performed: string `dwell_time_ms: "abc"` causes `TypeError` and can terminate brain loop.

3) Authoring service is still not runnable in current environment
- Import dependency failure remains at runtime: `authoring/backend/routes.py:17`
- `python -m soulframe --authoring` exits with `ModuleNotFoundError: PIL`.

### High

1) Heartbeat start is duplicated on first ENGAGED tick
- Initial start in transition handler:
  - `soulframe/brain/coordinator.py:246`
- Additional start in continuous updates for same dwell event:
  - `soulframe/brain/coordinator.py:402`
- Repro performed: same region emits two `PLAY_HEARTBEAT` commands.

2) Heartbeat `fade_in_ms` is effectively canceled by immediate volume-set
- Start path sets fade: `soulframe/audio/process.py:172`
- Immediate per-frame volume command sent by brain: `soulframe/brain/coordinator.py:432`
- `set_volume()` cancels active fade (`_fading = False`): `soulframe/audio/audio_stream.py:155`
- Impact: authored heartbeat fade-in behavior is not reliably honored.

3) Per-region confidence logic still conflicts with gaze-away withdrawal logic
- Region dwell uses per-region threshold: `soulframe/brain/interaction_model.py:70`
- Gaze-away timer uses global threshold: `soulframe/brain/state_machine.py:69`
- Repro performed: can ENGAGE at region min=0.4 and still WITHDRAW while gaze remains on-region at confidence 0.5 (< global 0.6).

4) `fade_out_ms` and WITHDRAW duration are inconsistent
- Audio fade duration uses per-image metadata: `soulframe/brain/coordinator.py:300`
- State duration to return IDLE is fixed global constant:
  - `soulframe/brain/state_machine.py:172`
- Impact: stop timing can cut fades short or delay state completion relative to configured fade.

5) Heartbeat lifecycle is incomplete (no per-region stop path)
- STOP command exists and is handled in audio process:
  - `soulframe/shared/types.py:120`
  - `soulframe/audio/process.py:176`
- Brain never emits `STOP_HEARTBEAT` and only clears started set at reset/cycle:
  - clear points: `soulframe/brain/coordinator.py:136`, `soulframe/brain/coordinator.py:158`
- Impact: started heartbeats can persist/accumulate until full fade-all/stop-all.

6) Metadata/spec contract still partially ignored
- `audio_crossfade_ms` parsed but not applied in runtime flow:
  - parse: `soulframe/brain/image_manager.py:191`
- Visual effect `trigger`/`fade_in_ms` parsed but trigger/timing semantics not enforced:
  - parse: `soulframe/brain/image_manager.py:141`
  - runtime activation ignores trigger field: `soulframe/brain/coordinator.py:261`

7) Spec heartbeat distance map format is not supported by parser
- Spec example uses map form (`"300":0.0`, `"150":0.3`, ...):
  - `docs/SPEC.md:248`
- Parser expects `max_distance_cm` / `min_distance_cm` / `curve` keys:
  - `soulframe/brain/image_manager.py:130`
- Repro performed: spec-style map silently falls back to defaults.

### Medium

1) Ambient volume commands are sent even when no ambient stream exists
- Ambient volume command can still be emitted in PRESENCE/ENGAGED path:
  - `soulframe/brain/coordinator.py:386`
- Audio process warns when target stream is missing:
  - `soulframe/audio/process.py:190`

2) Heartbeat volume updates are not rate-limited
- Ambient updates are thresholded (`_VOLUME_EPSILON`), heartbeat updates are unconditional:
  - ambient threshold path: `soulframe/brain/coordinator.py:386`
  - heartbeat send each loop: `soulframe/brain/coordinator.py:432`
- Impact: avoidable queue traffic and lock contention risk.

3) Deployment still not turnkey
- Default gallery content is incomplete (metadata only, missing referenced image/audio assets):
  - `content/gallery/example_portrait/metadata.json`
- YuNet model still not provisioned by setup:
  - expected path: `soulframe/vision/face_detector.py:59`
  - setup does not download model: `scripts/setup.sh`

4) State machine behavior still drifts from spec transition table
- Spec:
  - PRESENCE -> IDLE on face_lost 3s: `docs/SPEC.md:174`
  - Any -> IDLE on face_lost 5s: `docs/SPEC.md:179`
- Runtime routes to WITHDRAWING:
  - `soulframe/brain/state_machine.py:138`
  - `soulframe/brain/state_machine.py:150`

5) Operational hygiene gaps remain
- No discovered automated tests in repo; `pytest`/`ruff` absent in current env.
- Systemd units still machine-specific and include `sudo` in service prestart:
  - `systemd/soulframe.service:8`
  - `systemd/soulframe.service:22`
- Frontend pulls unpinned CDN dependency:
  - `authoring/frontend/index.html:7`

## Residual Risk

- Security exposure of authoring API is still the largest operational risk.
- Metadata parsing robustness is still weak enough for a single malformed file to destabilize runtime.
- Interaction/audio behavior still contains duplicated and conflicting command semantics in ENGAGED flows.
