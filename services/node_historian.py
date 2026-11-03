"""Nexus v2.5 — Node historian.

Watches fleet nodes for state changes and records them in node_history:
  - connect / disconnect events
  - external IP changes
  - internal network configuration changes
  - software version history
  - certificate lifecycle
  - fabric membership changes
  - OS / hardware drift
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from models import db as mdb

log = logging.getLogger("nexus.node_historian")


class NodeHistorian:
    """Compares fleet node snapshots between cycles and records diffs."""

    POLL_INTERVAL = 120  # seconds

    def __init__(self, socketio=None):
        self._sio = socketio
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._prev_snapshot: dict[int, dict] = {}
        self._prev_tools: dict[int, dict[str, str]] = {}
        self._prev_fabrics: dict[int, set[int]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="node-historian",
        )
        self._thread.start()
        log.info("Node historian started (interval=%ds)", self.POLL_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Node historian stopped")

    def _loop(self) -> None:
        time.sleep(10)
        self._snapshot()
        while not self._stop.wait(self.POLL_INTERVAL):
            try:
                self._snapshot()
            except Exception:
                log.exception("Node historian cycle error")

    def _snapshot(self) -> None:
        nodes = mdb.get_fleet_nodes()
        current: dict[int, dict] = {}
        for n in nodes:
            nid = n["id"]
            current[nid] = n

            prev = self._prev_snapshot.get(nid)
            if prev is None:
                self._prev_snapshot[nid] = n
                continue

            self._check_status_change(nid, prev, n)
            self._check_ip_change(nid, prev, n)
            self._check_os_drift(nid, prev, n)

        self._check_tool_versions(current)
        self._check_fabric_membership(current)
        self._prev_snapshot = current

    def _check_status_change(self, nid: int, prev: dict, cur: dict) -> None:
        old_status = prev.get("status", "")
        new_status = cur.get("status", "")
        if old_status == new_status:
            return
        hostname = cur.get("hostname", str(nid))
        if new_status == "online" and old_status != "online":
            mdb.insert_node_event(
                str(nid), "connect",
                old_value=old_status, new_value=new_status,
                details=f"{hostname} came online",
            )
            if self._sio:
                self._sio.emit("node_event", {
                    "node_id": nid, "type": "connect", "hostname": hostname,
                })
        elif new_status == "offline" and old_status == "online":
            mdb.insert_node_event(
                str(nid), "disconnect",
                old_value=old_status, new_value=new_status,
                details=f"{hostname} went offline",
            )
            if self._sio:
                self._sio.emit("node_event", {
                    "node_id": nid, "type": "disconnect", "hostname": hostname,
                })

    def _check_ip_change(self, nid: int, prev: dict, cur: dict) -> None:
        old_ip = prev.get("ip_primary", "")
        new_ip = cur.get("ip_primary", "")
        if old_ip == new_ip or not old_ip or not new_ip:
            return
        mdb.insert_node_event(
            str(nid), "ip_change",
            old_value=old_ip, new_value=new_ip,
            details=f"Primary IP changed on {cur.get('hostname', nid)}",
        )

    def _check_os_drift(self, nid: int, prev: dict, cur: dict) -> None:
        for field, event_type in [
            ("os_family", "os_drift"),
            ("arch", "os_drift"),
        ]:
            old_val = prev.get(field, "")
            new_val = cur.get(field, "")
            if old_val != new_val and old_val and new_val:
                mdb.insert_node_event(
                    str(nid), event_type,
                    old_value=f"{field}={old_val}",
                    new_value=f"{field}={new_val}",
                    details=f"{field} changed on {cur.get('hostname', nid)}",
                )

    def _check_tool_versions(self, nodes: dict[int, dict]) -> None:
        current_tools: dict[int, dict[str, str]] = {}
        for nid in nodes:
            tools = mdb.get_tool_instances(nid)
            versions = {}
            for t in tools:
                versions[t["tool_name"]] = t.get("version", "")
            current_tools[nid] = versions

            prev_versions = self._prev_tools.get(nid, {})
            if not prev_versions:
                continue

            all_names = set(prev_versions.keys()) | set(versions.keys())
            for name in all_names:
                old_ver = prev_versions.get(name, "")
                new_ver = versions.get(name, "")
                if old_ver and new_ver and old_ver != new_ver:
                    mdb.insert_node_event(
                        str(nid), "version_change",
                        old_value=f"{name}={old_ver}",
                        new_value=f"{name}={new_ver}",
                        details=f"{name} version changed on {nodes[nid].get('hostname', nid)}",
                    )
        self._prev_tools = current_tools

    def _check_fabric_membership(self, nodes: dict[int, dict]) -> None:
        conn = mdb.get_conn()
        current_fabrics: dict[int, set[int]] = {}
        for nid in nodes:
            rows = conn.execute(
                "SELECT DISTINCT fabric_id FROM node_network_addresses WHERE node_id = ?",
                (nid,),
            ).fetchall()
            fids = {r["fabric_id"] for r in rows}
            current_fabrics[nid] = fids

            prev = self._prev_fabrics.get(nid, set())
            if not prev and fids:
                continue

            added = fids - prev
            removed = prev - fids
            hostname = nodes[nid].get("hostname", str(nid))
            for fid in added:
                fab = mdb.get_fabric(fid)
                fname = fab["name"] if fab else str(fid)
                mdb.insert_node_event(
                    str(nid), "fabric_change",
                    old_value="", new_value=fname,
                    details=f"{hostname} joined fabric {fname}",
                )
            for fid in removed:
                fab = mdb.get_fabric(fid)
                fname = fab["name"] if fab else str(fid)
                mdb.insert_node_event(
                    str(nid), "fabric_change",
                    old_value=fname, new_value="",
                    details=f"{hostname} left fabric {fname}",
                )
        self._prev_fabrics = current_fabrics

    def record_cert_event(self, node_id: int | str, event: str,
                          old_value: str = "", new_value: str = "") -> None:
        mdb.insert_node_event(
            str(node_id), "cert_lifecycle",
            old_value=old_value, new_value=new_value,
            details=event,
        )
