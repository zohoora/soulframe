"""Global paths, constants, and defaults for Soul Frame."""

import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(os.environ.get("SOULFRAME_ROOT", Path(__file__).resolve().parent.parent))
CONTENT_DIR = PROJECT_ROOT / "content"
GALLERY_DIR = CONTENT_DIR / "gallery"
MODELS_DIR = PROJECT_ROOT / "models"
CALIBRATION_DIR = PROJECT_ROOT / "calibration"

# ── Display ────────────────────────────────────────────────────────────────
DISPLAY_WIDTH = 1920
DISPLAY_HEIGHT = 1080
DISPLAY_FPS = 60
DISPLAY_SCREEN_INDEX = 0

# ── Camera ─────────────────────────────────────────────────────────────────
CAMERA_INDEX = 0          # /dev/video0
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_FPS = 30

# ── Vision ─────────────────────────────────────────────────────────────────
FACE_DETECTION_CONFIDENCE = 0.5
GAZE_MODEL_PATH = MODELS_DIR / "gaze_model.onnx"
GAZE_ENGINE_PATH = MODELS_DIR / "gaze_model.engine"
VISION_SHM_NAME = "soulframe_vision"
VISION_SHM_SIZE = 44      # bytes (4 seqlock + 40 data), see shared/ipc.py
VISION_STALE_TIMEOUT_S = 2.0  # expire stale vision data after this many seconds

# ── Audio ──────────────────────────────────────────────────────────────────
AUDIO_SAMPLE_RATE = 44100
AUDIO_CHANNELS = 2        # stereo
AUDIO_BLOCK_SIZE = 1024
AUDIO_DEVICE_NAME = "seeed"  # substring match for ReSpeaker

# Bass boost for heartbeat (3-band parametric EQ targeting sub crossover)
HEARTBEAT_BASS_CENTER_HZ = 60
HEARTBEAT_BASS_Q = 0.7
HEARTBEAT_BASS_GAIN_DB = 12.0

# ── State Machine ──────────────────────────────────────────────────────────
IDLE_IMAGE_CYCLE_SECONDS = 300        # 5 minutes
PRESENCE_DISTANCE_CM = 300
CLOSE_INTERACTION_DISTANCE_CM = 80
PRESENCE_LOST_TIMEOUT_S = 3.0
IDLE_FACE_LOST_TIMEOUT_S = 5.0
GAZE_DWELL_MS = 1500
GAZE_MIN_CONFIDENCE = 0.6
WITHDRAW_GAZE_AWAY_TIMEOUT_S = 8.0
WITHDRAW_FADE_DURATION_S = 4.0

# ── Transitions ────────────────────────────────────────────────────────────
DEFAULT_FADE_IN_MS = 2000
DEFAULT_FADE_OUT_MS = 2000
DEFAULT_AUDIO_CROSSFADE_MS = 3000

# ── Authoring Server ──────────────────────────────────────────────────────
AUTHORING_HOST = os.environ.get("SOULFRAME_AUTHORING_HOST", "127.0.0.1")
AUTHORING_PORT = int(os.environ.get("SOULFRAME_AUTHORING_PORT", "8080"))
AUTHORING_API_KEY = os.environ.get("SOULFRAME_API_KEY", "")
