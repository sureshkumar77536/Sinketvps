#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SinketVPS - Notebook launcher (Colab / Kaggle friendly)
-------------------------------------------------------
Ek hi cell se sab kuch:
  - deps install (tmux, cloudflared, python pkgs)
  - server start + HEALTH CHECK (origin zinda hai ya nahi)
  - server crash -> auto restart (Error 530 fix)
  - cloudflare quick-tunnel + clean URL print

Usage (Colab/Kaggle cell):
    !git clone https://github.com/sureshkumar77536/Sinketvps
    %cd Sinketvps
    import run; run.main(password="myStrongPass")
"""
import os
import re
import sys
import time
import stat
import shutil
import platform
import threading
import subprocess
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SRV_LOG = os.path.join(HERE, "server.log")


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, **kw)


def _install_tmux():
    if shutil.which("tmux"):
        return
    print("[*] Installing tmux ...")
    for c in ("sudo apt-get update -qq", "apt-get update -qq"):
        if sh(c, capture_output=True).returncode == 0:
            break
    for c in ("sudo apt-get install -y -qq tmux", "apt-get install -y -qq tmux"):
        if sh(c, capture_output=True).returncode == 0:
            break


def _install_pkgs():
    print("[*] Installing python packages ...")
    sh(sys.executable + " -m pip install -q flask flask-socketio simple-websocket",
       capture_output=True)


def _get_cloudflared():
    found = shutil.which("cloudflared")
    if found:
        return found
    local = "/tmp/cloudflared"
    if os.path.exists(local):
        return local
    arch = platform.machine().lower()
    cf = "arm64" if arch in ("aarch64", "arm64") else "amd64"
    url = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
           "cloudflared-linux-%s" % cf)
    print("[*] Downloading cloudflared (%s) ..." % cf)
    urllib.request.urlretrieve(url, local)
    os.chmod(local, os.stat(local).st_mode | stat.S_IEXEC)
    return local


def _kill_old(port):
    """Pehle se chal rahe server/cloudflared ko saaf karo (port conflict 530 fix)."""
    sh("pkill -f server.py", capture_output=True)
    sh("pkill -f 'cloudflared tunnel'", capture_output=True)
    # fuser se port free karo (agar available ho)
    sh("fuser -k %d/tcp" % port, capture_output=True)
    time.sleep(1.5)


def _start_server(port):
    """Server start karo, log file me likho (crash dikh sake)."""
    logf = open(SRV_LOG, "w")
    p = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=HERE,
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    return p, logf


def _wait_healthy(port, timeout=30):
    """Server ke /login ke 200/302 dene ka wait karo = origin ready."""
    url = "http://127.0.0.1:%d/login" % port
    for _ in range(timeout * 2):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status in (200, 302):
                    return True
        except urllib.error.HTTPError as e:
            if e.code in (200, 302, 401, 403):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main(password="admin", port=7860):
    os.chdir(HERE)
    os.environ["TERMINAL_PASSWORD"] = str(password)
    os.environ["PORT"] = str(port)

    _install_tmux()
    _install_pkgs()
    cf = _get_cloudflared()

    # purana kachra saaf
    _kill_old(port)

    # ---- server start + health check ----
    print("[*] Starting terminal server on port %s ..." % port)
    server, logf = _start_server(port)
    if not _wait_healthy(port):
        print("\n[!] Server start nahi hua. Last log:")
        try:
            print(open(SRV_LOG).read()[-1500:])
        except Exception:
            pass
        print("[!] Upar wali error fix karo, phir cell dobara chalao.")
        return
    print("[✓] Server healthy (origin ready).")

    # ---- watchdog: server crash -> auto restart (530 se bachao) ----
    state = {"server": server, "logf": logf, "stop": False}

    def watchdog():
        while not state["stop"]:
            time.sleep(3)
            if state["server"].poll() is not None:  # crash ho gaya
                print("\n[!] Server crash hua, restart kar raha hu ...")
                try:
                    state["logf"].close()
                except Exception:
                    pass
                _kill_old(port)
                s2, l2 = _start_server(port)
                state["server"], state["logf"] = s2, l2
                if _wait_healthy(port):
                    print("[✓] Server wapas zinda. (tunnel auto reconnect ho jayega)")
                else:
                    print("[!] Restart fail. Log:\n" + open(SRV_LOG).read()[-1200:])

    threading.Thread(target=watchdog, daemon=True).start()

    # ---- cloudflare tunnel ----
    print("[*] Opening Cloudflare tunnel ...\n")
    tunnel = subprocess.Popen(
        [cf, "tunnel", "--url", "http://127.0.0.1:%s" % port,
         "--no-autoupdate", "--loglevel", "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    url_found = {"u": None}

    def reader():
        pat = re.compile(r"https://[a-zA-Z0-9.\-]+\.trycloudflare\.com")
        for line in tunnel.stdout:
            m = pat.search(line)
            if m and not url_found["u"]:
                url_found["u"] = m.group(0)
                print("\n" + "#" * 56)
                print("#  ✅ YOUR TERMINAL IS LIVE:")
                print("#  " + url_found["u"])
                print("#  🔑 Password: " + str(password))
                print("#" * 56 + "\n")
            # error 530 / reconnect signals dikhao
            low = line.lower()
            if "unregistered" in low or "error=" in low or "failed to" in low:
                print("[cf] " + line.rstrip())

    threading.Thread(target=reader, daemon=True).start()

    for _ in range(40):
        if url_found["u"]:
            break
        time.sleep(1)
    if not url_found["u"]:
        print("[!] URL nahi mila abhi tak - thoda wait karein, ya cell dobara chalayein.")

    try:
        # tunnel ya server me se koi bhi mare to handle ho
        while True:
            time.sleep(2)
            if tunnel.poll() is not None:
                print("\n[!] Tunnel band ho gaya, restart kar raha hu ...")
                tunnel = subprocess.Popen(
                    [cf, "tunnel", "--url", "http://127.0.0.1:%s" % port,
                     "--no-autoupdate"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                threading.Thread(target=reader, daemon=True).start()
    except KeyboardInterrupt:
        print("\n[*] Stopping ...")
        state["stop"] = True
        tunnel.terminate()
        state["server"].terminate()


if __name__ == "__main__":
    pw = os.environ.get("TERMINAL_PASSWORD", "admin")
    main(password=pw)
