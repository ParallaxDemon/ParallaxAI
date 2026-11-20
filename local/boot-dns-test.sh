#!/data/user/0/com.termux/files/usr/bin/bash
export HOME=/data/user/0/com.termux/files/home
export PATH=/data/user/0/com.termux/files/usr/bin:$PATH
export LD_LIBRARY_PATH=$HOME/projects/ParallaxAI/local/bin
export PARALLAX_LOCAL_CONFIG=$HOME/.config/parallax-ai/local-worker.json
echo "ctx=$(cat /proc/self/attr/current)"
curl -sS -o /dev/null -w "HTTP %{http_code}\n" --max-time 12 https://ai.parallaxserver.online/api/v1/health
python3 -c 'import socket; print("dns", socket.gethostbyname("ai.parallaxserver.online"))'
