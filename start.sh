#!/usr/bin/env bash
# =============================================================
#  SinketVPS - Web Terminal launcher (Colab / Kaggle)
#  Repo: https://github.com/sureshkumar77536/Sinketvps
# =============================================================
set -e

# ---- config (override via env) ----
export PORT="${PORT:-7860}"
export TERMINAL_PASSWORD="${TERMINAL_PASSWORD:-admin}"

cd "$(dirname "$0")"

echo "==============================================="
echo "   SinketVPS Web Terminal - setup starting"
echo "==============================================="

# ---- 1. system deps (tmux for persistent sessions) ----
echo "[*] Installing tmux + tools (quick)..."
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq >/dev/null 2>&1 || apt-get update -qq >/dev/null 2>&1 || true
  (sudo apt-get install -y -qq tmux curl >/dev/null 2>&1) \
    || (apt-get install -y -qq tmux curl >/dev/null 2>&1) || true
fi

# ---- 2. python deps ----
echo "[*] Installing python packages..."
pip install -q -r requirements.txt 2>/dev/null || pip install -q flask flask-socketio simple-websocket

# ---- 3. cloudflared (no signup needed: try quick tunnel) ----
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "[*] Downloading cloudflared..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64|amd64) CF_ARCH="amd64" ;;
    aarch64|arm64) CF_ARCH="arm64" ;;
    *) CF_ARCH="amd64" ;;
  esac
  curl -L --silent --show-error --fail \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
    -o /tmp/cloudflared && chmod +x /tmp/cloudflared
  CF_BIN="/tmp/cloudflared"
else
  CF_BIN="$(command -v cloudflared)"
fi

# ---- 4. nice debian-like banner inside shells ----
BASHRC="$HOME/.bashrc"
if ! grep -q "SINKET_BANNER" "$BASHRC" 2>/dev/null; then
cat >> "$BASHRC" <<'EOF'

# SINKET_BANNER
export PS1='\[\e[01;32m\]\u@debian\[\e[00m\]:\[\e[01;34m\]\w\[\e[00m\]\$ '
export TERM=xterm-256color
export LANG=C.UTF-8
if [ -z "$SINKET_WELCOMED" ]; then
  export SINKET_WELCOMED=1
  echo -e "\e[1;36m"
  echo "   ____  _       _        _   __     ______  ____  "
  echo "  / ___|(_)_ __ | | _____| |_ \ \   / /  _ \/ ___| "
  echo "  \___ \| | '_ \| |/ / _ \ __| \ \ / /| |_) \___ \ "
  echo "   ___) | | | | |   <  __/ |_   \ V / |  __/ ___) |"
  echo "  |____/|_|_| |_|_|\_\___|\__|   \_/  |_|   |____/ "
  echo -e "\e[0m"
  echo -e "  \e[1;32mDebian-style Web Terminal\e[0m  ·  type 'neofetch' or 'help'"
  echo ""
fi
EOF
fi

# ---- 5. start backend ----
echo "[*] Starting terminal server on :$PORT ..."
python3 server.py &
SERVER_PID=$!
sleep 4

# ---- 6. start cloudflare tunnel & print URL ----
echo ""
echo "==============================================="
echo "   Opening Cloudflare tunnel... please wait"
echo "==============================================="
"$CF_BIN" tunnel --url "http://localhost:$PORT" --no-autoupdate 2>&1 \
  | while read -r line; do
      echo "$line"
      if echo "$line" | grep -qE "https://[a-zA-Z0-9.-]+\.trycloudflare\.com"; then
        URL=$(echo "$line" | grep -oE "https://[a-zA-Z0-9.-]+\.trycloudflare\.com" | head -n1)
        echo ""
        echo "###############################################"
        echo "#  YOUR TERMINAL IS LIVE:"
        echo "#  $URL"
        echo "#  Password: $TERMINAL_PASSWORD"
        echo "###############################################"
        echo ""
      fi
    done

wait $SERVER_PID
