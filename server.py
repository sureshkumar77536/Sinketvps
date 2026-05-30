#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SinketVPS - Web Terminal Backend
--------------------------------
Ek macOS-style web terminal jo Colab/Kaggle pe chalta hai.
- tmux backed sessions (reset nahi hote, reconnect par waise hi)
- multiple tabs (har tab = ek tmux session)
- password protected
- mobile friendly (frontend handle karta hai)
"""

import os
import pty
import select
import struct
import fcntl
import termios
import signal
import shlex
import subprocess
import threading
import secrets

from flask import Flask, request, render_template, session, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit, disconnect

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
PASSWORD = os.environ.get("TERMINAL_PASSWORD", "admin")
SECRET_KEY = os.environ.get("TERMINAL_SECRET", secrets.token_hex(16))
SHELL = os.environ.get("TERMINAL_SHELL", "/bin/bash")
PORT = int(os.environ.get("PORT", "7860"))

# tmux available? agar nahi to plain shell par fallback
def _has_tmux():
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, check=True)
        return True
    except Exception:
        return False

USE_TMUX = _has_tmux()

app = Flask(__name__, template_folder="static", static_folder="static", static_url_path="/static")
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    ping_timeout=60, ping_interval=25)

# ----------------------------------------------------------------------------
# Terminal manager
# ----------------------------------------------------------------------------
# term_id -> {"fd": master_fd, "pid": pid, "thread": t}
terminals = {}
term_lock = threading.Lock()


def _set_winsize(fd, rows, cols):
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass


def _spawn(term_id, rows, cols):
    """Spawn a PTY attached to a tmux session (or plain shell)."""
    master_fd, slave_fd = pty.openpty()
    _set_winsize(master_fd, rows, cols)

    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["LANG"] = env.get("LANG", "C.UTF-8")
    env["PS1"] = env.get("PS1", "")

    if USE_TMUX:
        # -A : attach if exists, create if not -> persistence
        session_name = "sk_%s" % term_id.replace("-", "")[:20]
        conf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmux.conf")
        cmd = ["tmux"]
        if os.path.exists(conf):
            cmd += ["-f", conf]
        cmd += ["-u", "new-session", "-A", "-s", session_name]
    else:
        cmd = [SHELL, "-l"]

    pid = subprocess.Popen(
        cmd,
        preexec_fn=os.setsid,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        cwd=os.path.expanduser("~"),
        close_fds=True,
    ).pid

    os.close(slave_fd)

    with term_lock:
        terminals[term_id] = {"fd": master_fd, "pid": pid, "thread": None}

    t = socketio.start_background_task(_read_loop, term_id, master_fd)
    with term_lock:
        if term_id in terminals:
            terminals[term_id]["thread"] = t
    return pid


def _read_loop(term_id, fd):
    """Read PTY output and push to the matching room."""
    max_read = 65536
    while True:
        socketio.sleep(0)
        try:
            r, _, _ = select.select([fd], [], [], 0.2)
        except (OSError, ValueError):
            break
        if not r:
            with term_lock:
                if term_id not in terminals:
                    break
            continue
        try:
            data = os.read(fd, max_read)
        except OSError:
            break
        if not data:
            break
        try:
            socketio.emit("output",
                          {"id": term_id, "data": data.decode(errors="replace")},
                          room=term_id)
        except Exception:
            break

    # cleanup
    with term_lock:
        terminals.pop(term_id, None)
    socketio.emit("term_closed", {"id": term_id}, room=term_id)


# ----------------------------------------------------------------------------
# HTTP routes
# ----------------------------------------------------------------------------
@app.route("/")
def index():
    if not session.get("authed"):
        return redirect(url_for("login"))
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        if request.form.get("password", "") == PASSWORD:
            session["authed"] = True
            return redirect(url_for("index"))
        error = "Galat password / Wrong password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/sessions")
def api_sessions():
    """List existing tmux sessions (for restoring tabs)."""
    if not session.get("authed"):
        return jsonify([])
    if not USE_TMUX:
        return jsonify([])
    try:
        out = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True
        )
        names = [n.strip() for n in out.stdout.splitlines() if n.strip().startswith("sk_")]
        # strip the sk_ prefix -> original-ish id
        return jsonify(names)
    except Exception:
        return jsonify([])


# ----------------------------------------------------------------------------
# Socket.IO events
# ----------------------------------------------------------------------------
def _authed():
    return session.get("authed", False)


@socketio.on("connect")
def on_connect():
    if not _authed():
        return False  # reject
    emit("ready", {"tmux": USE_TMUX})


@socketio.on("start")
def on_start(data):
    if not _authed():
        disconnect()
        return
    term_id = str(data.get("id"))
    rows = int(data.get("rows", 24))
    cols = int(data.get("cols", 80))
    from flask_socketio import join_room
    join_room(term_id)

    with term_lock:
        exists = term_id in terminals

    if not exists:
        _spawn(term_id, rows, cols)
    else:
        # already running -> refresh size, re-render via tmux
        with term_lock:
            fd = terminals[term_id]["fd"]
        _set_winsize(fd, rows, cols)
        if USE_TMUX:
            session_name = "sk_%s" % term_id.replace("-", "")[:20]
            subprocess.run(["tmux", "refresh-client", "-t", session_name],
                           capture_output=True)
    emit("started", {"id": term_id})


@socketio.on("input")
def on_input(data):
    if not _authed():
        disconnect()
        return
    term_id = str(data.get("id"))
    with term_lock:
        info = terminals.get(term_id)
    if info:
        try:
            os.write(info["fd"], data.get("data", "").encode())
        except OSError:
            pass


@socketio.on("resize")
def on_resize(data):
    if not _authed():
        return
    term_id = str(data.get("id"))
    rows = int(data.get("rows", 24))
    cols = int(data.get("cols", 80))
    with term_lock:
        info = terminals.get(term_id)
    if info:
        _set_winsize(info["fd"], rows, cols)


@socketio.on("kill")
def on_kill(data):
    """Permanently kill a terminal + its tmux session."""
    if not _authed():
        return
    term_id = str(data.get("id"))
    with term_lock:
        info = terminals.pop(term_id, None)
    if USE_TMUX:
        session_name = "sk_%s" % term_id.replace("-", "")[:20]
        subprocess.run(["tmux", "kill-session", "-t", session_name],
                       capture_output=True)
    if info:
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except OSError:
            pass
        try:
            os.close(info["fd"])
        except OSError:
            pass


if __name__ == "__main__":
    print("=" * 60)
    print(" SinketVPS Web Terminal")
    print(" tmux:", "ON" if USE_TMUX else "OFF (plain shell)")
    print(" port:", PORT)
    print(" password:", PASSWORD)
    print("=" * 60)
    socketio.run(app, host="0.0.0.0", port=PORT, allow_unsafe_werkzeug=True)
