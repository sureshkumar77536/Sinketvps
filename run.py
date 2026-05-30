#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SinketVPS - Notebook launcher (Colab / Kaggle friendly)
-------------------------------------------------------
Ek hi cell se sab kuch:
  - deps install (tmux, cloudflared, python pkgs)
  - server start
  - cloudflare quick-tunnel + clean URL print

Usage (Colab/Kaggle cell):
    !git clone https://github.com/sureshkumar77536/Sinketvps
    %cd Sinketvps
    !pip -q install flask flask-socketio simple-websocket
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

HERE = os.path.dirname(os.path.abspath(__file__))


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


def main(password="admin", port=7860):
    os.chdir(HERE)
    os.environ["TERMINAL_PASSWORD"] = str(password)
    os.environ["PORT"] = str(port)

    _install_tmux()
    _install_pkgs()
    cf = _get_cloudflared()

    # start server
    print("[*] Starting terminal server on port %s ..." % port)
    server = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=HERE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    time.sleep(4)

    # start cloudflare tunnel
    print("[*] Opening Cloudflare tunnel ...\n")
    tunnel = subprocess.Popen(
        [cf, "tunnel", "--url", "http://localhost:%s" % port, "--no-autoupdate"],
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

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    # wait up to 40s for URL
    for _ in range(40):
        if url_found["u"]:
            break
        time.sleep(1)
    if not url_found["u"]:
        print("[!] URL nahi mila abhi tak - thoda wait karein, ya cell dobara chalayein.")

    try:
        server.wait()
    except KeyboardInterrupt:
        print("\n[*] Stopping ...")
        tunnel.terminate()
        server.terminate()


if __name__ == "__main__":
    pw = os.environ.get("TERMINAL_PASSWORD", "admin")
    main(password=pw)
