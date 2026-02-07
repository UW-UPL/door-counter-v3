#!/bin/bash
# UPL Door Jingle System Launcher

set -e

echo "Starting UPL Door Jingle System..."
echo ""

# Change to script directory FIRST
cd "$(dirname "$0")"

# Check if running on Pi
if ! command -v python3 &> /dev/null; then
    echo "Error: Python3 not found"
    exit 1
fi

# Use venv if it exists, otherwise use system python3
if [ -d "venv" ]; then
    echo "Using virtual environment"
    PYTHON="$(pwd)/venv/bin/python"
else
    echo "Using system Python3"
    PYTHON="python3"
fi

# Refresh sudo credentials
sudo -v

# Start Bluetooth pairing service (requires sudo)
echo "Starting Bluetooth pairing service..."
sudo -E "$PYTHON" src/bt_service.py &
BT_PID=$!

# Give BT service time to initialize
sleep 2

# Start main system (BLE + Audio)
echo "Starting main detection system..."
"$PYTHON" src/main.py &
MAIN_PID=$!

# Start GitHub sync service
echo "Starting GitHub sync service..."
"$PYTHON" src/github_sync.py &
SYNC_PID=$!

echo ""
echo "All services running"
echo "  - Bluetooth pairing service (PID: $BT_PID)"
echo "  - Main system (PID: $MAIN_PID)"
echo "  - GitHub sync service (PID: $SYNC_PID)"
echo ""
echo "Press Ctrl+C to stop all services"
echo ""

# Cleanup on exit
cleanup() {
    echo ""
    echo "Stopping all services..."
    sudo kill $BT_PID 2>/dev/null || true
    kill $MAIN_PID 2>/dev/null || true
    kill $SYNC_PID 2>/dev/null || true
    echo "All services stopped"
    exit 0
}

trap cleanup SIGINT SIGTERM

wait
