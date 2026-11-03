"""Nexus v2.2 — SSH Tunnel Manager: SOCKS5 proxy and local port forwarding."""
from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from datetime import datetime, timezone

try:
    from sshtunnel import SSHTunnelForwarder
    _HAS_SSHTUNNEL = True
except ImportError:
    SSHTunnelForwarder = None
    _HAS_SSHTUNNEL = False

try:
    import paramiko
except ImportError:
    paramiko = None

from models import db as mdb
from services import credential_store

log = logging.getLogger("nexus.ssh_tunnel")


def _find_free_port(start: int = 1080, end: int = 1180) -> int | None:
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None


class SSHTunnelManager:

    def __init__(self, fabric_manager):
        self._tunnels: dict[str, dict] = {}
        self._fm = fabric_manager
        self._lock = threading.Lock()
        self._running = False
        self._reaper: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._reaper = threading.Thread(
            target=self._reap_stale, daemon=True, name="nexus-tunnel-reaper",
        )
        self._reaper.start()
        log.info("SSH tunnel manager started")

    def stop(self):
        self._running = False
        with self._lock:
            for tid in list(self._tunnels):
                self._destroy_tunnel(tid)
        log.info("SSH tunnel manager stopped")

    # ------------------------------------------------------------------
    # Shared connection builder
    # ------------------------------------------------------------------

    def _build_ssh_params(self, node_id: int, credential_id: int) -> dict | None:
        node = mdb.get_fleet_node(node_id)
        if not node:
            return None
        cred = mdb.get_credential_raw(credential_id)
        if not cred:
            return None
        dispatch = self._fm.get_best_dispatch(node_id)
        if not dispatch:
            return None

        address = dispatch["address"]
        username = cred.get("username", "")
        cred_type = cred.get("cred_type", "password")
        try:
            secret = credential_store.decrypt(cred["encrypted_value"])
        except Exception:
            return None

        params = {
            "ssh_address_or_host": address,
            "ssh_port": 22,
            "ssh_username": username,
            "node": node,
            "dispatch": dispatch,
        }
        if cred_type == "ssh_key":
            import io
            try:
                pkey = paramiko.RSAKey.from_private_key(io.StringIO(secret))
            except Exception:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(secret))
                except Exception:
                    pkey = paramiko.ECDSAKey.from_private_key(io.StringIO(secret))
            params["ssh_pkey"] = pkey
        else:
            params["ssh_password"] = secret

        return params

    # ------------------------------------------------------------------
    # SOCKS5 proxy
    # ------------------------------------------------------------------

    def create_socks(self, node_id: int, credential_id: int,
                     local_port: int | None = None) -> dict:
        if not _HAS_SSHTUNNEL:
            return {"ok": False, "error": "sshtunnel package not installed"}

        params = self._build_ssh_params(node_id, credential_id)
        if not params:
            return {"ok": False, "error": "Cannot resolve SSH target or credential"}

        if local_port is None:
            local_port = _find_free_port(1080, 1180)
            if local_port is None:
                return {"ok": False, "error": "No free local ports in 1080-1179"}

        node = params.pop("node")
        dispatch = params.pop("dispatch")

        fwd_kwargs = {
            k: v for k, v in params.items()
            if k.startswith("ssh_")
        }

        try:
            forwarder = SSHTunnelForwarder(
                **fwd_kwargs,
                dynamic_forward=("127.0.0.1", local_port),
                set_keepalive=30,
            )
            forwarder.start()
        except Exception as exc:
            return {"ok": False, "error": f"SOCKS tunnel failed: {exc}"}

        tunnel_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        fabric_id = dispatch.get("fabric_id")
        fabric_name = dispatch.get("fabric_name", "direct")

        mdb.get_conn().execute(
            "INSERT INTO tunnels (node_id, fabric_id, tunnel_type, local_port, "
            "remote_host, remote_port, status, created_at) "
            "VALUES (?, ?, 'socks5', ?, '', 0, 'active', ?)",
            (node_id, fabric_id, local_port, now.isoformat()),
        )
        mdb.get_conn().commit()

        info = {
            "tunnel_id": tunnel_id,
            "node_id": node_id,
            "tunnel_type": "socks5",
            "local_port": local_port,
            "remote_host": "",
            "remote_port": 0,
            "fabric_id": fabric_id,
            "fabric_name": fabric_name,
            "hostname": node.get("hostname", ""),
            "address": dispatch["address"],
            "status": "active",
            "forwarder": forwarder,
            "created_at": now.isoformat(),
        }

        with self._lock:
            self._tunnels[tunnel_id] = info

        mdb.audit("tunnel_create",
                  f"SOCKS5 proxy on :{local_port} → {node.get('hostname', dispatch['address'])}",
                  username=mdb.get_setting("__last_user", "system"))

        return {
            "ok": True,
            "tunnel_id": tunnel_id,
            "tunnel_type": "socks5",
            "local_port": local_port,
            "hostname": node.get("hostname", ""),
            "address": dispatch["address"],
            "fabric_name": fabric_name,
        }

    # ------------------------------------------------------------------
    # Local port forward
    # ------------------------------------------------------------------

    def create_forward(self, node_id: int, credential_id: int,
                       local_port: int, remote_host: str, remote_port: int) -> dict:
        if not _HAS_SSHTUNNEL:
            return {"ok": False, "error": "sshtunnel package not installed"}

        params = self._build_ssh_params(node_id, credential_id)
        if not params:
            return {"ok": False, "error": "Cannot resolve SSH target or credential"}

        node = params.pop("node")
        dispatch = params.pop("dispatch")

        fwd_kwargs = {
            k: v for k, v in params.items()
            if k.startswith("ssh_")
        }

        try:
            forwarder = SSHTunnelForwarder(
                **fwd_kwargs,
                local_bind_address=("127.0.0.1", local_port),
                remote_bind_address=(remote_host, remote_port),
                set_keepalive=30,
            )
            forwarder.start()
        except Exception as exc:
            return {"ok": False, "error": f"Port forward failed: {exc}"}

        tunnel_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        fabric_id = dispatch.get("fabric_id")
        fabric_name = dispatch.get("fabric_name", "direct")

        mdb.get_conn().execute(
            "INSERT INTO tunnels (node_id, fabric_id, tunnel_type, local_port, "
            "remote_host, remote_port, status, created_at) "
            "VALUES (?, ?, 'local', ?, ?, ?, 'active', ?)",
            (node_id, fabric_id, local_port, remote_host, remote_port, now.isoformat()),
        )
        mdb.get_conn().commit()

        info = {
            "tunnel_id": tunnel_id,
            "node_id": node_id,
            "tunnel_type": "forward",
            "local_port": local_port,
            "remote_host": remote_host,
            "remote_port": remote_port,
            "fabric_id": fabric_id,
            "fabric_name": fabric_name,
            "hostname": node.get("hostname", ""),
            "address": dispatch["address"],
            "status": "active",
            "forwarder": forwarder,
            "created_at": now.isoformat(),
        }

        with self._lock:
            self._tunnels[tunnel_id] = info

        mdb.audit("tunnel_create",
                  f"Forward :{local_port} → {remote_host}:{remote_port} via {node.get('hostname', dispatch['address'])}",
                  username=mdb.get_setting("__last_user", "system"))

        return {
            "ok": True,
            "tunnel_id": tunnel_id,
            "tunnel_type": "forward",
            "local_port": local_port,
            "remote_host": remote_host,
            "remote_port": remote_port,
            "hostname": node.get("hostname", ""),
            "address": dispatch["address"],
            "fabric_name": fabric_name,
        }

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    def destroy(self, tunnel_id: str) -> bool:
        with self._lock:
            return self._destroy_tunnel(tunnel_id)

    def _destroy_tunnel(self, tunnel_id: str) -> bool:
        info = self._tunnels.pop(tunnel_id, None)
        if not info:
            return False
        fwd = info.get("forwarder")
        if fwd is not None:
            try:
                fwd.stop()
            except Exception:
                pass
        log.info("Tunnel %s destroyed (:%d)", tunnel_id[:8], info.get("local_port", 0))
        return True

    # ------------------------------------------------------------------
    # List
    # ------------------------------------------------------------------

    def list_tunnels(self) -> list[dict]:
        with self._lock:
            out = []
            for info in self._tunnels.values():
                out.append({
                    "tunnel_id": info["tunnel_id"],
                    "node_id": info["node_id"],
                    "tunnel_type": info["tunnel_type"],
                    "local_port": info["local_port"],
                    "remote_host": info.get("remote_host", ""),
                    "remote_port": info.get("remote_port", 0),
                    "fabric_name": info.get("fabric_name", ""),
                    "hostname": info.get("hostname", ""),
                    "address": info.get("address", ""),
                    "status": info.get("status", "unknown"),
                    "created_at": info.get("created_at", ""),
                })
            return out

    @property
    def active_count(self) -> int:
        return len(self._tunnels)

    # ------------------------------------------------------------------
    # Stale reaper
    # ------------------------------------------------------------------

    def _reap_stale(self):
        while self._running:
            try:
                with self._lock:
                    dead = []
                    for tid, info in self._tunnels.items():
                        fwd = info.get("forwarder")
                        if fwd is not None and not fwd.is_alive:
                            dead.append(tid)
                            info["status"] = "down"
                for tid in dead:
                    log.info("Reaping dead tunnel %s", tid[:8])
                    self.destroy(tid)
            except Exception:
                log.exception("Tunnel reaper error")
            interval = int(mdb.get_setting("tunnel_reaper_interval_s", "60"))
            time.sleep(interval)
