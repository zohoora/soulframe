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
7. [Tech Stack](#tech-stack)
8. [Development Phases](#development-phases)
9. [Known Blockers](#known-blockers)

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
| Computer | NVIDIA Jetson Orin NX Dev Kit | - | JetPack 5.1.3, Ubuntu 20.04, 16 GB RAM |
| Display | ASUS ZenScreen 16" OLED | mini-HDMI | USB-C DP Alt Mode NOT supported by Jetson |
| Camera | Arducam GMSL2 8MP (IMX219) | 22-pin flex + FAKRA coax | V4L2 compatible |
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
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+   +-----v------+  +----v--------+
     |   VISION   |   |  DISPLAY   |  |    AUDIO    |
     |  Process   |   |  Process   |  |   Process   |
     | Camera     |   | Pyglet/GL  |  | sounddevice |
     | Face Det.  |   | Shaders    |  | Custom mixer|
     | Gaze Est.  |   | Fullscreen |  | 2.1 routing |
     +------------+   +------------+  +-------------+
```

### Why Multi-Process

- **Python GIL** prevents true parallelism in a single process.
- **OpenGL context** must be owned by exactly one process (Display).
- **Audio** needs a low-latency, dedicated process to avoid underruns.
- **Vision** is CPU+GPU heavy (face detection, gaze estimation).
- **Orin NX has 8 cores** -- multi-process takes full advantage of the hardware.

---

## IPC: Shared Memory Struct

**Direction:** Vision --> Brain, updated at approximately 30 Hz.

```
Offset  Size   Field
------  ----   -----
0       4      frame_counter (uint32)
4       4      num_faces (uint32)
8       4      face_distance_cm (float32)
12      4      gaze_screen_x (float32)     -- normalized 0.0-1.0
16      4      gaze_screen_y (float32)     -- normalized 0.0-1.0
20      4      gaze_confidence (float32)
24      4      head_yaw (float32)
28      4      head_pitch (float32)
32      8      timestamp_ns (uint64)
------  ----
Total: 40 bytes
```

All spatial coordinates are normalized to 0.0--1.0 where applicable. The Brain process reads this struct each tick to drive the state machine. Commands from Brain to Display and Audio travel over `multiprocessing.Queue`.

---

## Interaction State Machine

### State Diagram

```
                        face_lost (5s timeout)
             +------------------------------------------+
             |                                          |
             v            face_detected                 |
       +----------+    (distance < 300cm)         +-----+------+
       |          +-----------------------------> |            |
       |   IDLE   |                               |  PRESENCE  |
       |          | <-----------------------------+            |
       +----+-----+    face_lost (3s timeout)     +-----+------+
            |                                           |
            | (5 min timer)                             | gaze_on_region
            +---> [next image]                          | (dwell > 1.5s)
                                                        v
                                                  +-----+------+
                                                  |  ENGAGED   |
                                                  +-----+------+
                                                        |
                                                        | distance < 80cm
                                                        v
                                                  +-----+------+
                                                  |   CLOSE    |
                                                  | INTERACTION|
                                                  +-----+------+
                                                        |
                                                        | face_lost or
                                                        | gaze_away 8s
                                                        v
                                                  +-----+------+
                                                  | WITHDRAWING|
                                                  +-----+------+
                                                        |
                                                        | fade complete
                                                        v
                                                   back to IDLE
```

### State Behavior Table

| State | What Happens | Audio | Visuals |
|-------|-------------|-------|---------|
| **IDLE** | Normal picture frame. Images cycle every 5 min. | Silent | Static image, very subtle Ken Burns |
| **PRESENCE** | Viewer detected. Image cycle paused. | Ambient audio fades in (volume scales with distance) | Ken Burns, gentle parallax |
| **ENGAGED** | Gaze locked on a person region for 1.5 s+ | Heartbeat begins. Bass scales with distance | Region-specific effects (breathing, etc.) |
| **CLOSE** | Viewer very close (< 80 cm) | Full bass, maximum heartbeat | All effects peak, vignette |
| **WITHDRAWING** | Viewer leaving. 3--5 s graceful fade. | All audio fades to zero | Effects ease back to static |

### Transition Summary

| From | To | Trigger |
|------|----|---------|
| IDLE | PRESENCE | `face_detected` AND `distance < 300 cm` |
| PRESENCE | IDLE | `face_lost` for 3 s |
| PRESENCE | ENGAGED | `gaze_on_region` dwell > 1.5 s |
| ENGAGED | CLOSE | `distance < 80 cm` |
| CLOSE | WITHDRAWING | `face_lost` OR `gaze_away` for 8 s |
| WITHDRAWING | IDLE | Fade complete (3--5 s) |
| Any state | IDLE | `face_lost` for 5 s (global timeout) |

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

- **image.jpg** -- The photograph to display (JPEG or PNG).
- **metadata.json** -- All metadata, regions, audio mappings, and effect definitions.
- **audio/** -- Audio assets referenced by `metadata.json`.

### metadata.json Schema

```json
{
  "version": "1.0",
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
      "fade_in_distance_cm": 300,
      "fade_in_complete_cm": 150,
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
          "300": 0.0,
          "150": 0.3,
          "80": 0.7,
          "30": 1.0
        }
      },
      "visual_effects": [
        {
          "type": "breathing",
          "params": {
            "amplitude": 0.003,
            "frequency_hz": 0.25
          },
          "trigger": "gaze_dwell",
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
    "fade_in_ms": 1500,
    "fade_out_ms": 1500,
    "audio_crossfade_ms": 2000
  }
}
```

### Field Reference

- **version** -- Schema version string for forward compatibility.
- **id** -- Unique identifier matching the directory name.
- **title** -- Human-readable title (used in authoring UI).
- **image.filename** -- Relative path to the image file within the portrait directory.
- **image.width / image.height** -- Native resolution in pixels.
- **audio.ambient** -- Background audio that plays during PRESENCE and beyond.
  - `fade_in_distance_cm` / `fade_in_complete_cm` -- Distance range over which volume ramps from 0 to 1.
  - `fade_curve` -- Easing function name (`linear`, `ease_in`, `ease_out`, `ease_in_out`).
- **regions[]** -- Interactive regions of the image, each with its own triggers and effects.
  - `shape.points_normalized` -- Polygon vertices in normalized coordinates (0.0--1.0).
  - `gaze_trigger.dwell_time_ms` -- How long gaze must dwell before triggering ENGAGED state.
  - `gaze_trigger.min_confidence` -- Minimum gaze confidence required to count as "looking at" region.
  - `heartbeat.intensity_by_distance` -- Maps distance (cm) to intensity (0.0--1.0). Interpolated linearly.
  - `visual_effects[].type` -- Effect type (`breathing`, `vignette`, `parallax`, `glow`, etc.).
  - `visual_effects[].trigger` -- When the effect activates (`gaze_dwell`, `close_interaction`, `presence`).
- **interaction** -- Global distance thresholds for this portrait.
- **transitions** -- Timing for image transitions and audio crossfades.

All spatial coordinates are normalized to 0.0--1.0 (relative to image dimensions).

---

## Tech Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.8+ | Jetson ecosystem, ML libraries |
| Face detection | MediaPipe or YuNet (OpenCV built-in fallback) | Lightweight, frees GPU |
| Gaze estimation | Head-pose via solvePnP (upgradeable to MobileGaze/TensorRT) | No extra model needed |
| Distance estimation | Iris landmark or face bbox size fallback | No extra sensor |
| Display | Pyglet 2.x + GLSL shaders (OpenGL 3.3) | Direct GPU, fullscreen |
| Audio | sounddevice + custom float32 mixer via ReSpeaker DAC | Stereo out to 2.1 amp |
| IPC | multiprocessing.Queue + SharedMemory | Low latency, no deps |
| Authoring | FastAPI + Konva.js | Web-based region editor |
| Config | JSON + Pydantic | Declarative, validated |

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
- Global 5-second face-lost timeout returns to IDLE from any state.

### Phase 6 -- Authoring Tool

Web-based tool for creating and editing portrait metadata (region polygons, audio mappings, effect parameters).

**Validation criteria:**
- FastAPI backend serves the authoring UI and reads/writes metadata.json.
- Konva.js canvas allows drawing polygon regions over the portrait image.
- Audio file upload and assignment to regions works.
- Effect parameters are editable with live preview.
- Saved metadata validates against the schema and is loadable by Brain.

---

## Known Blockers

1. **Arducam GMSL2 driver compatibility with JetPack 5.1.3**
   - The GMSL2 camera requires kernel-level driver support that may not be included in stock JetPack.
   - **Mitigation:** Fall back to a USB webcam (e.g., Logitech C920) for development. The Vision process abstracts the camera source, so swapping is a config change.

2. **Thermal management in enclosed frame**
   - The Jetson Orin NX generates significant heat under sustained workload. A 3D-printed enclosure restricts airflow.
   - **Mitigation:** Design ventilation channels into the frame. Monitor thermals continuously with `tegrastats`. Throttle vision FPS if temperatures exceed safe thresholds.
