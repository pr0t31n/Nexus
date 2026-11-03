"""Nexus v2.0 — Fabric Manager: lifecycle, health, discovery, and dispatch for network fabrics."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from models import db as mdb
from services import credential_store

log = logging.getLogger("nexus.fabric_manager")


class FabricManager:

    def __init__(self, socketio=None):
        self._socketio = socketio
        self._health_thread: Optional[threading.Thread] = None
        self._running = False
        self._adapters: dict = {}
        self._adapter_classes: dict = {}

    # ------------------------------------------------------------------
    # Lifecycle: start / stop
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._load_adapters()
        self._health_thread = threading.Thread(
            target=self._health_loop, daemon=True, name="nexus-fabric-health",
        )
        self._health_thread.start()
        log.info("Fabric manager started (%d adapter types loaded)", len(self._adapter_classes))

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Adapter loading
    # ------------------------------------------------------------------

    def _load_adapters(self):
        try:
            from services.fabric_adapters.base import _REGISTRY
            self._adapter_classes = dict(_REGISTRY)
        except Exception:
            log.exception("Failed to load fabric adapters")
            self._adapter_classes = {}

    def _get_adapter(self, fabric_type: str):
        if fabric_type not in self._adapters:
            cls = self._adapter_classes.get(fabric_type)
            if cls is None:
                try:
                    from services.fabric_adapters.base import _REGISTRY
                    cls = _REGISTRY.get(fabric_type)
                except Exception:
                    pass
            if cls is not None:
                self._adapters[fabric_type] = cls()
        return self._adapters.get(fabric_type)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_fabric(self, name: str, fabric_type: str, config: dict,
                      subnets: list, priority: int = 100,
                      probe_interval: int | None = None) -> dict:
        adapter = self._get_adapter(fabric_type)
        pi = probe_interval
        if pi is None and adapter is not None:
            pi = adapter.get_probe_interval()
        if pi is None:
            pi = 300

        enc_config = self._encrypt_config(fabric_type, config)

        fid = mdb.create_fabric(
            name=name,
            fabric_type=fabric_type,
            subnets=json.dumps(subnets),
            config=json.dumps(enc_config),
            priority=priority,
            probe_interval=pi,
        )
        fab = mdb.get_fabric(fid)
        log.info("Created fabric %s (%s) id=%d", name, fabric_type, fid)
        return fab

    def update_fabric(self, fabric_id: int, **kwargs) -> dict | None:
        fab = mdb.get_fabric(fabric_id)
        if not fab:
            return None

        if "config" in kwargs:
            kwargs["config"] = json.dumps(
                self._encrypt_config(fab["fabric_type"], kwargs["config"])
            )
        if "subnets" in kwargs and isinstance(kwargs["subnets"], list):
            kwargs["subnets"] = json.dumps(kwargs["subnets"])

        conn = mdb.get_conn()
        now = datetime.now(timezone.utc).isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        if sets:
            vals = list(kwargs.values()) + [now, fabric_id]
            conn.execute(
                f"UPDATE network_fabrics SET {sets}, updated_at = ? WHERE id = ?", vals
            )
            conn.commit()
        return mdb.get_fabric(fabric_id)

    def delete_fabric(self, fabric_id: int) -> bool:
        conn = mdb.get_conn()
        conn.execute("DELETE FROM node_network_addresses WHERE fabric_id = ?", (fabric_id,))
        conn.execute("DELETE FROM network_fabrics WHERE id = ?", (fabric_id,))
        conn.commit()
        self._adapters.pop(str(fabric_id), None)
        log.info("Deleted fabric id=%d", fabric_id)
        return True

    def toggle_fabric(self, fabric_id: int, enabled: bool) -> dict | None:
        return self.update_fabric(fabric_id, enabled=1 if enabled else 0)

    # ------------------------------------------------------------------
    # Config encryption helpers
    # ------------------------------------------------------------------

    def _encrypt_config(self, fabric_type: str, config: dict) -> dict:
        adapter = self._get_adapter(fabric_type)
        secret_fields = set()
        if adapter is not None:
            for field in getattr(adapter, "FIELDS", []):
                if field.get("secret"):
                    secret_fields.add(field["name"])
        enc = {}
        for k, v in config.items():
            if k in secret_fields and v and not self._looks_encrypted(v):
                enc[k] = credential_store.encrypt(str(v))
            else:
                enc[k] = v
        return enc

    def _decrypt_config(self, fabric_type: str, config_json: str) -> dict:
        try:
            config = json.loads(config_json) if isinstance(config_json, str) else config_json
        except (json.JSONDecodeError, TypeError):
            return {}
        adapter = self._get_adapter(fabric_type)
        secret_fields = set()
        if adapter is not None:
            for field in getattr(adapter, "FIELDS", []):
                if field.get("secret"):
                    secret_fields.add(field["name"])
        dec = {}
        for k, v in config.items():
            if k in secret_fields and v and self._looks_encrypted(v):
                try:
                    dec[k] = credential_store.decrypt(str(v))
                except Exception:
                    dec[k] = v
            else:
                dec[k] = v
        return dec

    @staticmethod
    def _looks_encrypted(val) -> bool:
        if not isinstance(val, str):
            return False
        return len(val) > 60 and val.startswith("gAAAAA")

    def get_decrypted_config(self, fabric: dict) -> dict:
        return self._decrypt_config(fabric["fabric_type"], fabric.get("config", "{}"))

    # ------------------------------------------------------------------
    # Test connection (without saving)
    # ------------------------------------------------------------------

    def test_fabric_connection(self, fabric_type: str, config: dict) -> dict:
        adapter = self._get_adapter(fabric_type)
        if adapter is None:
            return {"ok": False, "checks": [{"label": "Adapter", "passed": False,
                                              "detail": f"No adapter for type '{fabric_type}'"}]}
        try:
            result = adapter.test_connection(config)
            return result
        except Exception as exc:
            return {"ok": False, "checks": [{"label": "Connection test", "passed": False,
                                              "detail": str(exc)}]}

    # ------------------------------------------------------------------
    # Health checking
    # ------------------------------------------------------------------

    def check_fabric_health(self, fabric_id: int) -> dict | None:
        fab = mdb.get_fabric(fabric_id)
        if not fab or not fab.get("enabled"):
            return None

        adapter = self._get_adapter(fab["fabric_type"])
        if adapter is None:
            status = "unknown"
            detail = {"error": "no adapter"}
        else:
            config = self._decrypt_config(fab["fabric_type"], fab.get("config", "{}"))
            try:
                result = adapter.check_health(config)
                status = result.get("status", "unknown")
                detail = result.get("detail", {})
            except Exception as exc:
                status = "down"
                detail = {"error": str(exc)}

        prev_status = fab.get("status", "unknown")
        now = datetime.now(timezone.utc).isoformat()
        conn = mdb.get_conn()
        conn.execute(
            "UPDATE network_fabrics SET status = ?, health_detail = ?, last_health = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(detail), now, now, fabric_id),
        )
        conn.commit()

        if status != prev_status and self._socketio:
            self._socketio.emit("fabric_health", {
                "fabric_id": fabric_id,
                "name": fab["name"],
                "status": status,
                "detail": detail,
                "prev_status": prev_status,
            })
            log.info("Fabric '%s' health: %s → %s", fab["name"], prev_status, status)

        return {"fabric_id": fabric_id, "status": status, "detail": detail}

    def _health_loop(self):
        last_checked: dict[int, float] = {}
        while self._running:
            try:
                fabrics = mdb.get_fabrics(enabled_only=True)
                now = time.monotonic()
                for fab in fabrics:
                    fid = fab["id"]
                    interval = fab.get("probe_interval", 300)
                    elapsed = now - last_checked.get(fid, 0)
                    if elapsed >= interval:
                        self.check_fabric_health(fid)
                        last_checked[fid] = time.monotonic()
            except Exception:
                log.exception("Fabric health loop error")
            time.sleep(15)

    # ------------------------------------------------------------------
    # Node discovery via adapters
    # ------------------------------------------------------------------

    def discover_fabric_nodes(self, fabric_id: int) -> list[int]:
        fab = mdb.get_fabric(fabric_id)
        if not fab or not fab.get("enabled"):
            return []
        adapter = self._get_adapter(fab["fabric_type"])
        if adapter is None:
            return []

        config = self._decrypt_config(fab["fabric_type"], fab.get("config", "{}"))
        try:
            discovered = adapter.discover_nodes(config)
        except Exception:
            log.exception("Adapter discover_nodes failed for fabric %d (%s)",
                          fabric_id, fab["name"])
            return []

        node_ids = []
        for item in discovered:
            try:
                nid = self._resolve_node(item, fabric_id)
                node_ids.append(nid)
            except Exception:
                log.exception("Node resolution failed for %s on fabric %d", item, fabric_id)
        return node_ids

    def _resolve_node(self, discovered: dict, fabric_id: int) -> int:
        address = discovered.get("address", "")
        hostname = discovered.get("hostname", "") or address
        tags = discovered.get("tags", {})
        metadata = discovered.get("metadata", {})

        conn = mdb.get_conn()
        # 1. Match by IP+fabric in node_network_addresses
        row = conn.execute(
            "SELECT na.node_id FROM node_network_addresses na "
            "WHERE na.fabric_id = ? AND na.address = ?",
            (fabric_id, address),
        ).fetchone()
        if row:
            nid = row["node_id"]
            mdb.upsert_fleet_node(hostname, ip_primary=address, status="online",
                                  last_seen=datetime.now(timezone.utc).isoformat())
            mdb.upsert_node_address(nid, fabric_id, address, lane_status="up")
            return nid

        # 2. Match by IP across any fabric
        row = conn.execute(
            "SELECT na.node_id FROM node_network_addresses na "
            "WHERE na.address = ?",
            (address,),
        ).fetchone()
        if row:
            nid = row["node_id"]
            mdb.upsert_node_address(nid, fabric_id, address, lane_status="up")
            return nid

        # 3. Match by hostname
        if hostname and hostname != address:
            row = conn.execute(
                "SELECT id FROM fleet_nodes WHERE hostname = ?", (hostname,)
            ).fetchone()
            if row:
                nid = row["id"]
                mdb.upsert_fleet_node(hostname, ip_primary=address, status="online",
                                      last_seen=datetime.now(timezone.utc).isoformat())
                mdb.upsert_node_address(nid, fabric_id, address, lane_status="up")
                return nid

        # 4. Create new node
        kwargs = {"ip_primary": address, "status": "online"}
        if metadata.get("os_family"):
            kwargs["os_family"] = metadata["os_family"]
        if metadata.get("arch"):
            kwargs["arch"] = metadata["arch"]
        if metadata.get("device_type"):
            kwargs["device_type"] = metadata["device_type"]
        if metadata.get("role"):
            kwargs["role"] = metadata["role"]
        nid = mdb.upsert_fleet_node(hostname, **kwargs)
        mdb.upsert_node_address(nid, fabric_id, address, lane_status="up")
        return nid

    # ------------------------------------------------------------------
    # Dispatch resolution
    # ------------------------------------------------------------------

    def get_dispatch_path(self, node_id: int) -> list[dict]:
        addresses = mdb.get_node_addresses(node_id)
        paths = []
        for addr in addresses:
            fab = mdb.get_fabric(addr["fabric_id"])
            if not fab or not fab.get("enabled"):
                continue
            adapter = self._get_adapter(fab["fabric_type"])
            dispatch_modes = []
            if adapter is not None:
                try:
                    dispatch_modes = adapter.get_dispatch_modes()
                except Exception:
                    dispatch_modes = ["ssh"]
            paths.append({
                "fabric_id": fab["id"],
                "fabric_name": fab["name"],
                "fabric_type": fab["fabric_type"],
                "address": addr["address"],
                "lane_status": addr.get("lane_status", "unknown"),
                "dispatch_modes": dispatch_modes,
                "priority": fab.get("priority", 100),
                "fabric_status": fab.get("status", "unknown"),
            })
        paths.sort(key=lambda p: (
            0 if p["lane_status"] == "up" else 1,
            0 if p["fabric_status"] == "healthy" else 1,
            p["priority"],
        ))
        return paths

    def get_best_dispatch(self, node_id: int) -> dict | None:
        paths = self.get_dispatch_path(node_id)
        for p in paths:
            if p["lane_status"] == "up" and p["fabric_status"] in ("healthy", "unknown"):
                return p
        return paths[0] if paths else None

    # ------------------------------------------------------------------
    # Info / wizard support
    # ------------------------------------------------------------------

    def get_fabric_types(self) -> list[dict]:
        from config import FABRIC_TYPES
        result = []
        for category, type_ids in FABRIC_TYPES.items():
            for tid in type_ids:
                adapter = self._get_adapter(tid)
                entry = {
                    "type_id": tid,
                    "category": category,
                    "display_name": tid.replace("_", " ").title(),
                    "fields": [],
                    "dispatch_modes": ["ssh"],
                    "probe_interval": 300,
                }
                if adapter is not None:
                    entry["display_name"] = getattr(adapter, "DISPLAY_NAME", entry["display_name"])
                    entry["fields"] = getattr(adapter, "FIELDS", [])
                    try:
                        entry["dispatch_modes"] = adapter.get_dispatch_modes()
                    except Exception:
                        pass
                    try:
                        entry["probe_interval"] = adapter.get_probe_interval()
                    except Exception:
                        pass
                result.append(entry)
        return result

    def get_fabrics_with_health(self) -> list[dict]:
        fabrics = mdb.get_fabrics()
        for fab in fabrics:
            try:
                fab["health_detail"] = json.loads(fab.get("health_detail") or "{}")
            except (json.JSONDecodeError, TypeError):
                fab["health_detail"] = {}
            fab.pop("config", None)
        return fabrics
