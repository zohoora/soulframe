# Soul Frame

Interactive art installation — photographs that respond to viewer presence and gaze.

## Quick Start

```bash
git clone https://github.com/zohoora/soulframe.git
cd soulframe
./scripts/setup.sh
.venv/bin/python -m soulframe
```

## Hardware

| Component | Details |
|-----------|---------|
| Jetson Orin NX 16GB (Seeed reComputer J4012) | JetPack 6.2.1, Ubuntu 22.04, L4T R36.4.3 |
| ASUS ZenScreen 16" OLED | mini-HDMI connection |
| Arducam GMSL2 8MP (IMX219) | CSI-2 via GMSL2 SerDes on CAM0 (USB webcam fallback supported) |
| HiLetgo TPA3116D2 2.1 amp | Drives all three exciters |
| 2x Dayton DAEX25FHE-4 | Voice exciters (L/R) |
| 1x Dayton TT25-8 | Bass exciter (Sub) |
| SeeedStudio ReSpeaker Mic Array v2.0 | USB input, 3.5mm out to amp |

### Wiring

```
Jetson USB --> ReSpeaker v2.0 (3.5mm out) --> TPA3116D2 amp --> L: voice exciter
                                                             --> R: voice exciter
                                                             --> Sub: bass exciter
```

## System Dependencies

```bash
sudo apt-get install -y \
  python3-pip python3-venv python3-dev \
  portaudio19-dev libsndfile1-dev \
  libgl1-mesa-dev libasound2-dev
```

## Running

```bash
python -m soulframe              # full installation
python -m soulframe --authoring  # web authoring tool at http://localhost:8080
python -m soulframe --vision     # vision process only (debug)
python -m soulframe --display    # display process only (debug)
python -m soulframe --audio      # audio process only (debug)
```

All modes accept `--log-level {DEBUG,INFO,WARNING,ERROR}` (default: `INFO`).

## Configuration

Soul Frame is configured via environment variables. All are optional with sensible defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `SOULFRAME_ROOT` | Project directory | Override the project root path |
| `SOULFRAME_AUTHORING_HOST` | `127.0.0.1` | Bind address for the authoring server |
| `SOULFRAME_AUTHORING_PORT` | `8080` | Port for the authoring server |
| `SOULFRAME_API_KEY` | *(empty)* | API key for mutating authoring requests (POST/PUT/DELETE) |
| `SOULFRAME_CORS_ORIGINS` | localhost only | Comma-separated CORS origins for the authoring API |

To make the authoring tool accessible from another machine on the network:

```bash
export SOULFRAME_AUTHORING_HOST=0.0.0.0
export SOULFRAME_API_KEY=my-secret-key
python -m soulframe --authoring
```

When `SOULFRAME_API_KEY` is set, all mutating API requests must include the `X-Api-Key` header.

## Adding Content

Each portrait lives in its own directory under `content/gallery/`:

```
content/gallery/my_portrait/
  image.jpg
  metadata.json
  audio/
    ambient.wav
    heartbeat.wav
```

The easiest way to create and edit `metadata.json` is through the authoring tool
(`python -m soulframe --authoring`). See `docs/SPEC.md` for the full metadata
schema reference.

## Auto-Start on Boot

Systemd services (recommended):

```bash
# Main installation
sudo cp systemd/soulframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable soulframe

# Authoring tool (optional, separate service)
sudo cp systemd/soulframe-authoring.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable soulframe-authoring
```

Or desktop autostart:

```bash
cp systemd/soulframe.desktop ~/.config/autostart/
```

## Jetson Performance

Set maximum performance mode before running:

```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

Monitor resource usage:

```bash
./scripts/monitor.sh
```

## Architecture

Four Python processes communicate via shared memory and command queues:

- **brain** -- orchestrates state, reacts to vision events, drives display and audio
- **vision** -- camera capture, face detection, gaze estimation, distance estimation
- **display** -- OpenGL rendering with GLSL shader-driven image effects
- **audio** -- spatial audio playback with distance-based volume and bass-boost EQ

Vision writes a 44-byte struct (4-byte seqlock + 40-byte data) to shared memory at ~30 Hz. Brain reads it to drive a 5-state interaction state machine (IDLE, PRESENCE, ENGAGED, CLOSE_INTERACTION, WITHDRAWING). Commands to Display and Audio flow over `multiprocessing.Queue`.

See `docs/SPEC.md` for the full technical specification and `docs/HARDWARE_AND_PLATFORM.md` for detailed hardware capabilities, constraints, and pitfalls.

## Project Structure

```
soulframe/
  soulframe/           # main application package
    brain/             # coordinator, state machine, image manager, interaction model
    vision/            # camera, face detection, gaze estimation, distance estimation
    display/           # renderer, effects, GLSL shaders
    audio/             # audio stream, mixer, distance-volume curves
    shared/            # IPC (seqlock), types, smoothing, geometry
  authoring/           # web-based content authoring tool
    backend/           # FastAPI API (routes, models, app)
    frontend/          # Konva.js single-page app
  content/             # portrait galleries
    gallery/
  models/              # ML model weights
  calibration/         # screen calibration data
  scripts/             # setup and monitoring scripts
  systemd/             # service and autostart files
  docs/                # specification and audit docs
```
