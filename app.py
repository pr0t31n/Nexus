#!/usr/bin/env python3
"""
Nexus v2.1 — Network Fabric System
Fleet management hub for the pr0t31n toolkit. Discovers and manages
tool instances across any network topology — VPN, overlay, cloud, or LAN.
Runs on port 5400.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import timedelta
from pathlib import Path

VERSION = Path(__file__).resolve().parent.joinpath("VERSION").read_text().strip()

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit

import _auth
from config import NEXUS_DIR, NEXUS_PORT, TOOL_REGISTRY, TOOL_INSTALL_MAP
from models import db as mdb
from services import credential_store

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"]               = _auth.load_secret_key()
app.config["SESSION_COOKIE_NAME"]      = "nexus_session"
app.config["SESSION_COOKIE_SECURE"]    = False
app.config["SESSION_COOKIE_HTTPONLY"]   = True
app.config["SESSION_COOKIE_SAMESITE"]  = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
_auth.init_auth()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Database + credential store
_DB_PATH = NEXUS_DIR / "nexus.db"
mdb.init_db(_DB_PATH)
credential_store.init_credential_store(NEXUS_DIR)

# Register blueprints
from routes.admin import admin_bp
from routes.fleet import fleet_bp
from routes.fabrics import fabrics_bp

for bp in (admin_bp, fleet_bp, fabrics_bp):
    app.register_blueprint(bp)


@app.after_request
def _security_headers(response):
    return _auth.apply_security_headers(response)


@app.before_request
def _auth_gate():
    return _auth.auth_gate()


# ---------------------------------------------------------------------------
# Template globals
# ---------------------------------------------------------------------------

@app.context_processor
def _inject_globals():
    return {
        "version": VERSION,
        "app_version": VERSION,
        "services": TOOL_REGISTRY,
    }


# ---------------------------------------------------------------------------
# Fabric Manager + Fleet Discovery (Phase 2)
# ---------------------------------------------------------------------------

from services.fabric_manager import FabricManager
from services.fleet_discovery import FleetDiscovery

_fabric_manager = FabricManager(socketio=socketio)
_fleet_discovery = FleetDiscovery(_fabric_manager, socketio=socketio)

app.config["fabric_manager"] = _fabric_manager
app.config["fleet_discovery"] = _fleet_discovery


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("fleet.html")


@app.route("/fleet")
def fleet_page():
    return render_template("fleet.html")


@app.route("/terminal")
def terminal_page():
    return render_template("terminal.html")


@app.route("/arsenal")
def arsenal_page():
    return render_template("arsenal.html")


@app.route("/api/install-tool", methods=["POST"])
def api_install_tool():
    import subprocess
    data = request.get_json(force=True, silent=True) or {}
    tool = str(data.get("tool", "")).strip().lower()
    if not tool or tool not in TOOL_INSTALL_MAP:
        valid = sorted(TOOL_INSTALL_MAP.keys())
        return jsonify({"ok": False, "error": f"Unknown tool '{tool}'. Valid: {valid}"}), 400
    script = TOOL_INSTALL_MAP[tool]
    if not Path(script).exists():
        return jsonify({"ok": False, "error": f"Install script not found: {script}"}), 404

    def _run():
        try:
            subprocess.run(
                [sys.executable, script],
                timeout=300, check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name=f"nexus-install-{tool}").start()
    mdb.audit("install_tool", f"Started install: {tool}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "status": "install_started", "tool": tool})


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not _auth.validate_csrf(request.form.get("csrf_token")):
            return render_template("login.html", error="Invalid request token.",
                                   csrf_token=_auth.generate_csrf())
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _auth.verify_credentials(username, password):
            session.permanent = True
            session["authenticated"] = True
            session["username"] = username
            session["role"] = "admin"
            if _auth.must_change_password():
                return redirect(url_for("change_password"))
            mdb.audit("login", f"User logged in", username=username,
                      ip=request.remote_addr or "")
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials.",
                               username=username, csrf_token=_auth.generate_csrf())
    return render_template("login.html", csrf_token=_auth.generate_csrf())


@app.route("/logout")
def logout():
    user = session.get("username", "unknown")
    mdb.audit("logout", "User logged out", username=user,
              ip=request.remote_addr or "")
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/session_ping", methods=["POST"])
def session_ping():
    if not session.get("authenticated"):
        return jsonify({"ok": False}), 401
    session.modified = True
    return jsonify({"ok": True})


@app.route("/auth/change-password", methods=["GET", "POST"])
def change_password():
    force = _auth.must_change_password()
    if request.method == "POST":
        if not _auth.validate_csrf(request.form.get("csrf_token")):
            return render_template("change_password.html", error="Invalid request token.",
                                   force=force, csrf_token=_auth.generate_csrf())
        new_pw  = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if force:
            new_user = request.form.get("new_username", "").strip()
            err = _auth.is_weak_username(new_user)
            if err:
                return render_template("change_password.html", error=err,
                                       force=force, csrf_token=_auth.generate_csrf())
            if len(new_pw) < 8:
                return render_template("change_password.html",
                                       error="Password must be at least 8 characters.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if new_pw != confirm:
                return render_template("change_password.html",
                                       error="Passwords do not match.",
                                       force=force, csrf_token=_auth.generate_csrf())
            err = _auth.is_weak_password(new_pw, new_user)
            if err:
                return render_template("change_password.html", error=err,
                                       force=force, csrf_token=_auth.generate_csrf())
            _auth.update_credentials(new_user, new_pw)
            session["username"] = new_user
            mdb.audit("password_change", "Initial credentials set", username=new_user,
                      ip=request.remote_addr or "")
            return redirect(url_for("index"))
        else:
            current = request.form.get("current_password", "")
            if not _auth.check_current_password(current):
                return render_template("change_password.html",
                                       error="Current password is incorrect.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if len(new_pw) < 8:
                return render_template("change_password.html",
                                       error="Password must be at least 8 characters.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if new_pw != confirm:
                return render_template("change_password.html",
                                       error="Passwords do not match.",
                                       force=force, csrf_token=_auth.generate_csrf())
            err = _auth.is_weak_password(new_pw, _auth.get_username())
            if err:
                return render_template("change_password.html", error=err,
                                       force=force, csrf_token=_auth.generate_csrf())
            _auth.update_password(new_pw)
            mdb.audit("password_change", "Password changed",
                      username=session.get("username", "unknown"),
                      ip=request.remote_addr or "")
            return redirect(url_for("index"))
    return render_template("change_password.html", force=force,
                           csrf_token=_auth.generate_csrf())


# ---------------------------------------------------------------------------
# SocketIO
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    if not session.get("authenticated"):
        return False
    emit("fleet_update", _fleet_discovery.fleet_payload())


@socketio.on("request_scan")
def on_request_scan():
    if not session.get("authenticated"):
        return
    _fleet_discovery.trigger_scan()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 56)
    print(f"  Nexus — Network Fabric System  v{VERSION}")
    print(f"  https://0.0.0.0:{NEXUS_PORT}")
    print("=" * 56)
    _fabric_manager.start()
    _fleet_discovery.start()
    socketio.run(app, debug=False, host="0.0.0.0", port=NEXUS_PORT,
                 allow_unsafe_werkzeug=True, ssl_context=_auth.make_ssl_context())
