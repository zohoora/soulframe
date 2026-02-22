#!/usr/bin/env bash
# Soul Frame — initial setup script for Jetson Orin NX
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Soul Frame Setup ==="
echo "Project directory: $PROJECT_DIR"

# ── Python virtual environment ──────────────────────────────────────────
echo ""
echo "--- Setting up Python virtual environment ---"
if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
    echo "Created .venv"
else
    echo ".venv already exists"
fi

source "$PROJECT_DIR/.venv/bin/activate"
pip install --upgrade pip wheel setuptools

# ── Install dependencies ────────────────────────────────────────────────
echo ""
echo "--- Installing Python dependencies ---"
pip install -r "$PROJECT_DIR/requirements.txt"

# Install the project itself in development mode
pip install -e "$PROJECT_DIR"

# ── Create content directories ──────────────────────────────────────────
echo ""
echo "--- Creating content directories ---"
mkdir -p "$PROJECT_DIR/content/gallery"
mkdir -p "$PROJECT_DIR/models"
mkdir -p "$PROJECT_DIR/calibration"

# ── Disable screen blanking ────────────────────────────────────────────
echo ""
echo "--- Configuring display settings ---"
if command -v xset &>/dev/null; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
    echo "Screen blanking disabled (xset)"
else
    echo "xset not available — configure screen blanking manually"
fi

# ── Jetson performance mode ─────────────────────────────────────────────
echo ""
echo "--- Jetson performance settings ---"
if command -v nvpmodel &>/dev/null; then
    echo "Setting max performance mode (requires sudo)..."
    sudo nvpmodel -m 0 || echo "Warning: Could not set nvpmodel"
    sudo jetson_clocks || echo "Warning: Could not set jetson_clocks"
else
    echo "nvpmodel not found — not running on Jetson?"
fi

# ── Audio device check ──────────────────────────────────────────────────
echo ""
echo "--- Checking audio devices ---"
python3 -c "
import sounddevice as sd
devices = sd.query_devices()
print('Available audio devices:')
for i, d in enumerate(devices):
    marker = ' <-- ReSpeaker?' if 'seeed' in d['name'].lower() else ''
    print(f'  [{i}] {d[\"name\"]} (out={d[\"max_output_channels\"]}ch){marker}')
" 2>/dev/null || echo "sounddevice not yet installed or no devices found"

# ── Camera check ────────────────────────────────────────────────────────
echo ""
echo "--- Checking camera devices ---"
if [ -d /dev ]; then
    ls /dev/video* 2>/dev/null && echo "Video devices found" || echo "No /dev/video* devices found"
fi

# ── Install systemd services (optional) ────────────────────────────────
echo ""
echo "--- Systemd service files ---"
echo "To enable auto-start on boot, run:"
echo "  sudo cp $PROJECT_DIR/systemd/soulframe.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable soulframe"
echo ""
echo "To enable the authoring tool service:"
echo "  sudo cp $PROJECT_DIR/systemd/soulframe-authoring.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable soulframe-authoring"

echo ""
echo "=== Setup complete ==="
echo "Run with: cd $PROJECT_DIR && .venv/bin/python -m soulframe"
echo "Authoring: cd $PROJECT_DIR && .venv/bin/python -m soulframe --authoring"
