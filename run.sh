#!/bin/bash
echo "=========================================================="
echo "          DEDUPEFLOW SAAS AUTO-INSTALLER & RUNNER"
echo "=========================================================="
echo ""

# Check for Python
if ! command -v python3 &> /dev/null
then
    echo "[ERROR] Python 3 is not installed."
    echo "Please install Python 3.10+ using your package manager."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating Python virtual environment (.venv)..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        exit 1
    fi
fi

# Activate virtual environment and install dependencies
echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

echo "[INFO] Installing required packages..."
python3 -m pip install --upgrade pip
pip install -r backend/requirements.txt

# Check if app.py exists
if [ ! -f "app.py" ]; then
    echo "[ERROR] app.py not found in the current directory!"
    exit 1
fi

echo ""
echo "=========================================================="
echo "          STARTING SAAS SERVER ON PORT 8000"
echo "=========================================================="
echo ""
echo "Launching server..."
echo "Once started, open your browser and visit:"
echo "-> http://127.0.0.1:8000/"
echo ""
echo "Press Ctrl+C in this terminal window to stop the server."
echo "=========================================================="
echo ""

python3 app.py
