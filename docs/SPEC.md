# Soul Frame -- Technical Specification

> Authoritative reference document for the Soul Frame project.
> All implementation decisions should trace back to this spec.

---

## Table of Contents

1. [Context](#context)
2. [Hardware](#hardware)
3. [Architecture Overview](#architecture-overview)
4. [IPC: Shared Memory Struct](#ipc-shared-memory-struct)
5. [Interaction State Machine](#interaction-state-machine)
6. [Image Data Model](#image-data-model)
7. [Audio Engine](#audio-engine)
8. [Display Engine](#display-engine)
9. [Authoring Tool](#authoring-tool)
10. [Tech Stack](#tech-stack)
11. [Configuration](#configuration)
12. [Development Phases](#development-phases)
13. [Known Blockers](#known-blockers)

---

## Context

Soul Frame is a technical art installation: a 16" OLED display in a 3D-printed frame that displays photographs of real people. A camera tracks the viewer's gaze and proximity. As viewers approach and look at specific regions of the image, the frame comes alive with environmental audio, heartbeat sounds, and subtle visual animations. Each image is a unique interactive experience. The AI personality running it is called "Soulie."

- **Deployment:** Single prototype on NVIDIA Jetson Orin NX.
- **Connectivity:** Internet connectivity assumed.
- **Privacy:** No privacy concerns (art installation context).
- **Scope:** Open-ended project.

---

## Hardware

| Component | Model | Connection | Notes |
|-----------|-------|------------|-------|
| Computer | NVIDIA Jetson Orin NX 16GB (Seeed reComputer J4012) | - | JetPack 6.2.1, Ubuntu 22.04, L4T R36.4.3, 16 GB RAM |
| Display | ASUS ZenScreen 16" OLED | mini-HDMI | USB-C DP Alt Mode NOT supported by Jetson |
| Camera | Arducam GMSL2 8MP (IMX219) | 22-pin flex + FAKRA coax (via GMSL2 SerDes) | Uses nvarguscamerasrc (ISP), not V4L2. See `docs/HARDWARE_AND_PLATFORM.md` |
| Amplifier | HiLetgo TPA3116D2 | 3.5 mm analog input | 2.1 channel: 2x50 W (L/R) + 1x100 W (Sub) |
| Voice exciters | 2x Dayton DAEX25FHE-4 (4-ohm) | Amp L/R channels | Mounted to frame |
| Bass exciter | 1x Dayton TT25-8 (8-ohm) | Amp Sub channel | Mounted to frame |
| Mic + DAC | SeeedStudio ReSpeaker Mic Array v2.0 | USB | WM8960 DAC with 3.5 mm stereo output |

### Amplifier Wiring

```
Jetson USB --> ReSpeaker v2.0 (WM8960 DAC) --> 3.5mm stereo --> TPA3116D2 2.1 Amp
                                                                    |
                                                  L out --> Dayton DAEX25FHE-4 (voice)
                                                  R out --> Dayton DAEX25FHE-4 (voice)
                                                  Sub out --> Dayton TT25-8 (bass)
                                                                    |
                                                              DC 24V Power
```

**Note:** ReSpeaker outputs stereo L/R. The TPA3116D2's built-in crossover derives sub from bass frequencies. Heartbeat audio is EQ'd in software to emphasize low frequencies, and the amp's hardware crossover routes it to the bass exciter.

---

## Architecture Overview

Four Python processes communicating via `multiprocessing.Queue` (commands) and `multiprocessing.shared_memory` (vision data):

```
                    +-------------------+
                    |      BRAIN        |
                    |   (Coordinator)   |
                    |   State Machine   |
                    |   Image Manager   |
                    |   Interaction     |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+   +-----v------+  +----v--------+
     |   VISION   |   |  DISPLAY   |  |    AUDIO    |
     |  Process   |   |  Process   |  |   Process   |
     | Camera     |   | Pyglet/GL  |  | sounddevice |
     | Face Det.  |   | Shaders    |  | Custom mixer|
     | Gaze Est.  |   | Effects    |  | Bass-boost  |
     | Distance   |   | Fullscreen |  | Fade curves |
     +------------+   +------------+  +-------------+
```

### Process Communication

- **Vision → Brain:** Shared memory with seqlock (44 bytes at ~30 Hz).
- **Brain → Display:** `multiprocessing.Queue` carrying `Command` dataclasses.
- **Brain → Audio:** `multiprocessing.Queue` carrying `Command` dataclasses.

### Why Multi-Process

- **Python GIL** prevents true parallelism in a single process.
- **OpenGL context** must be owned by exactly one process (Display).
- **Audio** needs a low-latency, dedicated process to avoid underruns.
- **Vision** is CPU+GPU heavy (face detection, gaze estimation).
- **Orin NX has 8 cores** -- multi-process takes full advantage of the hardware.

### Process Lifecycle

All child processes are spawned with `daemon=False` to ensure proper cleanup of shared resources (e.g. shared memory segments). The brain process monitors child liveness and shuts down if any child dies. Shutdown is coordinated via `SHUTDOWN` commands sent over the queues.

---

## IPC: Shared Memory Struct

**Direction:** Vision --> Brain, updated at approximately 30 Hz.

### Seqlock Protocol

The shared memory segment is **44 bytes** total: a 4-byte seqlock counter followed by the 40-byte data payload. The seqlock prevents torn reads on architectures where a 40-byte memcpy is not atomic (e.g. aarch64/Jetson).

```
Offset  Size   Field
------  ----   -----
0       4      seqlock_counter (uint32)  -- even = stable, odd = write in progress
4       4      frame_counter (uint32)
8       4      num_faces (uint32)
12      4      face_distance_cm (float32)
16      4      gaze_screen_x (float32)     -- normalized 0.0-1.0
20      4      gaze_screen_y (float32)     -- normalized 0.0-1.0
24      4      gaze_confidence (float32)
28      4      head_yaw (float32)          -- radians
32      4      head_pitch (float32)        -- radians
36      8      timestamp_ns (uint64)
------  ----
Total: 44 bytes (4 seqlock + 40 data)
```

**Writer protocol (vision process):**
1. Increment seqlock counter to odd (write-in-progress).
2. Memory fence (`__sync_synchronize` / dmb on ARM).
3. Write data payload.
4. Memory fence.
5. Increment seqlock counter to even (write-complete).

**Reader protocol (brain process):**
1. Read seqlock counter → `seq1`. If odd, skip (write in progress).
2. Memory fence.
3. Read data payload.
4. Memory fence.
5. Read seqlock counter → `seq2`. If `seq1 != seq2`, discard (torn read).

If the shared memory segment becomes inaccessible (vision process crash), the reader catches `BufferError`/`ValueError`/`OSError` and synthesizes a zero-face `FaceData` so the brain can transition gracefully to IDLE.

All spatial coordinates are normalized to 0.0--1.0 where applicable. Commands from Brain to Display and Audio travel over `multiprocessing.Queue`.

---

## Interaction State Machine

### State Diagram

```
                   face_lost (3s) or
                   distance >= 300cm
             +---------------------------+
             |                           |
             v       face_detected       |
       +----------+ (distance < 300cm)   |
       |          +--------------------> +-----+------+
       |   IDLE   |                      |  PRESENCE  |
       |          |                      |            |
       +----+-----+                      +-----+------+
            ^                                  |
            |  (5 min timer)                   | gaze_on_region
            +---> [next image]                 | (dwell > 1.5s)
            |                                  v
            |                            +-----+------+
            |                            |  ENGAGED   +<-----+
            |                            +-----+------+      |
            |                                  |             |
            |                                  | dist < 80cm | dist > 120cm
            |                                  v             | (hysteresis)
            |                            +-----+------+      |
            |                            |   CLOSE    +------+
            |                            | INTERACTION|
            |                            +-----+------+
            |                                  |
            |  fade complete               face_lost 5s or
            |                              gaze_away 8s
       +----+-----+                           |
       |WITHDRAWING| <------------------------+
       +----------+   (also from ENGAGED:
                       face_lost 5s or gaze_away 8s)
```

### State Behavior Table

| State | What Happens | Audio | Visuals |
|-------|-------------|-------|---------|
| **IDLE** | Normal picture frame. Images cycle every 5 min. | Silent | Static image, very subtle Ken Burns |
| **PRESENCE** | Viewer detected. Image cycle paused. | Ambient audio fades in (volume scales with distance) | Ken Burns, gentle parallax |
| **ENGAGED** | Gaze locked on a person region for 1.5 s+ | Heartbeat begins. Bass scales with distance | Region-specific effects (breathing, etc.) |
| **CLOSE_INTERACTION** | Viewer very close (< 80 cm) | Full bass, maximum heartbeat | All effects peak, vignette |
| **WITHDRAWING** | Viewer leaving. Graceful fade to static. | All audio fades to zero | Effects ease back to static |

### Transition Table

| From | To | Trigger | Timeout |
|------|----|---------|---------|
| IDLE | PRESENCE | `face_detected` AND `distance < 300 cm` | — |
| PRESENCE | WITHDRAWING | `face_lost` | 3 s (`PRESENCE_LOST_TIMEOUT_S`) |
| PRESENCE | WITHDRAWING | `distance >= presence_distance` | — |
| PRESENCE | ENGAGED | `gaze_on_region` dwell threshold met | per-region `dwell_time_ms` (default 1500 ms) |
| ENGAGED | CLOSE_INTERACTION | `distance < 80 cm` | — |
| ENGAGED | WITHDRAWING | `face_lost` | 5 s (`IDLE_FACE_LOST_TIMEOUT_S`) |
| ENGAGED | WITHDRAWING | `gaze_away` (no region active) | 8 s (`WITHDRAW_GAZE_AWAY_TIMEOUT_S`) |
| CLOSE_INTERACTION | ENGAGED | `distance > close_distance * 1.5` (hysteresis) | — |
| CLOSE_INTERACTION | WITHDRAWING | `face_lost` | 5 s (`IDLE_FACE_LOST_TIMEOUT_S`) |
| CLOSE_INTERACTION | WITHDRAWING | `gaze_away` | 8 s (`WITHDRAW_GAZE_AWAY_TIMEOUT_S`) |
| WITHDRAWING | IDLE | Fade complete | `WITHDRAW_FADE_DURATION_S` (default 4 s) |

The withdraw fade duration can be overridden per-image via `transitions.fade_out_ms` in metadata.json. Minimum duration is clamped to 0.1 s.

---

## Image Data Model

### Directory Structure

```
content/gallery/portrait_001/
  image.jpg
  metadata.json
  audio/
    ambient.wav
    heartbeat_maria.wav
```

Each portrait lives in its own directory under `content/gallery/`. The directory contains:

- **image.jpg** -- The photograph to display (JPEG, PNG, BMP, TIFF, or WebP).
- **metadata.json** -- All metadata, regions, audio mappings, and effect definitions.
- **audio/** -- Audio assets referenced by `metadata.json`.

Path traversal is prevented: all file paths in metadata.json are validated to remain within the portrait directory.

### metadata.json Schema

```json
{
  "version": 1,
  "id": "portrait_001",
  "title": "Maria at the Window",

  "image": {
    "filename": "image.jpg",
    "width": 3840,
    "height": 2160
  },

  "audio": {
    "ambient": {
      "file": "audio/ambient.wav",
      "loop": true,
      "fade_in_distance_cm": 200,
      "fade_in_complete_cm": 100,
      "fade_curve": "ease_in_out"
    }
  },

  "regions": [
    {
      "id": "maria_face",
      "label": "Maria's face",
      "shape": {
        "type": "polygon",
        "points_normalized": [
          [0.35, 0.10],
          [0.65, 0.10],
          [0.65, 0.45],
          [0.35, 0.45]
        ]
      },
      "gaze_trigger": {
        "dwell_time_ms": 1500,
        "min_confidence": 0.6
      },
      "heartbeat": {
        "file": "audio/heartbeat_maria.wav",
        "loop": true,
        "bass_boost": true,
        "fade_in_ms": 2000,
        "intensity_by_distance": {
          "max_distance_cm": 150,
          "min_distance_cm": 30,
          "curve": "exponential"
        }
      },
      "visual_effects": [
        {
          "type": "breathing",
          "params": {
            "amplitude": 0.003,
            "frequency_hz": 0.25
          },
          "trigger": "on_gaze_dwell",
          "fade_in_ms": 3000
        },
        {
          "type": "vignette",
          "params": {
            "radius": 0.4,
            "softness": 0.3
          },
          "trigger": "close_interaction",
          "fade_in_ms": 1500
        }
      ]
    }
  ],

  "interaction": {
    "min_interaction_distance_cm": 300,
    "close_interaction_distance_cm": 80
  },

  "transitions": {
    "fade_in_ms": 2000,
    "fade_out_ms": 2000,
    "audio_crossfade_ms": 3000
  }
}
```

### Field Reference

- **version** -- Schema version integer for forward compatibility (currently `1`).
- **id** -- Unique identifier matching the directory name.
- **title** -- Human-readable title (used in authoring UI).
- **image.filename** -- Relative path to the image file within the portrait directory.
- **image.width / image.height** -- Native resolution in pixels.
- **audio.ambient** -- Background audio that plays during PRESENCE and beyond.
  - `fade_in_distance_cm` -- Distance at which ambient volume starts ramping up (default: 200 cm).
  - `fade_in_complete_cm` -- Distance at which ambient volume reaches full (default: 100 cm).
  - `fade_curve` -- Easing function name (see [Audio Engine > Distance Curves](#distance-curves)).
- **regions[]** -- Interactive regions of the image, each with its own triggers and effects.
  - `id` -- Unique region identifier. Auto-generated if empty; duplicates get a `_N` suffix.
  - `shape.points_normalized` -- Polygon vertices in normalized coordinates (0.0--1.0).
  - `gaze_trigger.dwell_time_ms` -- How long gaze must dwell before triggering ENGAGED state (default: 1500 ms).
  - `gaze_trigger.min_confidence` -- Minimum gaze confidence required to count as "looking at" region (default: 0.6).
  - `heartbeat.file` -- Path to heartbeat audio file (relative to portrait directory).
  - `heartbeat.bass_boost` -- Whether to apply bass-boost EQ (default: true).
  - `heartbeat.fade_in_ms` -- Fade-in duration when heartbeat starts (default: 2000 ms).
  - `heartbeat.intensity_by_distance` -- Distance-to-volume mapping config:
    - `max_distance_cm` -- Distance at which heartbeat volume is 0.0 (default: 150).
    - `min_distance_cm` -- Distance at which heartbeat volume is 1.0 (default: 30).
    - `curve` -- Curve function name (default: `"exponential"`).
  - `visual_effects[].type` -- Effect type: `breathing`, `vignette`, `parallax`, `kenburns`.
  - `visual_effects[].trigger` -- When the effect activates: `on_gaze_dwell`, `close_interaction`, `presence`.
  - `visual_effects[].fade_in_ms` -- How long the effect takes to reach full intensity (default: 3000 ms).
  - `visual_effects[].params` -- Effect-specific parameters (amplitude, frequency_hz, radius, softness, etc.).
- **interaction** -- Global distance thresholds for this portrait.
  - `min_interaction_distance_cm` -- IDLE→PRESENCE threshold (default: 300 cm).
  - `close_interaction_distance_cm` -- ENGAGED→CLOSE_INTERACTION threshold (default: 80 cm).
- **transitions** -- Timing for image transitions and audio crossfades.
  - `fade_in_ms` -- Image fade-in duration (default: 2000 ms).
  - `fade_out_ms` -- Image/effect fade-out duration and withdraw duration (default: 2000 ms).
  - `audio_crossfade_ms` -- Audio crossfade during image transitions (default: 3000 ms).

All spatial coordinates are normalized to 0.0--1.0 (relative to image dimensions).

---

## Audio Engine

### Distance Curves

The audio engine maps viewer distance to volume using configurable curve functions. Each curve takes three parameters: `distance_cm`, `max_dist` (volume = 0.0), and `min_dist` (volume = 1.0).

| Curve Name | Aliases | Behavior |
|-----------|---------|----------|
| `linear` | — | Straight-line falloff |
| `ease_in` | — | Quadratic ease-in (slow start, fast end) |
| `ease_out` | — | Quadratic ease-out (fast start, slow end) |
| `ease_in_out` | `smoothstep` | Hermite smoothstep (gentle at both extremes) |
| `exponential` | `exp` | Exponential falloff (drops quickly then tapers) |

### Bass Boost

Heartbeat audio can optionally be processed with a parametric bass-boost EQ:
- Center frequency: 60 Hz
- Q factor: 0.7
- Gain: +12 dB

This emphasizes the sub-bass content that the TPA3116D2 crossover routes to the bass exciter.

### Mixer

The audio mixer operates on a sounddevice output stream callback thread at 44100 Hz stereo. It supports named streams (`"ambient"`, `"heartbeat_{region_id}"`), per-stream volume fading, and global fade-all. Inactive streams (volume at 0.0 and target at 0.0) are automatically cleaned up.

---

## Display Engine

### Effects

Four shader-driven effects, each with smooth parameter transitions using framerate-independent exponential smoothing (`1 - e^(-speed * dt)`):

| Effect | Parameters | Description |
|--------|-----------|-------------|
| `breathing` | amplitude, frequency, center, radius, intensity | Pulsing glow on a region |
| `parallax` | depth_scale, intensity | Gaze-reactive parallax shift |
| `kenburns` | zoom_speed, pan_dir, intensity | Slow zoom/pan (active in IDLE+PRESENCE) |
| `vignette` | softness, radius, intensity | Edge darkening (peaks in CLOSE_INTERACTION) |

The `fade_in_ms` parameter in visual effects overrides the transition speed so that a full 0→1 transition takes approximately that many milliseconds.

### Rendering

- Fullscreen pyglet window with OpenGL 3.3.
- Composite GLSL fragment shader receives all effect uniforms per frame.
- Texture crossfade between images with configurable duration.
- Texture ring-buffer (3 slots) defers GPU texture deletion by at least 2 frames.

---

## Authoring Tool

A web-based tool for creating and editing portrait metadata.

### Backend

- **Framework:** FastAPI, served via uvicorn.
- **Bind address:** `127.0.0.1:8080` by default (configurable via `SOULFRAME_AUTHORING_HOST` / `SOULFRAME_AUTHORING_PORT`).
- **Security:**
  - CORS restricted to localhost origins (configurable via `SOULFRAME_CORS_ORIGINS`).
  - Optional API key authentication for mutating requests (`SOULFRAME_API_KEY` env var). Uses timing-safe comparison (`hmac.compare_digest`).
  - Upload size limits: 50 MB for images, 100 MB for audio.
  - Path traversal protection on all file operations.

### Frontend

- **Canvas:** Konva.js for drawing polygon regions over the portrait image.
- **SPA:** Served as static files from `authoring/frontend/`.

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/images` | List all gallery images with summary info |
| GET | `/api/images/{id}` | Full metadata.json for one image |
| PUT | `/api/images/{id}` | Overwrite metadata.json |
| POST | `/api/images` | Upload a new image (multipart form) |
| POST | `/api/images/{id}/audio` | Upload an audio file |
| DELETE | `/api/images/{id}` | Delete an image and its directory |
| GET | `/api/images/{id}/file` | Serve the actual image file |

---

## Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.8+ | Jetson ecosystem, ML libraries |
| Face detection | MediaPipe or YuNet (OpenCV built-in fallback) | Lightweight, frees GPU |
| Gaze estimation | Head-pose via solvePnP (upgradeable to ONNX/TensorRT) | No extra model needed initially |
| Distance estimation | Iris landmark or face bbox size fallback | No extra sensor |
| Display | Pyglet 2.x + GLSL shaders (OpenGL 3.3) | Direct GPU, fullscreen |
| Audio | sounddevice + custom float32 mixer via ReSpeaker DAC | Stereo out to 2.1 amp |
| IPC | multiprocessing.Queue + SharedMemory + seqlock | Low latency, no deps |
| Authoring | FastAPI + Konva.js | Web-based region editor |
| Config | JSON metadata + Python constants | Declarative, per-image overrides |
| Validation | Pydantic v2 (authoring API) | Schema enforcement for metadata |

---

## Configuration

All defaults are defined in `soulframe/config.py`. Per-image overrides are read from each portrait's `metadata.json`.

### Default Values

| Constant | Value | Description |
|----------|-------|-------------|
| `DISPLAY_WIDTH` | 1920 | Display resolution width |
| `DISPLAY_HEIGHT` | 1080 | Display resolution height |
| `DISPLAY_FPS` | 60 | Target display framerate |
| `CAMERA_INDEX` | 0 | V4L2 camera device index |
| `CAMERA_WIDTH` | 640 | Camera capture width |
| `CAMERA_HEIGHT` | 480 | Camera capture height |
| `CAMERA_FPS` | 30 | Camera capture framerate |
| `AUDIO_SAMPLE_RATE` | 44100 | Audio output sample rate |
| `AUDIO_CHANNELS` | 2 | Stereo output |
| `AUDIO_BLOCK_SIZE` | 1024 | sounddevice block size |
| `AUDIO_DEVICE_NAME` | `"seeed"` | Substring match for ReSpeaker |
| `IDLE_IMAGE_CYCLE_SECONDS` | 300 | 5 minutes between image cycles |
| `PRESENCE_DISTANCE_CM` | 300 | IDLE→PRESENCE threshold |
| `CLOSE_INTERACTION_DISTANCE_CM` | 80 | ENGAGED→CLOSE threshold |
| `PRESENCE_LOST_TIMEOUT_S` | 3.0 | Face-lost timeout in PRESENCE |
| `IDLE_FACE_LOST_TIMEOUT_S` | 5.0 | Face-lost timeout (global) |
| `GAZE_DWELL_MS` | 1500 | Default gaze dwell time |
| `GAZE_MIN_CONFIDENCE` | 0.6 | Default minimum gaze confidence |
| `WITHDRAW_GAZE_AWAY_TIMEOUT_S` | 8.0 | Gaze-away timeout |
| `WITHDRAW_FADE_DURATION_S` | 4.0 | Default withdraw fade |
| `DEFAULT_FADE_IN_MS` | 2000 | Default image fade-in |
| `DEFAULT_FADE_OUT_MS` | 2000 | Default image fade-out |
| `DEFAULT_AUDIO_CROSSFADE_MS` | 3000 | Default audio crossfade |
| `VISION_STALE_TIMEOUT_S` | 2.0 | Expire stale vision data |

---

## Development Phases

### Phase 0 -- Hardware Validation

Confirm all hardware components work individually on the Jetson Orin NX.

**Validation criteria:**
- Camera captures frames via V4L2 at target resolution.
- Display shows fullscreen content over mini-HDMI.
- Audio plays through ReSpeaker DAC to amplifier and all three exciters.
- System boots and runs without thermal throttling for 30 minutes.

### Phase 1 -- Skeleton Architecture

Stand up the four-process architecture with IPC. Processes start, communicate, and shut down cleanly.

**Validation criteria:**
- All four processes start via a single launcher.
- Vision writes to shared memory; Brain reads it successfully.
- Brain sends commands over Queue; Display and Audio receive them.
- Clean shutdown with no orphan processes or leaked shared memory.

### Phase 2 -- Vision Pipeline

Camera capture, face detection, gaze estimation, and distance estimation running in the Vision process and publishing to shared memory at 30 Hz.

**Validation criteria:**
- Face detection works at >= 20 FPS with one face in frame.
- Gaze screen coordinates map correctly to display regions.
- Distance estimation is accurate within +/- 20 cm at 1-meter range.
- Shared memory struct updates at >= 20 Hz.

### Phase 3 -- Display Rendering

Fullscreen image display with GLSL shader effects (Ken Burns, parallax, breathing, vignette) driven by commands from Brain.

**Validation criteria:**
- Image loads and displays fullscreen on the OLED at native resolution.
- Ken Burns subtle zoom/pan runs smoothly at 60 FPS.
- Breathing effect visually animates a region with configurable amplitude/frequency.
- Vignette effect activates and deactivates with smooth fade.
- Image transitions (fade in/out) work between portraits.

### Phase 4 -- Audio Engine

Custom audio mixer with distance-based volume, heartbeat playback, and ambient layering through the 2.1 exciter system.

**Validation criteria:**
- Ambient audio loops seamlessly with distance-based fade.
- Heartbeat audio plays with bass boost routed to sub exciter.
- Volume scales smoothly with viewer distance (no clicks or pops).
- Multiple audio layers mix correctly in float32.
- Audio crossfade works during image transitions.

### Phase 5 -- State Machine Integration

Full interaction loop: IDLE through CLOSE INTERACTION and WITHDRAWING, driven by vision data and metadata regions.

**Validation criteria:**
- State transitions match the state diagram for all paths.
- Image cycling works in IDLE (5-minute timer).
- PRESENCE pauses cycling and triggers ambient audio.
- ENGAGED activates region-specific effects and heartbeat.
- CLOSE INTERACTION peaks all effects.
- WITHDRAWING gracefully fades everything back to IDLE.
- Global 5-second face-lost timeout returns to WITHDRAWING from ENGAGED/CLOSE states.

### Phase 6 -- Authoring Tool

Web-based tool for creating and editing portrait metadata (region polygons, audio mappings, effect parameters).

**Validation criteria:**
- FastAPI backend serves the authoring UI and reads/writes metadata.json.
- Konva.js canvas allows drawing polygon regions over the portrait image.
- Audio file upload and assignment to regions works.
- Effect parameters are editable.
- Saved metadata validates against the schema and is loadable by Brain.

---

## Known Blockers

1. **~~Arducam GMSL2 driver compatibility~~ — RESOLVED**
   - The GMSL2 camera works with a device tree patch that changes `pix_clk_hz` from 182.4 MHz to 300 MHz. No kernel module changes needed. The SerDes link is transparent.
   - Fix applied and verified on 2026-02-28. See `~/GMSL2_CAMERA_FIX_GUIDE.md` for re-application after OS re-flash, and `docs/HARDWARE_AND_PLATFORM.md` for full camera details.
   - The Vision process auto-detects Jetson and uses `nvarguscamerasrc` (GStreamer/ISP), with automatic V4L2 fallback for USB webcams.

2. **Thermal management in enclosed frame**
   - The Jetson Orin NX generates significant heat under sustained workload. A 3D-printed enclosure restricts airflow.
   - **Mitigation:** Design ventilation channels into the frame. Monitor thermals continuously with `tegrastats`. Throttle vision FPS if temperatures exceed safe thresholds.
