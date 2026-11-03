#!/usr/bin/env python3
"""
Nexus v2.0 — Network Fabric System
Fleet management hub for the pr0t31n toolkit. Discovers and manages
tool instances across any network topology — VPN, overlay, cloud, or LAN.
Runs on port 5400.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

VERSION = Path(__file__).resolve().parent.joinpath("VERSION").read_text().strip()

import requests
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
app.register_blueprint(admin_bp)


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
# Network interface discovery
# ---------------------------------------------------------------------------

def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        if os.path.exists("/sbin/ip") or os.path.exists("/usr/sbin/ip"):
            out = subprocess.check_output(["ip", "-4", "addr"], text=True, timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    if not ip.startswith("127."):
                        ips.add(ip)
        else:
            out = subprocess.check_output(["ifconfig"], text=True, timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet ") and "127.0.0.1" not in line:
                    parts = line.split()
                    idx = parts.index("inet") + 1
                    ip = parts[idx]
                    if not ip.startswith("127."):
                        ips.add(ip)
    except Exception:
        pass
    return sorted(ips)


def _tcp_open(ip: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _scan_status(ip: str, port: int, proto: str, timeout: float = 1.5) -> Optional[dict]:
    try:
        url = f"{proto}://{ip}:{port}/api/nexus-status"
        r = requests.get(url, timeout=timeout, verify=False)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Discovery — local scan (Phase 1 minimal; Phase 2 replaces with full fleet discovery)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_last_scan: Optional[datetime] = None
_scan_running = False


def _do_scan():
    global _last_scan, _scan_running
    if _scan_running:
        return
    _scan_running = True
    try:
        local_hostname = socket.gethostname().split(".")[0]
        local_ips = set(_local_ips())
        ips = sorted(local_ips, key=lambda x: (x == "127.0.0.1", x))
        scan_timeout = float(mdb.get_setting("scan_timeout_s", "0.4"))

        node_id = mdb.upsert_fleet_node(
            local_hostname,
            ip_primary=ips[0] if ips else "127.0.0.1",
            status="online",
            os_family=sys.platform,
        )

        for svc in TOOL_REGISTRY:
            if svc["id"] == "nexus":
                continue
            for ip in ips:
                if _tcp_open(ip, svc["port"], scan_timeout):
                    extra = {}
                    if svc["status_api"]:
                        info = _scan_status(ip, svc["port"], svc["proto"])
                        if info:
                            extra["version"] = info.get("version", "")
                            extra["scanning"] = 1 if info.get("scanning") else 0
                    mdb.upsert_tool_instance(
                        node_id, svc["id"], svc["port"],
                        proto=svc["proto"], status="online", **extra,
                    )
                    break

        # Probe configured fabric subnets (Phase 2 replaces this with full fabric-aware discovery)
        fabrics = mdb.get_fabrics(enabled_only=True)
        workers = int(mdb.get_setting("scan_workers", "32"))
        for fab in fabrics:
            subnets = json.loads(fab.get("subnets", "[]"))
            for subnet_str in subnets:
                try:
                    net = ipaddress.ip_network(subnet_str, strict=False)
                except ValueError:
                    continue
                tasks = [
                    (str(host), svc)
                    for host in net.hosts()
                    for svc in TOOL_REGISTRY
                    if svc["id"] != "nexus"
                ]
                hits = []
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futs = {
                        ex.submit(_tcp_open, ip, svc["port"], scan_timeout): (ip, svc)
                        for ip, svc in tasks
                    }
                    for fut in as_completed(futs):
                        if fut.result():
                            hits.append(futs[fut])

                for ip, svc in hits:
                    hostname = local_hostname if ip in local_ips else ip
                    nid = mdb.upsert_fleet_node(
                        hostname, ip_primary=ip,
                        status="online",
                    )
                    mdb.upsert_node_address(nid, fab["id"], ip, lane_status="up")
                    extra = {}
                    if svc["status_api"]:
                        info = _scan_status(ip, svc["port"], svc["proto"])
                        if info:
                            extra["version"] = info.get("version", "")
                            extra["scanning"] = 1 if info.get("scanning") else 0
                    mdb.upsert_tool_instance(
                        nid, svc["id"], svc["port"],
                        proto=svc["proto"], status="online", **extra,
                    )

        with _lock:
            _last_scan = datetime.now(timezone.utc)

        socketio.emit("fleet_update", _fleet_payload())
    finally:
        _scan_running = False


def _fleet_payload() -> dict:
    nodes = mdb.get_fleet_nodes()
    for n in nodes:
        n["tools"] = mdb.get_tool_instances(n["id"])
        n["addresses"] = mdb.get_node_addresses(n["id"])
    return {
        "nodes": nodes,
        "stats": mdb.fleet_stats(),
        "last_scan": _last_scan.isoformat() if _last_scan else None,
    }


def _background_scanner():
    while True:
        try:
            _do_scan()
        except Exception:
            pass
        interval = int(mdb.get_setting("discovery_interval_s", "300"))
        time.sleep(interval)


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


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/fleet")
def api_fleet():
    return jsonify(_fleet_payload())


@app.route("/api/scan", methods=["POST"])
def api_scan():
    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"status": "scan started"})


@app.route("/api/nexus-status")
def api_nexus_status():
    return jsonify({
        "tool":     "nexus",
        "version":  VERSION,
        "scanning": _scan_running,
    })


@app.route("/api/install-tool", methods=["POST"])
def api_install_tool():
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
    emit("fleet_update", _fleet_payload())


@socketio.on("request_scan")
def on_request_scan():
    if not session.get("authenticated"):
        return
    threading.Thread(target=_do_scan, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 56)
    print(f"  Nexus — Network Fabric System  v{VERSION}")
    print(f"  https://0.0.0.0:{NEXUS_PORT}")
    print("=" * 56)
    t = threading.Thread(target=_background_scanner, daemon=True)
    t.start()
    socketio.run(app, debug=False, host="0.0.0.0", port=NEXUS_PORT,
                 allow_unsafe_werkzeug=True, ssl_context=_auth.make_ssl_context())
