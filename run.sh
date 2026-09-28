#!/usr/bin/env bash
# =====================================================================
#  ABA Fusion AI Agent - start the Web UI (Linux/macOS)
#  Auto-runs setup if needed, then launches http://127.0.0.1:8787
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "No virtual environment found - running setup first..."
    ./setup.sh
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# Keep deps in sync with requirements.txt on EVERY start (fast no-op when
# nothing changed). Guarantees env matches latest code after a git pull.
echo "Syncing dependencies with requirements.txt ..."
pip install -q -r requirements.txt

echo "Starting ABA Fusion AI Agent Web UI ..."
echo "Open your browser at:  http://127.0.0.1:8787   (Ctrl+C to stop)"
exec python webui/app.py
