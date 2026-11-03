"""Nexus v2.3 — Version Tracker: fleet-wide tool version monitoring."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

from models import db as mdb
from config import TOOL_REGISTRY

log = logging.getLogger("nexus.version_tracker")

_POLL_INTERVAL = 300
_GITHUB_RAW = "https://raw.githubusercontent.com/pr0t31n/{repo}/main/VERSION"
_FETCH_TIMEOUT = 10

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

_REPO_MAP: dict[str, str] = {
    "crucible":     "Crucible",
    "vantage":      "Vantage",
    "spectre":      "Spectre",
    "gauntlet":     "Gauntlet",
    "quiver":       "Quiver",
    "nexus":        "Nexus",
    "scattershot3": "Scattershot3",
    "osift":        "OSift",
    "fracture":     "Fracture",
    "siphon":       "Siphon",
    "gearbox":      "Gearbox",
    "bastion":      "Bastion",
}


def _parse_semver(ver: str) -> tuple[int, ...] | None:
    m = _SEMVER_RE.match(ver.strip())
    if m:
        return tuple(int(g) for g in m.groups())
    return None


def _compare_versions(local: str, remote: str) -> str:
    lv = _parse_semver(local)
    rv = _parse_semver(remote)
    if not lv or not rv:
        return "unknown"
    if lv < rv:
        return "behind"
    if lv > rv:
        return "ahead"
    return "current"


class VersionTracker:

    def __init__(self, socketio):
        self._socketio = socketio
        self._lock = threading.Lock()
        self._matrix: dict[str, dict] = {}
        self._latest: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_refresh = ""

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="nexus-version-tracker")
        self._thread.start()
        log.info("Version tracker started")

    def stop(self):
        self._running = False
        log.info("Version tracker stopped")

    def _poll_loop(self):
        while self._running:
            try:
                self.refresh()
            except Exception:
                log.exception("Version poll failed")
            for _ in range(int(_POLL_INTERVAL / 5)):
                if not self._running:
                    return
                time.sleep(5)

    def refresh(self):
        self._fetch_latest_versions()
        self._build_matrix()
        self._last_refresh = datetime.now(timezone.utc).isoformat()
        self._push_matrix()

    def _fetch_latest_versions(self):
        if requests is None:
            return
        latest = {}
        for tool_id, repo in _REPO_MAP.items():
            url = _GITHUB_RAW.format(repo=repo)
            try:
                r = requests.get(url, timeout=_FETCH_TIMEOUT)
                if r.status_code == 200:
                    ver = r.text.strip().splitlines()[0].strip()
                    if _parse_semver(ver):
                        latest[tool_id] = ver
            except Exception:
                pass
        with self._lock:
            self._latest = latest

    def _build_matrix(self):
        instances = mdb.get_tool_instances()
        nodes_map: dict[int, dict] = {}
        for inst in instances:
            nid = inst["node_id"]
            if nid not in nodes_map:
                nodes_map[nid] = {}
            nodes_map[nid][inst["tool_name"]] = {
                "version": inst["version"] or "unknown",
                "status": inst["status"],
                "port": inst["port"],
            }

        nodes = mdb.get_fleet_nodes()
        node_lookup = {n["id"]: n for n in nodes}

        matrix = {}
        for nid, tools in nodes_map.items():
            node = node_lookup.get(nid, {})
            hostname = node.get("hostname", f"node-{nid}")
            node_entry = {
                "node_id": nid,
                "hostname": hostname,
                "status": node.get("status", "unknown"),
                "tools": {},
            }
            for tool_id, info in tools.items():
                local_ver = info["version"]
                latest_ver = self._latest.get(tool_id, "")
                comparison = _compare_versions(local_ver, latest_ver) if latest_ver else "unknown"
                node_entry["tools"][tool_id] = {
                    "local_version": local_ver,
                    "latest_version": latest_ver,
                    "status": comparison,
                    "tool_status": info["status"],
                    "port": info["port"],
                }
            matrix[hostname] = node_entry

        with self._lock:
            self._matrix = matrix

    def _push_matrix(self):
        payload = self.get_matrix()
        self._socketio.emit("version_matrix", payload)

    def get_matrix(self) -> dict:
        with self._lock:
            return {
                "nodes": dict(self._matrix),
                "latest": dict(self._latest),
                "last_refresh": self._last_refresh,
                "registry": [t["id"] for t in TOOL_REGISTRY],
            }

    def get_summary(self) -> dict:
        with self._lock:
            total = 0
            current = 0
            behind = 0
            ahead = 0
            unknown = 0
            for node in self._matrix.values():
                for tool_info in node["tools"].values():
                    total += 1
                    s = tool_info["status"]
                    if s == "current":
                        current += 1
                    elif s == "behind":
                        behind += 1
                    elif s == "ahead":
                        ahead += 1
                    else:
                        unknown += 1
            return {
                "total_instances": total,
                "current": current,
                "behind": behind,
                "ahead": ahead,
                "unknown": unknown,
                "last_refresh": self._last_refresh,
            }
