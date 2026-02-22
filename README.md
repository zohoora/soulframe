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
| Jetson Orin NX Dev Kit | JetPack 5.1.3, Ubuntu 20.04 |
| ASUS ZenScreen 16" OLED | mini-HDMI connection |
| Arducam GMSL2 8MP (IMX219) | USB webcam fallback supported |
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
python -m soulframe --authoring  # web authoring tool at http://<jetson-ip>:8080
python -m soulframe --vision     # vision process only (debug)
python -m soulframe --display    # display process only (debug)
python -m soulframe --audio      # audio process only (debug)
```

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

Systemd service (recommended):

```bash
sudo cp systemd/soulframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable soulframe
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
- **vision** -- camera capture, face detection, gaze estimation
- **display** -- OpenGL rendering, shader-driven image effects
- **audio** -- spatial audio playback, reactive soundscapes

See `docs/SPEC.md` for the full technical specification.

## Project Structure

```
soulframe/
  soulframe/           # main application package
    brain/
    vision/
    display/
    audio/
    shared/
  authoring/           # web-based content authoring tool
    backend/
    frontend/
  content/             # portrait galleries
    gallery/
  models/              # ML model weights
  calibration/         # camera calibration data
  scripts/             # setup and monitoring scripts
  systemd/             # service and autostart files
```
