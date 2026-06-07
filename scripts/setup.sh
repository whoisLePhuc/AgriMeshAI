#!/usr/bin/env bash
set -euo pipefail

echo "============================================"
echo "  AgriMeshAI — Setup"
echo "============================================"
echo ""

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# ---- Step 1: Python ----
echo "[1/3] Checking Python..."
PYTHON=""
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    PYTHON="$cmd"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "  ✗ Python not found. Install with: sudo apt install python3 python3-pip python3-venv"
  exit 1
fi

# Verify minimum Python version (3.8+)
PY_VERSION=$("$PYTHON" -c 'import sys; print(sys.version_info.major * 10 + sys.version_info.minor)')
if [ "$PY_VERSION" -lt 38 ]; then
  echo "  ✗ Python 3.8+ is required. Found: $("$PYTHON" --version)"
  exit 1
fi
echo "  ✓ $("$PYTHON" --version)"

# ---- Step 2: Virtual environment ----
echo "[2/3] Setting up virtual environment..."
if [ ! -d "venv" ]; then
  "$PYTHON" -m venv venv
  echo "  ✓ Created venv/"
else
  echo "  → venv/ already exists, skipping"
fi

# Activate once — reused for all subsequent steps
source venv/bin/activate

# ---- Step 3: Python dependencies ----
echo "[3/3] Installing Python dependencies..."
pip install --upgrade pip -q
if pip install -r requirements.txt; then
  echo "  ✓ Done"
else
  echo "  ✗ Failed to install dependencies. Check requirements.txt"
  exit 1
fi

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Next: install Ollama and pull the LLM model"
echo "  See README.md for instructions."
echo ""