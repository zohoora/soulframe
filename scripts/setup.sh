#!/usr/bin/env bash
# Soul Frame — full setup script for Jetson Orin NX
# Run this after a fresh clone: ./scripts/setup.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Soul Frame Setup ==="
echo "Project directory: $PROJECT_DIR"

# ── System packages (requires sudo) ────────────────────────────────────
echo ""
echo "--- Installing system dependencies ---"
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    portaudio19-dev \
    libsndfile1-dev \
    libgl1-mesa-dev \
    libasound2-dev

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
    echo "Setting max performance mode..."
    sudo nvpmodel -m 0 || echo "Warning: Could not set nvpmodel"
    sudo jetson_clocks || echo "Warning: Could not set jetson_clocks"
else
    echo "nvpmodel not found — not running on Jetson?"
fi

# ── Audio device check ──────────────────────────────────────────────────
echo ""
echo "--- Checking audio devices ---"
"$PROJECT_DIR/.venv/bin/python" -c "
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
ls /dev/video* 2>/dev/null && echo "Video devices found" || echo "No /dev/video* devices found"

# Jetson GMSL2 / CSI camera checks
if command -v nvpmodel &>/dev/null; then
    echo ""
    echo "--- Jetson CSI camera checks ---"

    # Verify nvargus-daemon is running (required for nvarguscamerasrc)
    if systemctl is-active --quiet nvargus-daemon; then
        echo "nvargus-daemon: running"
    else
        echo "nvargus-daemon: NOT running — starting it..."
        sudo systemctl restart nvargus-daemon && echo "  started" || echo "  FAILED to start"
    fi

    # Check for IMX219 sensor
    if dmesg | grep -q "imx219 9-0010"; then
        echo "IMX219 sensor: detected on I2C bus 9"
    else
        echo "IMX219 sensor: NOT detected — check GMSL2 cable and receiver board power"
    fi

    # Verify pix_clk_hz patch (GMSL2 requires 300 MHz)
    if command -v dtc &>/dev/null; then
        PIX_CLK=$(sudo dtc -I fs /proc/device-tree -O dts 2>/dev/null | grep pix_clk_hz | head -1)
        if echo "$PIX_CLK" | grep -q "300000000"; then
            echo "pix_clk_hz: 300 MHz (GMSL2 patch applied)"
        else
            echo "WARNING: pix_clk_hz is NOT 300 MHz — GMSL2 video will be corrupted!"
            echo "  See ~/GMSL2_CAMERA_FIX_GUIDE.md for fix instructions."
        fi
    fi

    # Quick capture test
    echo "Running quick capture test..."
    if gst-launch-1.0 nvarguscamerasrc sensor-id=0 num-buffers=1 ! \
        'video/x-raw(memory:NVMM), width=1280, height=720, framerate=60/1' ! \
        nvjpegenc ! filesink location=/tmp/soulframe_camera_test.jpg 2>/dev/null; then
        echo "Camera capture test: PASSED (/tmp/soulframe_camera_test.jpg)"
    else
        echo "Camera capture test: FAILED"
        echo "  Check: sudo systemctl restart nvargus-daemon"
    fi
fi

# ── Desktop autostart ──────────────────────────────────────────────────
echo ""
echo "--- Installing desktop autostart entry ---"
mkdir -p "$HOME/.config/autostart"
cp "$PROJECT_DIR/systemd/soulframe.desktop" "$HOME/.config/autostart/" 2>/dev/null || true
echo "Desktop autostart entry installed"

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

# ── Git config (for development) ───────────────────────────────────────
echo ""
echo "--- Git config ---"
if ! git -C "$PROJECT_DIR" config user.email &>/dev/null; then
    echo "Git user not configured. To set:"
    echo "  git config user.name \"Arash Zohoor\""
    echo "  git config user.email \"zohoora@gmail.com\""
fi

echo ""
echo "================================================"
echo "  Setup complete!"
echo "================================================"
echo ""
echo "Run the installation:"
echo "  cd $PROJECT_DIR && .venv/bin/python -m soulframe"
echo ""
echo "Run the authoring tool:"
echo "  cd $PROJECT_DIR && .venv/bin/python -m soulframe --authoring"
echo ""
echo "Monitor system (Jetson thermals/utilization):"
echo "  $PROJECT_DIR/scripts/monitor.sh"
