#!/system/bin/sh
# ParallaxAI always-on chat worker — launched on boot by KernelSU.
# Installed at /data/adb/service.d/parallax-worker.sh (mode 0755).
(
  # Wait until Android is fully booted and Termux data is readable.
  for i in $(seq 1 90); do
    [ "$(getprop sys.boot_completed)" = "1" ] && break
    sleep 5
  done
  sleep 20

  HOME_DIR=/data/user/0/com.termux/files/home
  [ -f "$HOME_DIR/.config/parallax-ai/local-worker.json" ] || exit 0

  # Don't start a second copy if one is already alive.
  if pgrep -f 'local_worker[.]py' >/dev/null 2>&1; then exit 0; fi

  su 10282 -c "
    export HOME=$HOME_DIR
    export PATH=/data/user/0/com.termux/files/usr/bin:\$PATH
    export LD_LIBRARY_PATH=$HOME_DIR/projects/ParallaxAI/local/bin
    export PARALLAX_LOCAL_CONFIG=$HOME_DIR/.config/parallax-ai/local-worker.json
    setsid $HOME_DIR/projects/ParallaxAI/local/start-worker.sh >> $HOME_DIR/.local/share/parallax-local-worker.log 2>&1 < /dev/null &
  "
) &
