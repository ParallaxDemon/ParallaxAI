#!/data/user/0/com.termux/files/usr/bin/bash
# Keeps the phone awake and starts the always-on ParallaxAI chat worker.
set -euo pipefail
BASE="$HOME/projects/ParallaxAI/local"
export LD_LIBRARY_PATH="$BASE/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PARALLAX_LOCAL_CONFIG="$HOME/.config/parallax-ai/local-worker.json"
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock || true; fi
exec python3 "$BASE/local_worker.py"
