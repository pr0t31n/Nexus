#!/usr/bin/env python3
"""
Nexus — Central Hub
Discovers and monitors all active pr0t31n and third-party security platform
instances on every local network interface. Runs on port 5400.
"""
from __future__ import annotations

VERSION = "1.2.1"

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

# Maps wrapper tool name → install script path
_NEXUS_TOOL_INSTALL_MAP: dict[str, str] = {
    "crucible":     "/opt/pr0t31n/Crucible/install.py",
    "vantage":      "/opt/pr0t31n/Vantage/install.py",
    "spectre":      "/opt/pr0t31n/Spectre/install.py",
    "gauntlet":     "/opt/pr0t31n/Gauntlet/install.py",
    "bastion":      "/opt/pr0t31n/Bastion/install.py",
    "fracture":     "/opt/pr0t31n/Fracture/install.py",
    "siphon":       "/opt/pr0t31n/Siphon/install.py",
    "scattershot3": "/opt/pr0t31n/Scattershot3/install.py",
    "osift":        "/opt/pr0t31n/OSift/install.py",
    "gearbox":      "/opt/pr0t31n/Gearbox/install.py",
    "arbiter":      "/opt/pr0t31n/Arbiter/install.py",
}

import requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, emit
import _auth

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"]               = _auth.load_secret_key()
app.config["SESSION_COOKIE_SECURE"]    = False  # self-signed cert; HTTPS enforced server-side
app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"]  = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
_auth.init_auth()
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

NEXUS_DIR = Path(__file__).parent.resolve()

# ---------------------------------------------------------------------------
# Hostname resolution — clients file + ZeroTier
# ---------------------------------------------------------------------------

_NET_CLIENTS  = ipaddress.ip_network("10.87.84.0/24")
_NET_ZEROTIER = ipaddress.ip_network("10.87.86.0/23")

_ZEROTIER_API = "https://api.zerotier.com/api/v1/network/fada62b01554c3b1/member"
_ZEROTIER_KEY = "g2hetRnuthhip9ALj9pW2RlbqIJcBv0E"
_ZEROTIER_TTL = 300   # seconds between API refreshes

_clients_map: dict = {}
_zt_map: dict      = {}
_zt_lock           = threading.Lock()
_zt_last_fetch: float = 0.0


def _load_clients() -> dict:
    """Parse NEXUS_DIR/clients (IP<whitespace>hostname per line) into a dict."""
    mapping: dict = {}
    try:
        for line in (NEXUS_DIR / "clients").read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                mapping[parts[0]] = parts[1]
    except Exception:
        pass
    return mapping


def _refresh_zerotier() -> None:
    """Fetch ZeroTier member list and rebuild _zt_map (IP → hostname)."""
    global _zt_last_fetch
    now = time.time()
    with _zt_lock:
        if now - _zt_last_fetch < _ZEROTIER_TTL:
            return
    try:
        r = requests.get(
            _ZEROTIER_API,
            headers={"Authorization": f"token {_ZEROTIER_KEY}"},
            timeout=5,
        )
        if not r.ok:
            return
        new_map: dict = {}
        for member in r.json():
            name = member.get("name", "").strip()
            if name.endswith(".ciso"):
                name = name[:-5]
            for ip in member.get("config", {}).get("ipAssignments", []):
                if ip and name:
                    new_map[ip] = name
        with _zt_lock:
            _zt_map.clear()
            _zt_map.update(new_map)
            _zt_last_fetch = time.time()
    except Exception:
        pass


# Load clients at startup
_clients_map = _load_clients()


@app.after_request
def _security_headers(response):
    return _auth.apply_security_headers(response)


@app.before_request
def _auth_gate():
    return _auth.auth_gate()


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

SERVICES = [
    {"id": "gauntlet",  "name": "Gauntlet",   "port": 5000,  "proto": "https",
     "desc": "Device Security Assessment", "status_api": True,  "icon": "chip"},
    {"id": "spiderfoot","name": "SpiderFoot",  "port": 5001,  "proto": "http",
     "desc": "OSINT Automation",            "status_api": False, "icon": "spider"},
    {"id": "vantage",   "name": "Vantage",     "port": 5100,  "proto": "https",
     "desc": "Recon Platform",              "status_api": True,  "icon": "scope"},
    {"id": "quiver",    "name": "Quiver",      "port": 5200,  "proto": "https",
     "desc": "Pentest Hardware Manager",    "status_api": False, "icon": "quiver"},
    {"id": "spectre",   "name": "Spectre",     "port": 5300,  "proto": "https",
     "desc": "Wireless Assessment",         "status_api": True,  "icon": "wave"},
    {"id": "nessus",    "name": "Nessus",      "port": 8834,  "proto": "https",
     "desc": "Vulnerability Scanner",       "status_api": False, "icon": "scan"},
    {"id": "hitman",  "name": "HiTMan",    "port": 8443,  "proto": "https",
     "desc": "Security Management",         "status_api": False, "icon": "lock"},
    {"id": "crucible",  "name": "Crucible",    "port": 5500,  "proto": "https",
     "desc": "Web & API Assessment",        "status_api": True,  "icon": "crucible"},
]

# ---------------------------------------------------------------------------
# Network interface discovery
# ---------------------------------------------------------------------------

def _local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses on this host, plus 127.0.0.1."""
    ips = {"127.0.0.1"}
    try:
        # Use 'ip' on Linux, 'ifconfig' on macOS
        if os.path.exists("/sbin/ip") or os.path.exists("/usr/sbin/ip"):
            out = subprocess.check_output(["ip", "-4", "addr"], text=True, timeout=5)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    parts = line.split()
                    ip = parts[1].split("/")[0]
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


def _nmap_hostname(ip: str) -> Optional[str]:
    """Run nmap -sL against ip and return the resolved hostname, or None."""
    try:
        out = subprocess.check_output(
            ["nmap", "-sL", ip],
            text=True, timeout=10, stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if line.startswith("Nmap scan report for "):
                rest = line[len("Nmap scan report for "):]
                # Resolved format: "hostname (ip)" — unresolved: just "ip"
                if rest.endswith(f"({ip})"):
                    name = rest[:-(len(ip) + 3)].strip()
                    if name and name != ip:
                        return name.split(".")[0]
    except Exception:
        pass
    return None


def _hostname_for(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
        if addr in _NET_CLIENTS:
            return _clients_map.get(ip, ip)
        if addr in _NET_ZEROTIER:
            with _zt_lock:
                name = _zt_map.get(ip)
            if name:
                return name
    except ValueError:
        pass
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0]
    except Exception:
        pass
    name = _nmap_hostname(ip)
    return name if name else ip


def _scan_status(ip: str, port: int, proto: str, timeout: float = 1.5) -> Optional[bool]:
    """Query /api/nexus-status and return scanning bool, or None on failure."""
    try:
        url = f"{proto}://{ip}:{port}/api/nexus-status"
        r = requests.get(url, timeout=timeout, verify=False)
        if r.ok:
            return r.json().get("scanning", False)
    except Exception:
        pass
    return None

# ---------------------------------------------------------------------------
# Discovery state
# ---------------------------------------------------------------------------

_instances: dict = {}    # key: (hostname, service_id) → instance dict
_lock = threading.Lock()
_last_scan: Optional[datetime] = None
_scan_interval   = 30    # seconds between scans
_NETSCAN_WORKERS = 128   # parallel TCP probe threads
_NETSCAN_TIMEOUT = 0.15  # per-probe timeout for host discovery (seconds)


def _scan_networks(found: dict, local_ips: set, local_hostname: str) -> None:
    """
    Probe every host in the defined internal networks for each service port.
    IPs that belong to this machine use local_hostname so they merge with the
    entries already created by the local-interface scan rather than creating
    duplicates.  All other IPs are resolved normally via _hostname_for().
    """
    tasks = [
        (str(host), svc)
        for net in (_NET_CLIENTS, _NET_ZEROTIER)
        for host in net.hosts()
        for svc in SERVICES
    ]

    def _probe(ip: str, svc: dict):
        return (ip, svc) if _tcp_open(ip, svc["port"], _NETSCAN_TIMEOUT) else None

    hits = []
    with ThreadPoolExecutor(max_workers=_NETSCAN_WORKERS) as ex:
        futures = {ex.submit(_probe, ip, svc) for ip, svc in tasks}
        for fut in as_completed(futures):
            result = fut.result()
            if result is not None:
                hits.append(result)

    for ip, svc in hits:
        hostname = local_hostname if ip in local_ips else _hostname_for(ip)
        key = (hostname, svc["id"])
        if key not in found:
            found[key] = {
                "id":       svc["id"],
                "name":     svc["name"],
                "desc":     svc["desc"],
                "icon":     svc["icon"],
                "proto":    svc["proto"],
                "port":     svc["port"],
                "hostname": hostname,
                "ips":      [],
                "scanning": None,
                "seen_at":  datetime.now(timezone.utc).isoformat(),
            }
        if ip not in found[key]["ips"]:
            found[key]["ips"].append(ip)
        if svc["status_api"]:
            status = _scan_status(ip, svc["port"], svc["proto"])
            if status is not None:
                found[key]["scanning"] = status


def _do_scan():
    global _last_scan
    _refresh_zerotier()

    # Resolve this machine's identity once per scan.
    # All local interface IPs — real and loopback — belong to this host.
    # Sort so 127.0.0.1 comes last: the first IP in the list becomes the
    # primary URL, so a real routable address is preferred over loopback.
    local_hostname = socket.gethostname().split(".")[0]
    raw_ips = _local_ips()
    local_ips_set = set(raw_ips)
    ips = sorted(raw_ips, key=lambda x: (x == "127.0.0.1", x))

    found: dict = {}

    for svc in SERVICES:
        port = svc["port"]
        for ip in ips:
            if not _tcp_open(ip, port):
                continue

            # All local IPs are this host — use the machine hostname so every
            # interface address merges into one entry per service, and
            # 127.0.0.1 (sorted last) never spawns its own separate instance.
            key = (local_hostname, svc["id"])

            if key not in found:
                found[key] = {
                    "id":       svc["id"],
                    "name":     svc["name"],
                    "desc":     svc["desc"],
                    "icon":     svc["icon"],
                    "proto":    svc["proto"],
                    "port":     port,
                    "hostname": local_hostname,
                    "ips":      [],
                    "scanning": None,
                    "seen_at":  datetime.now(timezone.utc).isoformat(),
                }
            if ip not in found[key]["ips"]:
                found[key]["ips"].append(ip)

            # Scan status (only for pr0t31n apps, not Quiver/third-party)
            if svc["status_api"]:
                status = _scan_status(ip, port, svc["proto"])
                if status is not None:
                    found[key]["scanning"] = status

    _scan_networks(found, local_ips_set, local_hostname)

    with _lock:
        _instances.clear()
        _instances.update(found)
        _last_scan = datetime.now(timezone.utc)

    socketio.emit("instances_update", _serialised_instances())


def _serialised_instances() -> list:
    with _lock:
        result = []
        for (hostname, svc_id), inst in _instances.items():
            obj = dict(inst)
            # Build primary URL from first IP
            primary_ip = inst["ips"][0] if inst["ips"] else "127.0.0.1"
            obj["url"] = f"{inst['proto']}://{primary_ip}:{inst['port']}"
            obj["extra_urls"] = [
                f"{inst['proto']}://{ip}:{inst['port']}"
                for ip in inst["ips"][1:]
            ]
            result.append(obj)
    # Sort: pr0t31n apps first by port, then third-party
    pr0t31n_ids = {"gauntlet", "vantage", "quiver", "spectre"}
    result.sort(key=lambda x: (0 if x["id"] in pr0t31n_ids else 1, x["port"]))
    return result


def _background_scanner():
    while True:
        try:
            _do_scan()
        except Exception:
            pass
        time.sleep(_scan_interval)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    instances = _serialised_instances()
    last = _last_scan.strftime("%H:%M:%S UTC") if _last_scan else "scanning…"
    return render_template("index.html", instances=instances, last_scan=last,
                           version=VERSION, services=SERVICES)


@app.route("/api/instances")
def api_instances():
    return jsonify({
        "instances": _serialised_instances(),
        "last_scan": _last_scan.isoformat() if _last_scan else None,
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"status": "scan started"})


@app.route("/api/nexus-status")
def api_nexus_status():
    """Standard pr0t31n health probe endpoint."""
    return jsonify({
        "tool":    "nexus",
        "version": VERSION,
        "scanning": False,
    })


@app.route("/api/install-tool", methods=["POST"])
def api_install_tool():
    """
    Trigger a background installation of a pr0t31n tool on this host.
    Called by Arbiter's execute-recommendation flow when the required tool
    is missing but Nexus (Arsenal) is available.
    """
    data = request.get_json(force=True, silent=True) or {}
    tool = str(data.get("tool", "")).strip().lower()
    if not tool or tool not in _NEXUS_TOOL_INSTALL_MAP:
        valid = sorted(_NEXUS_TOOL_INSTALL_MAP.keys())
        return jsonify({"ok": False, "error": f"Unknown tool '{tool}'. Valid: {valid}"}), 400

    script = _NEXUS_TOOL_INSTALL_MAP[tool]
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
    return jsonify({"ok": True, "status": "install_started", "tool": tool})


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not _auth.validate_csrf(request.form.get("csrf_token")):
            return render_template("login.html", error="Invalid request token.", csrf_token=_auth.generate_csrf())
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _auth.verify_credentials(username, password):
            session.permanent = True
            session["authenticated"] = True
            session["username"] = username
            if _auth.must_change_password():
                return redirect(url_for("change_password"))
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials.", username=username,
                               csrf_token=_auth.generate_csrf())
    return render_template("login.html", csrf_token=_auth.generate_csrf())


@app.route("/logout")
def logout():
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
                return render_template("change_password.html", error="Password must be at least 8 characters.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if new_pw != confirm:
                return render_template("change_password.html", error="Passwords do not match.",
                                       force=force, csrf_token=_auth.generate_csrf())
            err = _auth.is_weak_password(new_pw, new_user)
            if err:
                return render_template("change_password.html", error=err,
                                       force=force, csrf_token=_auth.generate_csrf())
            _auth.update_credentials(new_user, new_pw)
            session["username"] = new_user
            return redirect(url_for("index"))
        else:
            current = request.form.get("current_password", "")
            if not _auth.check_current_password(current):
                return render_template("change_password.html", error="Current password is incorrect.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if len(new_pw) < 8:
                return render_template("change_password.html", error="Password must be at least 8 characters.",
                                       force=force, csrf_token=_auth.generate_csrf())
            if new_pw != confirm:
                return render_template("change_password.html", error="Passwords do not match.",
                                       force=force, csrf_token=_auth.generate_csrf())
            err = _auth.is_weak_password(new_pw, _auth.get_username())
            if err:
                return render_template("change_password.html", error=err,
                                       force=force, csrf_token=_auth.generate_csrf())
            _auth.update_password(new_pw)
            return redirect(url_for("index"))
    return render_template("change_password.html", force=force, csrf_token=_auth.generate_csrf())

# ---------------------------------------------------------------------------
# SocketIO
# ---------------------------------------------------------------------------

@socketio.on("connect")
def on_connect():
    if not session.get("authenticated"):
        return False
    emit("instances_update", _serialised_instances())


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
    print(f"  Nexus — Central Hub  v{VERSION}")
    print("  https://0.0.0.0:5400")
    print("=" * 56)
    # Kick off the background scanner
    t = threading.Thread(target=_background_scanner, daemon=True)
    t.start()
    socketio.run(app, debug=False, host="0.0.0.0", port=5400,
                 allow_unsafe_werkzeug=True, ssl_context=_auth.make_ssl_context())
