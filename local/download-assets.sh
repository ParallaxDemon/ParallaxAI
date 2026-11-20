#!/data/user/0/com.termux/files/usr/bin/bash
set -euo pipefail
BASE="$HOME/projects/ParallaxAI/local"
echo "[$(date)] downloading llama arm64 binary"
curl -fL --retry 3 --max-time 600 \
  -o "$BASE/llama.tar.gz" \
  "https://github.com/ggml-org/llama.cpp/releases/download/b12556/llama-b12556-bin-android-arm64.tar.gz"
mkdir -p "$BASE/bin"
tar -xzf "$BASE/llama.tar.gz" -C "$BASE/bin" --strip-components=1
chmod +x "$BASE/bin/llama-server"
export LD_LIBRARY_PATH="$BASE/bin${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$BASE/bin/llama-server" --version | head -1
echo "[$(date)] downloading chat model (Qwen2.5-3B-Instruct — good Hinglish/multilingual, fast)"
curl -fL --retry 5 --retry-delay 3 --continue-at - \
  -o "$BASE/model/Qwen2.5-3B-Instruct-Q4_K_M.gguf" \
  "https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
ls -lh "$BASE/model/Qwen2.5-3B-Instruct-Q4_K_M.gguf"
echo "[$(date)] assets ready"
