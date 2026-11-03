"""Nexus v2.5 — Remote session monitor.

Background thread that discovers and tracks remote access sessions
(SSH, RDP, VNC, mosh) to the Nexus server.  Enriches with username,
auth method, key fingerprint, GeoIP, MAC address, and session type.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from models import db as mdb

log = logging.getLogger("nexus.session_monitor")

# ---------------------------------------------------------------------------
# GeoIP — optional enrichment
# ---------------------------------------------------------------------------

_geoip_reader = None
_geoip_asn_reader = None

try:
    import geoip2.database as _gdb

    for _p in (
        Path("/usr/share/GeoIP/GeoLite2-City.mmdb"),
        Path("/opt/GeoIP/GeoLite2-City.mmdb"),
        Path("/usr/local/share/GeoIP/GeoLite2-City.mmdb"),
    ):
        if _p.exists():
            _geoip_reader = _gdb.Reader(str(_p))
            break
    for _p in (
        Path("/usr/share/GeoIP/GeoLite2-ASN.mmdb"),
        Path("/opt/GeoIP/GeoLite2-ASN.mmdb"),
        Path("/usr/local/share/GeoIP/GeoLite2-ASN.mmdb"),
    ):
        if _p.exists():
            _geoip_asn_reader = _gdb.Reader(str(_p))
            break
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REMOTE_ACCESS_PORTS = {22, 3389, 5900, 5901, 5902, 4200}

_PROCESS_METHOD_MAP = {
    "sshd": "ssh",
    "ssh": "ssh",
    "dropbear": "ssh",
    "xrdp": "rdp",
    "xrdp-sesman": "rdp",
    "xfreerdp": "rdp",
    "Xvnc": "vnc",
    "x11vnc": "vnc",
    "vino-server": "vnc",
    "screensharingd": "vnc",
    "ARDAgent": "ard",
    "mosh-server": "mosh",
}

_RFC1918 = re.compile(
    r"^(10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.|fe80:|fd[0-9a-f]{2}:)"
)

_AUTH_LOG_PATHS_LINUX = ["/var/log/auth.log", "/var/log/secure"]
_AUTH_LOG_PATHS_MACOS = ["/var/log/system.log"]

_RE_ACCEPTED_KEY = re.compile(
    r"Accepted publickey for (\S+) from ([\d.]+) port (\d+)"
    r"(?: \S+ (\S+))?.*?(SHA256:\S+|MD5:\S+|RSA \S+)?"
)
_RE_ACCEPTED_PW = re.compile(
    r"Accepted password for (\S+) from ([\d.]+) port (\d+)"
)
_RE_FAILED_PW = re.compile(
    r"Failed password for (?:invalid user )?(\S+) from ([\d.]+) port (\d+)"
)
_RE_DISCONNECT = re.compile(
    r"(?:Connection closed|Disconnected from(?: authenticating)?(?: invalid)?(?: user \S+)?) (?:by )?([\d.]+)"
)
_RE_ACCEPTED_KEYBOARD = re.compile(
    r"Accepted keyboard-interactive/pam for (\S+) from ([\d.]+) port (\d+)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str] | str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=isinstance(cmd, str),
        )
        return r.stdout
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# MAC resolution cache
# ---------------------------------------------------------------------------

_mac_cache: dict[str, tuple[str, float]] = {}
_MAC_TTL = 300.0


def _resolve_mac(ip: str) -> str:
    now = time.monotonic()
    cached = _mac_cache.get(ip)
    if cached and (now - cached[1]) < _MAC_TTL:
        return cached[0]
    out = _run(["arp", "-n", ip])
    mac = ""
    for line in out.splitlines():
        parts = line.split()
        for p in parts:
            if re.match(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", p):
                mac = p.lower()
                break
        if mac:
            break
    _mac_cache[ip] = (mac, now)
    return mac


# ---------------------------------------------------------------------------
# GeoIP helpers
# ---------------------------------------------------------------------------

def _geoip_lookup(ip: str) -> tuple[str, str]:
    if _RFC1918.match(ip) or ip.startswith("127.") or ip == "::1":
        return "", ""
    geo = ""
    asn = ""
    if _geoip_reader:
        try:
            resp = _geoip_reader.city(ip)
            parts = []
            if resp.city.name:
                parts.append(resp.city.name)
            if resp.country.name:
                parts.append(resp.country.name)
            geo = ", ".join(parts) if parts else ""
        except Exception:
            pass
    if _geoip_asn_reader:
        try:
            resp = _geoip_asn_reader.asn(ip)
            asn = f"AS{resp.autonomous_system_number} {resp.autonomous_system_organization or ''}".strip()
        except Exception:
            pass
    return geo, asn


# ---------------------------------------------------------------------------
# Interface detection
# ---------------------------------------------------------------------------

def _detect_interface(remote_ip: str) -> str:
    if sys.platform.startswith("linux"):
        out = _run(["ip", "route", "get", remote_ip])
        m = re.search(r"dev\s+(\S+)", out)
        return m.group(1) if m else ""
    elif sys.platform == "darwin":
        out = _run(["route", "-n", "get", remote_ip])
        m = re.search(r"interface:\s+(\S+)", out)
        return m.group(1) if m else ""
    return ""


# ---------------------------------------------------------------------------
# Session type heuristic
# ---------------------------------------------------------------------------

def _detect_session_type(pid: int) -> str:
    if sys.platform.startswith("linux"):
        try:
            fd_dir = Path(f"/proc/{pid}/fd")
            if fd_dir.exists():
                for fd in fd_dir.iterdir():
                    try:
                        target = fd.resolve()
                        if "/pts/" in str(target) or "/ptmx" in str(target):
                            return "interactive"
                    except Exception:
                        continue
            cmdline = Path(f"/proc/{pid}/cmdline").read_text()
            if "sftp" in cmdline:
                return "sftp"
            if "scp" in cmdline:
                return "scp"
            if "forwarding" in cmdline or "-L" in cmdline or "-R" in cmdline:
                return "tunnel"
        except Exception:
            pass
    elif sys.platform == "darwin":
        out = _run(["ps", "-o", "tty=", "-p", str(pid)])
        tty = out.strip()
        if tty and tty != "??" and tty != "-":
            return "interactive"
        out = _run(["ps", "-o", "args=", "-p", str(pid)])
        args = out.strip().lower()
        if "sftp" in args:
            return "sftp"
        if "scp" in args:
            return "scp"
    return "unknown"


# ---------------------------------------------------------------------------
# SessionMonitor
# ---------------------------------------------------------------------------

class SessionMonitor:
    def __init__(self, socketio=None):
        self._sio = socketio
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._known_sessions: dict[str, int] = {}
        self._log_offsets: dict[str, int] = {}
        self._missed_cycles: dict[str, int] = {}
        self._interval = 30

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="session-monitor",
        )
        self._thread.start()
        log.info("Session monitor started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Session monitor stopped")

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        time.sleep(5)
        while not self._stop.wait(self._interval):
            try:
                self._poll_active_connections()
            except Exception:
                log.exception("Error polling active connections")
            try:
                self._poll_auth_log()
            except Exception:
                log.exception("Error polling auth log")

    # -----------------------------------------------------------------------
    # Active connection polling
    # -----------------------------------------------------------------------

    def _poll_active_connections(self) -> None:
        current_keys: set[str] = set()
        connections = self._get_active_connections()

        for conn in connections:
            key = f"{conn['source_ip']}:{conn['source_port']}:{conn['pid']}"
            current_keys.add(key)

            if key not in self._known_sessions:
                geo, asn = _geoip_lookup(conn["source_ip"])
                mac = _resolve_mac(conn["source_ip"])
                iface = _detect_interface(conn["source_ip"])
                sess_type = _detect_session_type(conn["pid"]) if conn["pid"] else "unknown"

                sid = mdb.insert_session(
                    timestamp_start=_now(),
                    method=conn["method"],
                    interface=iface,
                    source_ip=conn["source_ip"],
                    source_mac=mac,
                    source_hostname=conn.get("hostname", ""),
                    username=conn.get("username", ""),
                    auth_method="",
                    auth_result="success",
                    session_type=sess_type,
                    key_fingerprint="",
                    geo_ip=geo,
                    asn=asn,
                    process_name=conn["process_name"],
                    process_pid=conn["pid"],
                )
                self._known_sessions[key] = sid
                self._missed_cycles.pop(key, None)
                if self._sio:
                    self._sio.emit("session_started", {
                        "id": sid, "ip": conn["source_ip"],
                        "method": conn["method"],
                    })

        stale = set(self._known_sessions.keys()) - current_keys
        for key in stale:
            missed = self._missed_cycles.get(key, 0) + 1
            self._missed_cycles[key] = missed
            if missed >= 2:
                sid = self._known_sessions.pop(key)
                self._missed_cycles.pop(key, None)
                mdb.update_session_end(sid, _now())
                if self._sio:
                    self._sio.emit("session_ended", {"id": sid})

    def _get_active_connections(self) -> list[dict]:
        results: list[dict] = []
        if sys.platform.startswith("linux"):
            results.extend(self._parse_ss_connections())
        elif sys.platform == "darwin":
            results.extend(self._parse_lsof_connections())
        return results

    def _parse_ss_connections(self) -> list[dict]:
        out = _run(["ss", "-tnpH", "state", "established"])
        results = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 6:
                continue
            local_addr = parts[3]
            peer_addr = parts[4]
            proc_info = " ".join(parts[5:])

            local_port = int(local_addr.rsplit(":", 1)[-1]) if ":" in local_addr else 0
            if local_port not in _REMOTE_ACCESS_PORTS:
                continue

            peer_ip = peer_addr.rsplit(":", 1)[0].strip("[]")
            peer_port = peer_addr.rsplit(":", 1)[-1] if ":" in peer_addr else "0"

            pid = 0
            pname = ""
            m = re.search(r'pid=(\d+)', proc_info)
            if m:
                pid = int(m.group(1))
            m = re.search(r'"([^"]+)"', proc_info)
            if m:
                pname = m.group(1)

            method = _PROCESS_METHOD_MAP.get(pname, "unknown")
            results.append({
                "source_ip": peer_ip,
                "source_port": peer_port,
                "pid": pid,
                "process_name": pname,
                "method": method,
            })
        return results

    def _parse_lsof_connections(self) -> list[dict]:
        remote_procs = "|".join(_PROCESS_METHOD_MAP.keys())
        out = _run(
            f"lsof -i -n -P 2>/dev/null | grep -E '({remote_procs})' | grep ESTABLISHED",
        )
        results = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 9:
                continue
            pname = parts[0]
            try:
                pid = int(parts[1])
            except ValueError:
                pid = 0
            name_col = parts[8] if len(parts) > 8 else ""
            m = re.search(r"->([\d.]+):(\d+)", name_col)
            if not m:
                m = re.search(r"->\[([0-9a-f:]+)\]:(\d+)", name_col)
            if not m:
                continue
            peer_ip = m.group(1)
            peer_port = m.group(2)
            method = _PROCESS_METHOD_MAP.get(pname, "unknown")
            results.append({
                "source_ip": peer_ip,
                "source_port": peer_port,
                "pid": pid,
                "process_name": pname,
                "method": method,
            })
        return results

    # -----------------------------------------------------------------------
    # Auth log parsing
    # -----------------------------------------------------------------------

    def _poll_auth_log(self) -> None:
        paths = (
            _AUTH_LOG_PATHS_LINUX if sys.platform.startswith("linux")
            else _AUTH_LOG_PATHS_MACOS
        )
        for p in paths:
            fp = Path(p)
            if not fp.exists():
                continue
            try:
                self._parse_auth_file(fp)
            except PermissionError:
                pass
            except Exception:
                log.exception("Error reading %s", p)
            break

    def _parse_auth_file(self, path: Path) -> None:
        key = str(path)
        try:
            size = path.stat().st_size
        except OSError:
            return
        offset = self._log_offsets.get(key, 0)
        if size < offset:
            offset = 0
        if size == offset:
            return
        with open(path, "r", errors="replace") as f:
            f.seek(offset)
            for line in f:
                self._process_auth_line(line.rstrip())
            self._log_offsets[key] = f.tell()

    def _process_auth_line(self, line: str) -> None:
        m = _RE_ACCEPTED_KEY.search(line)
        if m:
            user, ip, port = m.group(1), m.group(2), m.group(3)
            fingerprint = m.group(5) or ""
            self._record_auth_event(
                ip, user, "key", "success", fingerprint,
            )
            return

        m = _RE_ACCEPTED_PW.search(line)
        if m:
            user, ip, port = m.group(1), m.group(2), m.group(3)
            self._record_auth_event(ip, user, "password", "success", "")
            return

        m = _RE_ACCEPTED_KEYBOARD.search(line)
        if m:
            user, ip, port = m.group(1), m.group(2), m.group(3)
            self._record_auth_event(
                ip, user, "keyboard-interactive", "success", "",
            )
            return

        m = _RE_FAILED_PW.search(line)
        if m:
            user, ip, port = m.group(1), m.group(2), m.group(3)
            self._record_auth_event(ip, user, "password", "failure", "")
            return

    def _record_auth_event(
        self, ip: str, username: str, auth_method: str,
        auth_result: str, key_fingerprint: str,
    ) -> None:
        geo, asn = _geoip_lookup(ip)
        mac = _resolve_mac(ip)
        iface = _detect_interface(ip)

        sid = mdb.insert_session(
            timestamp_start=_now(),
            method="ssh",
            interface=iface,
            source_ip=ip,
            source_mac=mac,
            source_hostname="",
            username=username,
            auth_method=auth_method,
            auth_result=auth_result,
            session_type="",
            key_fingerprint=key_fingerprint,
            geo_ip=geo,
            asn=asn,
            process_name="sshd",
            process_pid=0,
        )

        event_name = "auth_success" if auth_result == "success" else "auth_failure"
        if self._sio:
            self._sio.emit(event_name, {
                "id": sid, "ip": ip, "username": username,
                "method": auth_method, "result": auth_result,
            })
