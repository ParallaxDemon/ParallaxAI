#!/data/user/0/com.termux/files/usr/bin/bash
# Keeps the phone awake and runs ONE ParallaxAI chat worker in 6-hour sessions.
# A session ends cleanly only when the worker is idle; this launcher immediately
# starts the next one. Result: one continuous chat processor that refreshes every 6h.
set -euo pipefail
BASE="$HOME/projects/ParallaxAI/local"
export LD_LIBRARY_PATH="$BASE/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PARALLAX_LOCAL_CONFIG="$HOME/.config/parallax-ai/local-worker.json"
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock || true; fi
while true; do
  python3 "$BASE/local_worker.py" || true
  sleep 3
done
