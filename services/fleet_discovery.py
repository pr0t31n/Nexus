"""Nexus v2.0 — Three-tier fleet discovery engine.

Replaces the Phase 1 _do_scan() with fabric-aware discovery:
  Tier 1: Active TCP port scan across fabric subnets
  Tier 2: Push-based agent checkin (POST /api/fleet/checkin)
  Tier 3: Adapter-driven discovery (per fabric type)
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from config import TOOL_REGISTRY
from models import db as mdb

log = logging.getLogger("nexus.fleet_discovery")


class FleetDiscovery:

    def __init__(self, fabric_manager, socketio=None):
        self._fm = fabric_manager
        self._socketio = socketio
        self._scan_running = False
        self._last_scan: Optional[datetime] = None
        self._lock = threading.Lock()
        self._bg_thread: Optional[threading.Thread] = None
        self._running = False
        self._miss_count: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._bg_thread = threading.Thread(
            target=self._background_loop, daemon=True, name="nexus-fleet-discovery",
        )
        self._bg_thread.start()
        log.info("Fleet discovery started")

    def stop(self):
        self._running = False

    @property
    def is_scanning(self) -> bool:
        return self._scan_running

    @property
    def last_scan(self) -> Optional[datetime]:
        return self._last_scan

    # ------------------------------------------------------------------
    # Trigger
    # ------------------------------------------------------------------

    def trigger_scan(self):
        if self._scan_running:
            return
        threading.Thread(target=self.full_scan, daemon=True, name="nexus-scan-trigger").start()

    # ------------------------------------------------------------------
    # Full discovery cycle
    # ------------------------------------------------------------------

    def full_scan(self):
        if self._scan_running:
            return
        self._scan_running = True
        try:
            seen_tool_ids: set[tuple[int, str, int]] = set()

            self._scan_local(seen_tool_ids)

            fabrics = mdb.get_fabrics(enabled_only=True)
            for fab in fabrics:
                try:
                    self._scan_fabric_subnets(fab, seen_tool_ids)
                except Exception:
                    log.exception("Subnet scan failed for fabric %d (%s)", fab["id"], fab["name"])

            for fab in fabrics:
                try:
                    self._fm.discover_fabric_nodes(fab["id"])
                except Exception:
                    log.exception("Adapter discovery failed for fabric %d (%s)", fab["id"], fab["name"])

            self._mark_offline(seen_tool_ids)

            with self._lock:
                self._last_scan = datetime.now(timezone.utc)

            if self._socketio:
                self._socketio.emit("fleet_update", self.fleet_payload())

        except Exception:
            log.exception("Full scan failed")
        finally:
            self._scan_running = False

    # ------------------------------------------------------------------
    # Tier 1: Local node self-discovery
    # ------------------------------------------------------------------

    def _scan_local(self, seen: set):
        local_hostname = socket.gethostname().split(".")[0]
        local_ips = set(self._get_local_ips())
        ips = sorted(local_ips, key=lambda x: (x == "127.0.0.1", x))
        scan_timeout = float(mdb.get_setting("scan_timeout_s", "0.4"))

        node_id = mdb.upsert_fleet_node(
            local_hostname,
            ip_primary=ips[0] if ips else "127.0.0.1",
            status="online",
            os_family=sys.platform,
            last_seen=datetime.now(timezone.utc).isoformat(),
        )

        for svc in TOOL_REGISTRY:
            if svc["id"] == "nexus":
                continue
            for ip in ips:
                if self._tcp_open(ip, svc["port"], scan_timeout):
                    extra = {}
                    if svc["status_api"]:
                        info = self._probe_tool_status(ip, svc["port"], svc["proto"])
                        if info:
                            extra["version"] = info.get("version", "")
                            extra["scanning"] = 1 if info.get("scanning") else 0
                    tid = mdb.upsert_tool_instance(
                        node_id, svc["id"], svc["port"],
                        proto=svc["proto"], status="online", **extra,
                    )
                    seen.add((node_id, svc["id"], svc["port"]))
                    break

    # ------------------------------------------------------------------
    # Tier 1: Fabric subnet scanning
    # ------------------------------------------------------------------

    def _scan_fabric_subnets(self, fabric: dict, seen: set):
        subnets_raw = fabric.get("subnets", "[]")
        try:
            subnets = json.loads(subnets_raw) if isinstance(subnets_raw, str) else subnets_raw
        except (json.JSONDecodeError, TypeError):
            subnets = []

        if not subnets:
            return

        scan_timeout = float(mdb.get_setting("scan_timeout_s", "0.4"))
        workers = int(mdb.get_setting("scan_workers", "32"))
        local_ips = set(self._get_local_ips())
        local_hostname = socket.gethostname().split(".")[0]
        fab_id = fabric["id"]

        tool_ports = [(svc["id"], svc["port"], svc["proto"], svc.get("status_api", False))
                      for svc in TOOL_REGISTRY if svc["id"] != "nexus"]

        for subnet_str in subnets:
            try:
                net = ipaddress.ip_network(subnet_str, strict=False)
            except ValueError:
                continue

            hosts = list(net.hosts())
            if not hosts:
                continue

            tasks = []
            for host in hosts:
                ip = str(host)
                for tool_id, port, proto, has_api in tool_ports:
                    tasks.append((ip, tool_id, port, proto, has_api))

            hits: list[tuple[str, str, int, str, bool]] = []
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(self._tcp_open, ip, port, scan_timeout): (ip, tool_id, port, proto, has_api)
                    for ip, tool_id, port, proto, has_api in tasks
                }
                for fut in as_completed(futs):
                    try:
                        if fut.result():
                            hits.append(futs[fut])
                    except Exception:
                        pass

            for ip, tool_id, port, proto, has_api in hits:
                hostname = local_hostname if ip in local_ips else self._resolve_hostname(ip)
                nid = mdb.upsert_fleet_node(
                    hostname, ip_primary=ip, status="online",
                    last_seen=datetime.now(timezone.utc).isoformat(),
                )
                mdb.upsert_node_address(nid, fab_id, ip, lane_status="up")
                extra = {}
                if has_api:
                    info = self._probe_tool_status(ip, port, proto)
                    if info:
                        extra["version"] = info.get("version", "")
                        extra["scanning"] = 1 if info.get("scanning") else 0
                mdb.upsert_tool_instance(
                    nid, tool_id, port,
                    proto=proto, status="online", **extra,
                )
                seen.add((nid, tool_id, port))

    # ------------------------------------------------------------------
    # Tier 2: Checkin handler (called from route, not from scan)
    # ------------------------------------------------------------------

    def handle_checkin(self, data: dict) -> dict:
        ip = data.get("ip", "")
        hostname = data.get("hostname", ip)
        tools = data.get("tools", [])
        os_family = data.get("os_family", "")
        arch = data.get("arch", "")
        agent_version = data.get("agent_version", "")

        kwargs = {"ip_primary": ip, "status": "online",
                  "last_seen": datetime.now(timezone.utc).isoformat()}
        if os_family:
            kwargs["os_family"] = os_family
        if arch:
            kwargs["arch"] = arch
        if agent_version:
            kwargs["agent_version"] = agent_version

        nid = mdb.upsert_fleet_node(hostname, **kwargs)

        fabrics = mdb.get_fabrics(enabled_only=True)
        for fab in fabrics:
            try:
                fab_subnets = json.loads(fab.get("subnets", "[]"))
            except (json.JSONDecodeError, TypeError):
                fab_subnets = []
            for subnet_str in fab_subnets:
                try:
                    net = ipaddress.ip_network(subnet_str, strict=False)
                    if ipaddress.ip_address(ip) in net:
                        mdb.upsert_node_address(nid, fab["id"], ip, lane_status="up")
                        break
                except ValueError:
                    continue

        for t in tools:
            mdb.upsert_tool_instance(
                nid, t.get("tool_name", ""), t.get("port", 0),
                proto=t.get("proto", "https"),
                version=t.get("version", ""),
                status="online",
                scanning=1 if t.get("scanning") else 0,
            )

        interval = int(mdb.get_setting("discovery_interval_s", "300"))

        if self._socketio:
            node = mdb.get_fleet_node(nid)
            if node:
                node["tools"] = mdb.get_tool_instances(nid)
                node["addresses"] = mdb.get_node_addresses(nid)
                self._socketio.emit("fleet_update", {
                    "nodes": [node],
                    "stats": mdb.fleet_stats(),
                    "last_scan": self._last_scan.isoformat() if self._last_scan else None,
                    "delta": True,
                })

        return {"ok": True, "node_id": nid, "next_checkin": interval}

    # ------------------------------------------------------------------
    # Offline detection
    # ------------------------------------------------------------------

    def _mark_offline(self, seen_tool_ids: set):
        threshold = int(mdb.get_setting("offline_threshold_cycles", "3"))
        conn = mdb.get_conn()
        all_tools = conn.execute("SELECT id, node_id, tool_name, port FROM tool_instances").fetchall()
        now = datetime.now(timezone.utc).isoformat()

        for t in all_tools:
            key = (t["node_id"], t["tool_name"], t["port"])
            tid = t["id"]
            if key in seen_tool_ids:
                self._miss_count.pop(tid, None)
            else:
                self._miss_count[tid] = self._miss_count.get(tid, 0) + 1
                if self._miss_count[tid] >= threshold:
                    conn.execute(
                        "UPDATE tool_instances SET status = 'offline', last_seen = ? WHERE id = ?",
                        (now, tid),
                    )

        conn.commit()

        nodes = conn.execute("SELECT id FROM fleet_nodes").fetchall()
        for n in nodes:
            nid = n["id"]
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM tool_instances WHERE node_id = ? AND status = 'online'",
                (nid,),
            ).fetchone()
            if row["c"] == 0:
                total = conn.execute(
                    "SELECT COUNT(*) AS c FROM tool_instances WHERE node_id = ?", (nid,),
                ).fetchone()["c"]
                if total > 0:
                    conn.execute(
                        "UPDATE fleet_nodes SET status = 'offline', updated_at = ? WHERE id = ? AND status != 'offline'",
                        (now, nid),
                    )
        conn.commit()

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _background_loop(self):
        while self._running:
            try:
                self.full_scan()
            except Exception:
                log.exception("Background scan cycle failed")
            interval = int(mdb.get_setting("discovery_interval_s", "300"))
            deadline = time.monotonic() + interval
            while self._running and time.monotonic() < deadline:
                time.sleep(5)

    # ------------------------------------------------------------------
    # Fleet payload builder
    # ------------------------------------------------------------------

    def fleet_payload(self) -> dict:
        nodes = mdb.get_fleet_nodes()
        for n in nodes:
            n["tools"] = mdb.get_tool_instances(n["id"])
            n["addresses"] = mdb.get_node_addresses(n["id"])
        fabrics = self._fm.get_fabrics_with_health()
        return {
            "nodes": nodes,
            "stats": mdb.fleet_stats(),
            "last_scan": self._last_scan.isoformat() if self._last_scan else None,
            "fabrics": fabrics,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tcp_open(ip: str, port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _probe_tool_status(ip: str, port: int, proto: str, timeout: float = 1.5) -> Optional[dict]:
        try:
            url = f"{proto}://{ip}:{port}/api/nexus-status"
            r = requests.get(url, timeout=timeout, verify=False)
            if r.ok:
                return r.json()
        except Exception:
            pass
        return None

    @staticmethod
    def _get_local_ips() -> list[str]:
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
                        ip_addr = parts[idx]
                        if not ip_addr.startswith("127."):
                            ips.add(ip_addr)
        except Exception:
            pass
        return sorted(ips)

    @staticmethod
    def _resolve_hostname(ip: str) -> str:
        try:
            hostname, _, _ = socket.gethostbyaddr(ip)
            return hostname.split(".")[0]
        except (socket.herror, socket.gaierror, OSError):
            return ip
