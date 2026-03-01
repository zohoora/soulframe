# Soul Frame — Hardware & Platform Reference

> **Purpose**: Definitive reference for AI coding agents working on Soul Frame.
> This document describes the exact hardware, software stack, capabilities,
> constraints, and known pitfalls of the deployment system. Consult this
> before adding features, optimizing pipelines, or choosing libraries.
>
> **This application runs exclusively on this one system.** There is no
> second target. Design for this hardware, not for portability.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Compute Module — Jetson Orin NX 16GB](#2-compute-module--jetson-orin-nx-16gb)
3. [Camera System — GMSL2 IMX219](#3-camera-system--gmsl2-imx219)
4. [Display — ASUS ZenScreen 16" OLED](#4-display--asus-zenscreen-16-oled)
5. [Audio Chain](#5-audio-chain)
6. [Software Stack](#6-software-stack)
7. [GPU and Accelerator Capabilities](#7-gpu-and-accelerator-capabilities)
8. [GStreamer Hardware Pipeline](#8-gstreamer-hardware-pipeline)
9. [Vision Pipeline Constraints](#9-vision-pipeline-constraints)
10. [Thermal and Power Management](#10-thermal-and-power-management)
11. [Storage and Filesystem](#11-storage-and-filesystem)
12. [Networking](#12-networking)
13. [Known Pitfalls and Hard Constraints](#13-known-pitfalls-and-hard-constraints)
14. [What You CAN Do (Capabilities)](#14-what-you-can-do-capabilities)
15. [What You CANNOT Do (Hard Limits)](#15-what-you-cannot-do-hard-limits)

---

## 1. System Overview

```
┌──────────────────────────────────────────────────────┐
│  Seeed Studio reComputer J4012 (J401 carrier board)  │
│                                                      │
│  ┌──────────────────────────────────────────┐        │
│  │  NVIDIA Jetson Orin NX 16GB              │        │
│  │  8-core ARM Cortex-A78AE                 │        │
│  │  1024-core Ampere GPU                    │        │
│  │  2x NVDLA v2.0 engines                  │        │
│  │  CUDA 12.6 / TensorRT 10.3              │        │
│  └──────────────────────────────────────────┘        │
│                                                      │
│  Ports used:                                         │
│  ├── CAM0 (CSI-2, 22-pin) ── Arducam GMSL2 receiver │
│  ├── HDMI ──────────────────── ASUS ZenScreen OLED   │
│  ├── USB ───────────────────── ReSpeaker Mic v2.0    │
│  └── NVMe ─────────────────── 128GB system SSD       │
└──────────────────────────────────────────────────────┘
```

---

## 2. Compute Module — Jetson Orin NX 16GB

| Spec | Value |
|------|-------|
| **SoC** | NVIDIA Orin (Tegra T234) |
| **CPU** | 8x ARM Cortex-A78AE @ up to 2.0 GHz |
| **GPU** | 1024-core NVIDIA Ampere (GA10B), up to 918 MHz |
| **DLA** | 2x NVDLA v2.0 engines (dedicated inference accelerators) |
| **RAM** | 16 GB LPDDR5 (shared between CPU and GPU — unified memory) |
| **Storage** | 128 GB NVMe SSD (91 GB free as of 2026-02-28) |
| **Power Mode** | 40W (NV Power Mode 4 — not max performance) |
| **Max Power Mode** | `sudo nvpmodel -m 0` + `sudo jetson_clocks` for full 25W sustained |
| **Carrier Board** | Seeed Studio J401 (reComputer J4012) |

### OS and Kernel

| Component | Version |
|-----------|---------|
| **Ubuntu** | 22.04.5 LTS (Jammy) |
| **Architecture** | aarch64 (ARM 64-bit) |
| **JetPack** | 6.2.1 (`nvidia-jetpack 6.2.1+b38`) |
| **L4T** | R36.4.3 |
| **Kernel** | 5.15.148-tegra |
| **Python** | 3.10.12 (system) |

### Memory Architecture

The Jetson uses **unified memory** — CPU and GPU share the same 16 GB LPDDR5
pool. There is no discrete VRAM. This means:

- GPU tensors and CPU arrays can share the same physical memory (zero-copy possible via NVMM)
- Large GPU allocations directly reduce memory available to the CPU and vice versa
- Current usage at idle: ~3.2 GB used, ~11 GB available
- The `memory:NVMM` buffer type in GStreamer keeps frames in GPU-accessible memory without copying to CPU RAM

**Implication for development**: You cannot assume "unlimited GPU memory" — budget
carefully if loading large models. A 2 GB model + 1080p frame buffers + OpenGL
textures + Python overhead adds up quickly in 16 GB shared space.

---

## 3. Camera System — GMSL2 IMX219

### Hardware

| Component | Details |
|-----------|---------|
| **Kit** | Arducam GMSL2 Camera Extension Kit (SKU B0570) |
| **Sensor** | Sony IMX219 8MP, 10-bit Bayer, I2C address `0x10` |
| **Deserializer** | Maxim MAX96716A (on Arducam receiver board), I2C address `0x0c` |
| **Serializer** | Maxim MAX96717 (on camera module PCB, transparent) |
| **Topology** | Jetson CAM0 (i2c-2) ↔ Deserializer (`0x0c`) ↔ FAKRA coax ↔ IMX219 (`0x10`) |
| **Physical connection** | Receiver board → 15-to-22 pin adapter → **CAM0** (CSI-2 port) |
| **Receiver power** | External 5V via USB-C power injection |
| **CSI lanes** | 2 |
| **I2C bus** | `i2c-9` (muxed channel for CAM0, parent `i2c-2` / `3180000.i2c`) |
| **SerDes transparency** | Both I2C and CSI-2 pass through unchanged — no driver needed for deserializer |

### Sensor Modes

The ISP selects a mode based on the resolution/fps requested in the GStreamer caps.
**You must request an exact native resolution** — the ISP will not arbitrarily scale.

| Mode | Resolution | Max FPS | Aspect Ratio | Notes |
|------|-----------|---------|--------------|-------|
| 0 | 3280x2464 | 21 | 4:3 | Full sensor, max detail |
| 1 | 3280x1848 | 28 | ~16:9 | Wide crop |
| 2 | 1920x1080 | 30 | 16:9 | Standard HD |
| 3 | 1640x1232 | 30 | 4:3 | Balanced |
| 4 | **1280x720** | **60** | 16:9 | **Used by Soul Frame** (high fps, low latency) |

**Soul Frame uses mode 4** (1280x720 @ 60fps capture, hardware-resized to 640x480
for the vision pipeline). This gives the fastest frame rate for responsive face/gaze
tracking while keeping CPU load minimal.

### Vision Topology

```
Jetson CAM0 (i2c-2) <──> Arducam GMSL2 Deserializer (0x0c) <── FAKRA Cable ──> IMX219 Sensor (0x10)
```

The GMSL2 SerDes link is fully transparent — no kernel driver is needed for the
deserializer. I2C passes through (the kernel talks directly to the IMX219 at
`9-0010`), and CSI-2 video data passes through unchanged. From the Jetson's
perspective, the IMX219 appears as if it were directly connected to CAM0.

### Camera Pipeline (How Frames Flow)

```
IMX219 sensor (10-bit Bayer, 1280x720 @ 60fps)
    │  GMSL2 SerDes link (transparent — I2C + CSI-2 passthrough)
    ▼
Jetson CSI-2 receiver (2 lanes on CAM0, i2c-2)
    │
    ▼
NVIDIA VI (Video Input) engine → /dev/video0
    │
    ▼
NVIDIA ISP (hardware Image Signal Processor)
    ├── Bayer demosaic
    ├── Auto white balance (AWB)
    ├── Auto exposure (AE)
    ├── Noise reduction
    └── Tone mapping
    │
    ▼
nvarguscamerasrc (GStreamer element) → NV12 in NVMM memory
    │
    ▼
nvvidconv (hardware resize 1280x720 → 640x480, NV12 → BGRx)
    │
    ▼
videoconvert (BGRx → BGR, for OpenCV)
    │
    ▼
appsink → numpy array (640, 480, 3) uint8 BGR
    │
    ▼
Vision pipeline (face detection, gaze, distance)
```

### Critical Camera Rules

1. **ALWAYS use `nvarguscamerasrc`**, never `v4l2src`. The IMX219 outputs raw Bayer
   data — without the ISP you get unusable green-tinted garbage.

2. **ALWAYS use `cv2.CAP_GSTREAMER`** backend when opening the camera in OpenCV.
   `cv2.VideoCapture(0)` or `cv2.VideoCapture("/dev/video0")` will try V4L2 and
   bypass the ISP.

3. **Only one process can use the camera.** `nvarguscamerasrc` acquires an exclusive
   lock via the Argus daemon. A second open will fail silently.

4. **The camera takes ~500ms to initialize.** The first 1-2 frames may have incorrect
   exposure. Do not make decisions based on the very first frame.

5. **`nvargus-daemon` must be running.** It's a systemd service that auto-starts, but
   can need a restart after a crash: `sudo systemctl restart nvargus-daemon`

6. **The GMSL2 link requires a patched device tree.** The stock `pix_clk_hz` of
   182.4 MHz causes CSI lane desync. The patched DTB at
   `/boot/tegra234-gmsl-imx219-patched.dtb` sets it to 300 MHz. See
   `~/GMSL2_CAMERA_FIX_GUIDE.md` if the fix needs to be re-applied.

7. **The receiver board must be powered via USB-C before boot.** If power is applied
   after the kernel loads, the IMX219 will not be detected on I2C.

8. **`appsink drop=1 max-buffers=1` is mandatory** for real-time applications. Without
   it, frames queue up and you get seconds-old stale data.

### Environment Variable Override

Set `SOULFRAME_CAMERA_FORCE_V4L2=1` to bypass the GStreamer/ISP path and use plain
V4L2 (e.g., for a USB webcam during development on a non-Jetson machine). The camera
module auto-detects Jetson by checking `/proc/device-tree/compatible` for "tegra".

---

## 4. Display — ASUS ZenScreen 16" OLED

| Spec | Value |
|------|-------|
| **Model** | ASUS ZenScreen 16" OLED (MB166CR or similar) |
| **Connection** | mini-HDMI (USB-C DisplayPort Alt Mode is NOT supported by Jetson) |
| **Resolution** | 1920x1080 @ 60 Hz |
| **Output** | `HDMI-0` (primary, only connected display) |

### Display Constraints

- **Only HDMI works.** The Jetson Orin NX does not support USB-C DisplayPort
  Alt Mode. Do not attempt to use USB-C for display output.
- **OLED burn-in risk.** Static UI elements displayed for hours will burn in.
  The application uses subtle Ken Burns movement and image cycling to mitigate this.
- **No second display.** There is exactly one display output. Debug UIs must
  either overlay on the main display or be accessed via network (e.g., the
  web authoring tool).
- **Screen blanking must be disabled.** The setup script runs `xset s off && xset -dpms`
  to prevent the display from sleeping during the installation.

### OpenGL

- **API**: OpenGL 3.3+ (via Pyglet 2.x and GLSL shaders)
- **Renderer**: NVIDIA Tegra (integrated Ampere GPU)
- **Target framerate**: 60 FPS (vsync to HDMI output)
- **Texture format**: RGB/RGBA 8-bit per channel
- The GPU shares memory with the CPU — large textures reduce RAM available for ML models

---

## 5. Audio Chain

```
Python (sounddevice, float32 mixer)
    ↓
ReSpeaker Mic Array v2.0 (USB, WM8960 DAC)
    ↓ 3.5mm stereo analog
TPA3116D2 2.1 Amplifier (24V DC powered)
    ├── L channel → Dayton DAEX25FHE-4 (voice exciter, 4Ω)
    ├── R channel → Dayton DAEX25FHE-4 (voice exciter, 4Ω)
    └── Sub channel → Dayton TT25-8 (bass exciter, 8Ω)
```

| Parameter | Value |
|-----------|-------|
| **Sample rate** | 44100 Hz |
| **Channels** | 2 (stereo) |
| **Block size** | 1024 samples |
| **Audio device** | Matched by substring `"seeed"` in device name |
| **Format** | float32 (internal mixer) |

### Audio Constraints

- **Stereo only** — the ReSpeaker outputs L/R. Bass is derived by the amplifier's
  built-in hardware crossover, not by software routing.
- **Bass boost is software EQ** — 3-band parametric targeting 60 Hz, Q=0.7, +12 dB.
  This emphasizes content for the sub exciter.
- **Exciters are not speakers** — they vibrate a surface (the picture frame). Frequency
  response depends heavily on the frame material and mounting. Don't expect flat
  response below 80 Hz or above 15 kHz.
- **No microphone input used** — the ReSpeaker has a mic array but Soul Frame only
  uses the DAC output. The mic could be used for future features (voice interaction).

---

## 6. Software Stack

### System Packages

| Package | Version | Purpose |
|---------|---------|---------|
| Python | 3.10.12 | Application runtime |
| OpenCV | 4.8.0 | Camera capture, face detection, image processing |
| GStreamer | 1.20.3 | Camera pipeline (compiled into OpenCV) |
| CUDA Toolkit | 12.6 | GPU compute |
| TensorRT | 10.3.0 | Optimized inference engine |
| L4T Multimedia | 36.4.7 | Hardware video encode/decode, NVMM |

### Python Dependencies (from requirements.txt)

| Package | Purpose | Notes |
|---------|---------|-------|
| `numpy>=1.19` | Array operations | Frames are numpy arrays |
| `opencv-python>=4.5` | Camera + vision | System OpenCV 4.8.0 has GStreamer |
| `pyglet>=2.0` | Display / OpenGL | Fullscreen rendering |
| `sounddevice>=0.4` | Audio I/O | Callback-based, low latency |
| `soundfile>=0.10` | WAV/FLAC loading | |
| `scipy>=1.5` | Signal processing | Used in audio EQ |
| `pydantic>=2.0,<2.6` | Data validation | Authoring API schemas |
| `fastapi>=0.100` | Web API | Authoring tool backend |
| `uvicorn>=0.15` | ASGI server | Serves FastAPI |
| `Pillow>=8.0` | Image loading | Gallery images |

### Optional ML Libraries (Not Yet Installed)

These are referenced in the code but not currently installed in the system Python or
any venv. They are needed to activate higher-quality vision backends:

| Library | Purpose | Install |
|---------|---------|---------|
| **MediaPipe** | Face detection (preferred backend, 6 keypoints) | `pip install mediapipe` — **check aarch64 compatibility** |
| **TensorRT Python** | GPU-accelerated gaze estimation | Usually available via JetPack: `pip install tensorrt` |
| **ONNX Runtime** | ONNX model inference (alternative to TensorRT) | `pip install onnxruntime-gpu` |

**Current state**: Without MediaPipe, face detection falls back to YuNet (OpenCV
built-in, requires `face_detection_yunet.onnx` in `models/`). Without TensorRT or
ONNX models, gaze estimation falls back to head-pose via `cv2.solvePnP` (functional
but less accurate).

### GStreamer Plugins Available

These are hardware-accelerated and essentially free (run on dedicated engines, not CPU/GPU):

| Plugin | Purpose |
|--------|---------|
| `nvarguscamerasrc` | Camera capture via ISP |
| `nvvidconv` | Hardware colorspace conversion and resize |
| `nvjpegenc` / `nvjpegdec` | Hardware JPEG encode/decode |
| `nvv4l2h264enc` | Hardware H.264 encode |
| `nvv4l2h265enc` | Hardware H.265/HEVC encode |
| `nvv4l2decoder` | Hardware video decode (H.264, H.265, VP9) |
| `nvegltransform` / `nveglglessink` | GPU-direct display output |

---

## 7. GPU and Accelerator Capabilities

### GPU — 1024-core Ampere

- **Architecture**: NVIDIA Ampere (same generation as RTX 30-series, but mobile)
- **CUDA cores**: 1024
- **CUDA version**: 12.6
- **Tensor cores**: Yes (INT8, FP16 — useful for TensorRT inference)
- **Memory**: Shared with CPU from the 16 GB LPDDR5 pool
- **Peak FP32**: ~5.3 TFLOPS (at max clocks)
- **Peak INT8**: ~100+ TOPS (via tensor cores, with TensorRT)

**What the GPU is good for:**
- TensorRT inference (face detection, gaze estimation, any neural network)
- CUDA-accelerated image processing (OpenCV CUDA module)
- OpenGL rendering (display process uses GLSL shaders)
- Hardware video encode/decode

**What the GPU is NOT good for:**
- Training neural networks (too little memory, too few cores)
- Running multiple large models simultaneously (memory pressure)

### DLA — 2x NVDLA v2.0

The Orin NX has **two Deep Learning Accelerator** cores. These are dedicated
inference engines separate from the GPU — they can run models while the GPU
handles rendering.

- Supported by TensorRT (specify `device_type=trt.DeviceType.DLA`)
- Good for: INT8/FP16 inference of common vision models
- Limitation: Not all layer types are supported — TensorRT falls back unsupported
  layers to GPU automatically
- **Currently unused** — a future optimization could run face detection on DLA
  while gaze estimation runs on GPU

### Hardware Codec Engines

Dedicated silicon for video encode/decode — these are separate from both CPU and GPU:

| Engine | Capability |
|--------|-----------|
| NVENC | H.264/H.265 encode up to 4K30 |
| NVDEC | H.264/H.265/VP9/AV1 decode up to 4K60 |
| JPEG | Hardware JPEG encode/decode |

These are accessed through GStreamer plugins (`nvv4l2h264enc`, etc.) and are
essentially free in terms of CPU/GPU utilization.

---

## 8. GStreamer Hardware Pipeline

### The Full Pipeline (as configured in camera.py)

```
nvarguscamerasrc sensor-id=0
    ! video/x-raw(memory:NVMM), width=1280, height=720, framerate=60/1, format=NV12
    ! nvvidconv flip-method=0
    ! video/x-raw, width=640, height=480, format=BGRx
    ! videoconvert
    ! video/x-raw, format=BGR
    ! appsink drop=1 max-buffers=1
```

### What Each Stage Costs

| Stage | Runs On | CPU Cost | GPU Cost | Latency |
|-------|---------|----------|----------|---------|
| `nvarguscamerasrc` | ISP engine | ~0% | ~0% | ~16ms (1 frame @ 60fps) |
| `nvvidconv` (resize + colorspace) | VIC engine | ~0% | ~0% | <1ms |
| `videoconvert` (BGRx → BGR) | **CPU** | ~2-3% | 0% | <1ms |
| `appsink` | CPU | ~0% | 0% | ~0% |

**The CPU bottleneck is `videoconvert`** — it strips the alpha channel on the CPU.
For maximum performance, consider staying in NV12/NVMM and using CUDA for
colorspace conversion, but this adds significant code complexity.

### Alternative Pipelines (for future features)

**Save H.264 video to file (surveillance/recording):**
```
nvarguscamerasrc sensor-id=0
    ! video/x-raw(memory:NVMM), width=1920, height=1080, framerate=30/1
    ! nvv4l2h264enc bitrate=8000000
    ! h264parse ! mp4mux
    ! filesink location=output.mp4
```

**RTSP streaming (remote monitoring):**
```
nvarguscamerasrc sensor-id=0
    ! video/x-raw(memory:NVMM), width=1280, height=720, framerate=30/1
    ! nvv4l2h264enc bitrate=4000000
    ! h264parse ! rtph264pay
    ! udpsink host=<IP> port=5000
```

**Direct GPU texture (if you ever need to skip CPU entirely):**
```
nvarguscamerasrc sensor-id=0
    ! video/x-raw(memory:NVMM), width=1920, height=1080, framerate=30/1
    ! nvegltransform ! nveglglessink
```

---

## 9. Vision Pipeline Constraints

### Current Pipeline Performance Budget (at 640x480 input)

| Stage | Backend | Time per Frame | Notes |
|-------|---------|---------------|-------|
| Camera read | GStreamer/NVMM | <1ms | Non-blocking, threaded |
| Face detection | MediaPipe | ~8-12ms | CPU-only on ARM |
| Face detection | YuNet (fallback) | ~5-8ms | OpenCV DNN, CPU |
| Gaze estimation | solvePnP (fallback) | ~1-2ms | CPU, no model |
| Gaze estimation | TensorRT | ~3-5ms | GPU, if model available |
| Distance estimation | Landmark math | <1ms | CPU |
| Screen mapping | Math | <1ms | CPU |
| **Total** | | **~15-25ms** | **Fits comfortably in 33ms (30 Hz target)** |

### Resolution Trade-offs

| Resolution | Face Detection Range | CPU Load | Notes |
|-----------|---------------------|----------|-------|
| 320x240 | ~1.5m max | Very low | May miss faces beyond 1.5m |
| **640x480** | **~3m max** | **Low** | **Current setting — good balance** |
| 1280x720 | ~5m+ | Medium | Overkill for this use case |
| 1920x1080 | ~8m+ | High | Wastes resources, no benefit |

640x480 is ideal because the interaction distance is 0-3 meters (PRESENCE triggers
at 300cm). Higher resolutions detect faces further away but waste CPU on the face
detection step with no interaction benefit.

### Face Detection Backend Notes

**MediaPipe** (preferred, not yet installed):
- 6 keypoints: right_eye, left_eye, nose_tip, mouth_center, right_ear, left_ear
- Runs on CPU (no GPU acceleration on Jetson aarch64)
- aarch64 wheel availability has historically been spotty — test before depending on it
- `model_selection=0` is the short-range model (< 2m), `1` is full-range

**YuNet** (fallback):
- 5 keypoints: right_eye, left_eye, nose_tip, right_mouth, left_mouth
- No ear keypoints (reduces solvePnP accuracy)
- Synthesizes `mouth_center` from the two mouth corners
- Requires `models/face_detection_yunet.onnx`

**Gaze estimation** currently falls back to head-pose approximation via `cv2.solvePnP`
for all three backends (TensorRT and ONNX are placeholder implementations). This
gives yaw/pitch from head orientation, not true eye gaze. Accuracy is limited but
sufficient for region-level gaze detection at Soul Frame's interaction distances.

---

## 10. Thermal and Power Management

### Power Modes

| Mode | Name | CPU Cores | CPU Freq | GPU Freq | Power |
|------|------|-----------|----------|----------|-------|
| 0 | MAXN | 8 | 2.0 GHz | 918 MHz | 25W |
| 4 | 40W | 8 | 1.5 GHz | 765 MHz | 40W cap* |

*Mode 4 (current) allows higher burst power but caps sustained draw.

**For production**: Run `sudo nvpmodel -m 0 && sudo jetson_clocks` to lock all
clocks at maximum. The setup script does this automatically.

### Thermal Considerations

- The Seeed reComputer J4012 has an integrated fan and heatsink
- Sustained full-load operation (GPU inference + display + camera) is safe
- Monitor with `tegrastats` or `scripts/monitor.sh`
- If temperature exceeds ~95°C, the kernel will throttle — this should not happen
  with the stock cooling solution at Soul Frame's workload
- **The 3D-printed art frame enclosure restricts airflow** — ensure ventilation
  channels exist in the frame design

### Jetson Clocks

`sudo jetson_clocks` locks CPU, GPU, and memory clocks at maximum, preventing
dynamic frequency scaling. This trades power efficiency for consistent latency
(no frame drops from clock ramp-up delays).

---

## 11. Storage and Filesystem

| Path | Size | Purpose |
|------|------|---------|
| `/` (NVMe) | 128 GB total, ~91 GB free | Everything |
| `/boot/` | Boot images, DTBs, extlinux.conf | |
| `/home/arash/soulframe/` | Soul Frame project | |
| `/home/arash/soulframe/content/gallery/` | Portrait images + audio | Can grow large |
| `/home/arash/soulframe/models/` | ML model weights | |
| `/tmp/` | Volatile, cleared on reboot | |

**Storage is generous** — 91 GB free. High-res portraits with audio are typically
5-30 MB each. You would need thousands to fill the disk. Video recording is the
main risk for filling storage (1080p H.264 at 8 Mbps ≈ 1 GB per 17 minutes).

---

## 12. Networking

- **Wi-Fi**: Available (Jetson Orin NX has built-in Wi-Fi)
- **Ethernet**: Available via J401 carrier board
- **Internet access**: Assumed available
- **SSH**: Accessible for remote development
- **Authoring tool**: FastAPI server on port 8080 (localhost by default, can bind to 0.0.0.0)

---

## 13. Known Pitfalls and Hard Constraints

### Camera Pitfalls

| Pitfall | Consequence | Prevention |
|---------|-------------|------------|
| Using `v4l2src` or `cv2.VideoCapture(0)` | Raw Bayer data, green garbage frames | Always use `nvarguscamerasrc` with `cv2.CAP_GSTREAMER` |
| Requesting non-native resolution | Pipeline fails to negotiate | Only use resolutions from the sensor mode table |
| Two processes opening the camera | Second process silently fails | Ensure exclusive access; check `fuser /dev/video0` |
| Camera opened before receiver board powered | IMX219 not detected | Power receiver board before boot |
| Running without GMSL2 DTB patch | Corrupted frames (garbled top 15%) | Verify `pix_clk_hz = 300000000` in live device tree |
| Not restarting nvargus-daemon after crash | Camera refuses to open | `sudo systemctl restart nvargus-daemon` |

### GPU/Memory Pitfalls

| Pitfall | Consequence | Prevention |
|---------|-------------|------------|
| Loading large model without checking memory | OOM kills a process | Check `free -h` before loading; budget for 16 GB shared |
| Assuming discrete VRAM | Incorrect memory math | Jetson has unified memory — GPU uses main RAM |
| Running CUDA training workloads | System freezes, OOM | This device is for inference only |
| Not using TensorRT for production inference | 5-10x slower than possible | Convert ONNX models to TensorRT `.engine` files |

### Display Pitfalls

| Pitfall | Consequence | Prevention |
|---------|-------------|------------|
| Trying USB-C for display | Nothing happens | Jetson doesn't support USB-C DP Alt Mode; use HDMI |
| Static content for hours | OLED burn-in | Use Ken Burns / subtle animation |
| Creating second OpenGL context | Crashes or corruption | Display process owns the sole GL context |

### System Pitfalls

| Pitfall | Consequence | Prevention |
|---------|-------------|------------|
| Running Arducam's `install_full.sh` | Breaks USB, system instability | **NEVER run it.** See GMSL2_CAMERA_FIX_GUIDE.md |
| Installing amd64 packages | Architecture mismatch failure | Always check for `arm64`/`aarch64` variants |
| `pip install` without checking aarch64 wheels | Build failures, missing C extensions | Many ML libraries need special aarch64 builds |
| Upgrading L4T/JetPack without re-patching DTB | Camera corruption returns | Re-run camera fix after any OS update |
| Filling /tmp with video recordings | Boot/system issues | Record to `/home/arash/` or external storage |

---

## 14. What You CAN Do (Capabilities)

### Vision / ML

- Run multiple inference models simultaneously (GPU + 2x DLA)
- TensorRT INT8/FP16 inference at high throughput (~100+ TOPS INT8)
- Real-time face detection + gaze estimation at 30 Hz with CPU headroom to spare
- Hardware JPEG/H.264/H.265 encode and decode (free, uses dedicated engines)
- CUDA-accelerated image processing (OpenCV CUDA module, cuDNN)
- Run small language models / embedding models (with careful memory management)

### Camera

- Capture at up to 3280x2464 @ 21fps (full 8MP sensor)
- 60 fps at 720p for low-latency tracking
- Hardware ISP with auto exposure, white balance, noise reduction
- Hardware resize via `nvvidconv` (essentially free)
- Record video to H.264/H.265 via hardware encoder
- Stream video over network (RTSP, UDP)

### Display

- Fullscreen 1080p @ 60fps OpenGL rendering
- GLSL fragment shaders with per-frame uniform updates
- Texture crossfade between images
- GPU-accelerated composition

### Audio

- Low-latency audio output via ReSpeaker DAC
- Software mixing of multiple audio streams (ambient, heartbeat, effects)
- Real-time parametric EQ (bass boost for sub exciter)
- Distance-based volume curves with configurable easing

### System

- 8 CPU cores for parallel Python processes
- 91 GB free storage
- Network access (Wi-Fi + Ethernet)
- Systemd services for auto-start
- Web-based remote authoring tool

---

## 15. What You CANNOT Do (Hard Limits)

| Limitation | Why | Workaround |
|-----------|-----|------------|
| Run x86/amd64 binaries | ARM architecture | Use aarch64 packages only |
| Train large neural networks | 16 GB shared memory, no dedicated VRAM | Train elsewhere, deploy .engine files |
| Connect a second display | Single HDMI output on J401 carrier | Use web UI for secondary interfaces |
| Use USB-C for display | Jetson doesn't support DP Alt Mode | HDMI only |
| Open camera from multiple processes | nvargus exclusive lock | Single vision process reads, shares via SHM |
| Use ALSA directly for spatial audio routing | ReSpeaker outputs stereo only | Bass routing handled by hardware crossover in amp |
| Get true eye gaze without ML model | solvePnP only gives head pose | Integrate dedicated gaze model (ONNX/TensorRT) |
| Install Dropbox native client | No aarch64 build | Use rclone or maestral |
| Run more than ~2-3 large models concurrently | 16 GB shared memory ceiling | Prioritize; use DLA offload; quantize to INT8 |
| Capture more than 60 fps | Sensor/ISP hardware limit | 60fps at 720p is the maximum |
