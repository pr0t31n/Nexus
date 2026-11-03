"""Nexus v2.5 — Port and service monitor.

Background thread that tracks open listening ports/services on the Nexus
server.  Detects opens/closes, enriches with process owner, binary hash,
Arsenal tool matching, and firewall rule status.  Supports whitelisting
persistent services.
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

log = logging.getLogger("nexus.port_monitor")

_MAX_HASH_SIZE = 100 * 1024 * 1024  # 100 MiB


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
# Binary hashing
# ---------------------------------------------------------------------------

def _hash_binary(path: str) -> str:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size > _MAX_HASH_SIZE:
            return ""
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Process info helpers
# ---------------------------------------------------------------------------

def _get_process_user(pid: int) -> str:
    if pid <= 0:
        return ""
    out = _run(["ps", "-o", "user=", "-p", str(pid)])
    return out.strip()


def _get_process_binary_linux(pid: int) -> str:
    try:
        return str(Path(f"/proc/{pid}/exe").resolve())
    except Exception:
        return ""


def _get_process_binary_macos(pid: int) -> str:
    if pid <= 0:
        return ""
    out = _run(["ps", "-o", "comm=", "-p", str(pid)])
    binary = out.strip()
    if binary and not binary.startswith("/"):
        which_out = _run(["which", binary])
        resolved = which_out.strip()
        if resolved:
            return resolved
    return binary


def _get_process_binary(pid: int) -> str:
    if pid <= 0:
        return ""
    if sys.platform.startswith("linux"):
        return _get_process_binary_linux(pid)
    elif sys.platform == "darwin":
        return _get_process_binary_macos(pid)
    return ""


# ---------------------------------------------------------------------------
# Firewall checks
# ---------------------------------------------------------------------------

def _check_firewall_linux(port: int, proto: str) -> int | None:
    if shutil.which("nft"):
        out = _run(f"nft list ruleset 2>/dev/null | grep -i '{proto}.*dport.*{port}'")
        if out.strip():
            if "accept" in out.lower():
                return 1
            if "drop" in out.lower() or "reject" in out.lower():
                return 0
    if shutil.which("iptables"):
        out = _run(f"iptables -L -n 2>/dev/null | grep -i '{proto}.*dpt:{port}'")
        if out.strip():
            if "ACCEPT" in out:
                return 1
            if "DROP" in out or "REJECT" in out:
                return 0
    return None


def _check_firewall_macos(port: int, proto: str) -> int | None:
    out = _run(f"pfctl -sr 2>/dev/null | grep '{port}'")
    if out.strip():
        if "pass" in out.lower():
            return 1
        if "block" in out.lower():
            return 0
    return None


def _check_firewall(port: int, proto: str = "tcp") -> int | None:
    if sys.platform.startswith("linux"):
        return _check_firewall_linux(port, proto)
    elif sys.platform == "darwin":
        return _check_firewall_macos(port, proto)
    return None


# ---------------------------------------------------------------------------
# Port scanning
# ---------------------------------------------------------------------------

def _scan_ports_linux() -> list[dict]:
    results = []
    for proto, flag in [("tcp", "-tlnpH"), ("udp", "-ulnpH")]:
        out = _run(["ss", flag])
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[3] if proto == "tcp" else parts[3]
            proc_info = " ".join(parts[5:]) if len(parts) > 5 else ""

            if "]:" in local:
                m = re.match(r"\[([^\]]+)\]:(\d+)", local)
                if not m:
                    continue
                bind_addr, port_s = m.group(1), m.group(2)
            else:
                bind_addr, _, port_s = local.rpartition(":")
                if not port_s:
                    continue

            if bind_addr in ("127.0.0.1", "::1", "[::1]"):
                continue

            try:
                port = int(port_s)
            except ValueError:
                continue

            pid = 0
            pname = ""
            pm = re.search(r'pid=(\d+)', proc_info)
            if pm:
                pid = int(pm.group(1))
            pm = re.search(r'"([^"]+)"', proc_info)
            if pm:
                pname = pm.group(1)

            results.append({
                "port": port,
                "protocol": proto,
                "bind_address": bind_addr if bind_addr != "*" else "0.0.0.0",
                "process_name": pname,
                "pid": pid,
            })
    return results


def _scan_ports_macos() -> list[dict]:
    results = []
    out = _run("lsof -i -P -n -sTCP:LISTEN 2>/dev/null")
    seen: set[tuple[int, str]] = set()
    for line in out.splitlines():
        if line.startswith("COMMAND"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        pname = parts[0]
        try:
            pid = int(parts[1])
        except ValueError:
            pid = 0
        name_col = parts[8]

        m = re.search(r"([\d.*]+):(\d+)", name_col)
        if not m:
            m = re.search(r"\[([0-9a-f:*]+)\]:(\d+)", name_col)
        if not m:
            continue

        bind_addr = m.group(1)
        try:
            port = int(m.group(2))
        except ValueError:
            continue

        if bind_addr in ("127.0.0.1", "::1", "[::1]"):
            continue

        key = (port, bind_addr)
        if key in seen:
            continue
        seen.add(key)

        results.append({
            "port": port,
            "protocol": "tcp",
            "bind_address": bind_addr if bind_addr != "*" else "0.0.0.0",
            "process_name": pname,
            "pid": pid,
        })

    out_udp = _run("lsof -i UDP -P -n 2>/dev/null")
    for line in out_udp.splitlines():
        if line.startswith("COMMAND"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        pname = parts[0]
        try:
            pid = int(parts[1])
        except ValueError:
            pid = 0
        name_col = parts[8]
        m = re.search(r"([\d.*]+):(\d+)", name_col)
        if not m:
            continue
        bind_addr = m.group(1)
        try:
            port = int(m.group(2))
        except ValueError:
            continue
        if bind_addr in ("127.0.0.1", "::1"):
            continue
        key = (port, bind_addr)
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "port": port,
            "protocol": "udp",
            "bind_address": bind_addr if bind_addr != "*" else "0.0.0.0",
            "process_name": pname,
            "pid": pid,
        })

    return results


# ---------------------------------------------------------------------------
# PortMonitor
# ---------------------------------------------------------------------------

class PortMonitor:

    WHITELIST_GRACE_CYCLES = 3

    def __init__(self, socketio=None, tool_registry: list[dict] | None = None):
        self._sio = socketio
        self._registry = tool_registry or []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interval = 60
        self._prev_keys: dict[tuple[int, str, str], int] = {}
        self._miss_count: dict[tuple[int, str, str], int] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="port-monitor",
        )
        self._thread.start()
        log.info("Port monitor started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Port monitor stopped")

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        time.sleep(8)
        while not self._stop.wait(self._interval):
            try:
                current = self._scan_and_enrich()
                self._detect_changes(current)
            except Exception:
                log.exception("Error in port monitor cycle")

    # -----------------------------------------------------------------------
    # Scan + enrich
    # -----------------------------------------------------------------------

    def _scan_and_enrich(self) -> list[dict]:
        if sys.platform.startswith("linux"):
            raw = _scan_ports_linux()
        elif sys.platform == "darwin":
            raw = _scan_ports_macos()
        else:
            raw = []

        for entry in raw:
            pid = entry.get("pid", 0)
            entry["process_user"] = _get_process_user(pid)
            binary = _get_process_binary(pid)
            entry["process_binary"] = binary
            entry["binary_hash"] = _hash_binary(binary) if binary else ""
            entry["arsenal_tool_match"] = self._match_tool(entry["port"])
            entry["firewall_allowed"] = _check_firewall(
                entry["port"], entry["protocol"],
            )
        return raw

    def _match_tool(self, port: int) -> str:
        for tool in self._registry:
            if tool.get("port") == port:
                return tool.get("name", tool.get("id", ""))
        return ""

    # -----------------------------------------------------------------------
    # Change detection
    # -----------------------------------------------------------------------

    def _detect_changes(self, current: list[dict]) -> None:
        now = _now()
        current_keys: dict[tuple[int, str, str], dict] = {}

        for entry in current:
            key = (entry["port"], entry["protocol"], entry["bind_address"])
            current_keys[key] = entry

        for key, entry in current_keys.items():
            if key in self._prev_keys:
                db_id = self._prev_keys[key]
                self._miss_count.pop(key, None)
                mdb.update_service_port_seen(db_id, now)
                prev_hash = mdb.get_service_port_hash(db_id)
                if prev_hash and entry["binary_hash"] and prev_hash != entry["binary_hash"]:
                    mdb.update_service_port_hash(db_id, entry["binary_hash"])
                    if self._sio:
                        self._sio.emit("port_binary_changed", {
                            "id": db_id, "port": entry["port"],
                            "old_hash": prev_hash, "new_hash": entry["binary_hash"],
                        })
            else:
                db_id = mdb.upsert_service_port(
                    port=entry["port"],
                    protocol=entry["protocol"],
                    bind_address=entry["bind_address"],
                    service_name=entry.get("process_name", ""),
                    process_name=entry.get("process_name", ""),
                    process_user=entry.get("process_user", ""),
                    process_binary=entry.get("process_binary", ""),
                    binary_hash=entry.get("binary_hash", ""),
                    first_seen=now,
                    last_seen=now,
                    arsenal_tool_match=entry.get("arsenal_tool_match", ""),
                    firewall_allowed=entry.get("firewall_allowed"),
                )
                self._prev_keys[key] = db_id
                self._miss_count.pop(key, None)
                if self._sio:
                    self._sio.emit("port_opened", {
                        "id": db_id, "port": entry["port"],
                        "proto": entry["protocol"],
                        "bind": entry["bind_address"],
                        "service": entry.get("process_name", ""),
                        "tool_match": entry.get("arsenal_tool_match", ""),
                    })

        vanished = set(self._prev_keys.keys()) - set(current_keys.keys())
        for key in vanished:
            db_id = self._prev_keys[key]
            is_whitelisted = mdb.is_service_port_whitelisted(db_id)

            missed = self._miss_count.get(key, 0) + 1
            self._miss_count[key] = missed

            grace = self.WHITELIST_GRACE_CYCLES if is_whitelisted else 1
            if missed < grace:
                continue

            mdb.close_service_port(db_id, now)
            del self._prev_keys[key]
            self._miss_count.pop(key, None)

            if self._sio:
                self._sio.emit("port_closed", {
                    "id": db_id, "port": key[0],
                    "proto": key[1], "bind": key[2],
                })
