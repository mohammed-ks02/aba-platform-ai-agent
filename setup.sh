#!/usr/bin/env bash
# =====================================================================
#  ABA Fusion AI Agent - one-click setup (Linux/macOS)
#  Creates .venv, installs requirements.txt, installs Playwright Chromium.
#  Re-run any time: it always syncs with the CURRENT requirements.txt,
#  so after every project update just run ./setup.sh again.
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "[1/4] Checking Python..."
PY=${PYTHON:-python3}
command -v "$PY" >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
"$PY" --version

if [ ! -d ".venv" ]; then
    echo "[2/4] Creating virtual environment .venv ..."
    "$PY" -m venv .venv
else
    echo "[2/4] Virtual environment already exists - reusing it."
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "[3/4] Installing/updating dependencies from requirements.txt ..."
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "[4/5] Ensuring Playwright Chromium browser is installed ..."
python -m playwright install chromium || \
    echo "WARNING: chromium download failed - live/visual testing will fall back to urllib"

echo "[5/5] Preparing .env config file..."
if [ ! -f .env ] && [ -f env.example ]; then
    cp env.example .env
    echo " Created .env from env.example -- EDIT IT with your real keys and password!"
else
    echo " .env already exists (or template missing) - leaving it untouched."
fi

echo
echo "====================================================================="
echo " Setup complete.  Start the web interface with:  ./run.sh"
echo "====================================================================="
