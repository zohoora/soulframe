#!/usr/bin/env bash
# Soul Frame — monitor system resources (Jetson)
# Run this in a separate terminal to watch thermals and utilization.

echo "=== Soul Frame System Monitor ==="
echo "Press Ctrl+C to stop"
echo ""

if command -v tegrastats &>/dev/null; then
    echo "Using tegrastats (Jetson)..."
    sudo tegrastats --interval 2000
else
    echo "tegrastats not found. Falling back to basic monitoring..."
    while true; do
        echo "---"
        date
        echo "CPU:"
        top -bn1 | head -5
        echo ""
        if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
            for tz in /sys/class/thermal/thermal_zone*/temp; do
                zone=$(basename "$(dirname "$tz")")
                temp=$(cat "$tz")
                echo "  $zone: $((temp / 1000))°C"
            done
        fi
        echo ""
        free -h | head -2
        sleep 5
    done
fi
