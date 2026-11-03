"""Nexus v2.4 — Broker Server: WebSocket dispatch for fleet agents."""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from models import db as mdb
from config import NEXUS_DIR

log = logging.getLogger("nexus.broker")

_BROKER_PORT = 5401
_HEARTBEAT_TIMEOUT = 90
_STALE_CHECK_INTERVAL = 30
_COMMAND_TIMEOUT = 300

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    import uvicorn
except ImportError:
    FastAPI = None
    uvicorn = None


class _AgentConnection:
    __slots__ = (
        "ws", "hostname", "os_family", "arch", "agent_version",
        "fabrics", "node_id", "last_heartbeat", "allowed_tools",
    )

    def __init__(self, ws, hostname="", os_family="", arch="",
                 agent_version="", fabrics=None, node_id=None):
        self.ws = ws
        self.hostname = hostname
        self.os_family = os_family
        self.arch = arch
        self.agent_version = agent_version
        self.fabrics = fabrics or []
        self.node_id = node_id
        self.last_heartbeat = time.monotonic()
        self.allowed_tools: list[str] = []


class BrokerServer:

    def __init__(self, socketio, fabric_manager):
        self._socketio = socketio
        self._fm = fabric_manager
        self._lock = threading.Lock()
        self._agents: dict[str, _AgentConnection] = {}
        self._pending_commands: dict[str, dict] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._stale_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cert_dir = NEXUS_DIR / "certs"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        if FastAPI is None or uvicorn is None:
            log.warning("FastAPI/uvicorn not installed — broker disabled")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run_server, daemon=True, name="nexus-broker",
        )
        self._thread.start()
        self._stale_thread = threading.Thread(
            target=self._stale_loop, daemon=True, name="nexus-broker-stale",
        )
        self._stale_thread.start()
        log.info("Broker server starting on port %d", _BROKER_PORT)

    def stop(self):
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        with self._lock:
            self._agents.clear()
            self._pending_commands.clear()
        log.info("Broker server stopped")

    # ------------------------------------------------------------------
    # FastAPI WebSocket server (runs in dedicated thread)
    # ------------------------------------------------------------------

    def _run_server(self):
        app = FastAPI(title="Nexus Broker", docs_url=None, redoc_url=None)

        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            agent_id = uuid.uuid4().hex[:16]
            try:
                await self._handle_agent(agent_id, ws)
            except WebSocketDisconnect:
                pass
            except Exception:
                log.exception("Agent %s connection error", agent_id)
            finally:
                self._remove_agent(agent_id)

        ssl_ctx = self._get_ssl_context()
        config = uvicorn.Config(
            app, host="0.0.0.0", port=_BROKER_PORT,
            log_level="warning",
            ssl_keyfile=str(self._cert_dir / "broker_server.key") if ssl_ctx else None,
            ssl_certfile=str(self._cert_dir / "broker_server.pem") if ssl_ctx else None,
            ssl_ca_certs=str(self._cert_dir / "broker_ca.pem") if ssl_ctx else None,
        )
        server = uvicorn.Server(config)
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(server.serve())

    def _get_ssl_context(self):
        try:
            from services.broker_ca import get_ssl_context
            return get_ssl_context(self._cert_dir)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Agent message handling
    # ------------------------------------------------------------------

    async def _handle_agent(self, agent_id: str, ws: WebSocket):
        conn = _AgentConnection(ws=ws)
        with self._lock:
            self._agents[agent_id] = conn

        while self._running:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=_HEARTBEAT_TIMEOUT)
            except asyncio.TimeoutError:
                log.info("Agent %s heartbeat timeout", agent_id)
                break
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "register":
                self._handle_register(agent_id, conn, msg)
            elif msg_type == "heartbeat":
                conn.last_heartbeat = time.monotonic()
                if conn.node_id:
                    mdb.update_fleet_node(conn.node_id, status="online",
                                          last_seen=_now())
            elif msg_type == "output":
                self._handle_output(msg)
            elif msg_type == "complete":
                self._handle_complete(msg)

    def _handle_register(self, agent_id: str, conn: _AgentConnection, msg: dict):
        conn.hostname = msg.get("hostname", "")
        conn.os_family = msg.get("os_family", "")
        conn.arch = msg.get("arch", "")
        conn.agent_version = msg.get("agent_version", "")
        conn.fabrics = msg.get("fabrics", [])
        conn.allowed_tools = msg.get("allowed_tools", [])

        node = self._resolve_node(conn)
        conn.node_id = node["id"] if node else None

        if conn.node_id:
            mdb.update_fleet_node(
                conn.node_id,
                status="online",
                agent_version=conn.agent_version,
                agent_dist=f"{conn.os_family}/{conn.arch}",
                enrolled=1,
                os_family=conn.os_family,
                arch=conn.arch,
                last_seen=_now(),
            )
            self._update_fabric_addresses(conn)

        self._socketio.emit("broker_agent_connected", {
            "agent_id": agent_id,
            "hostname": conn.hostname,
            "node_id": conn.node_id,
            "os_family": conn.os_family,
            "arch": conn.arch,
        })
        log.info("Agent registered: %s (%s/%s) node=%s",
                 conn.hostname, conn.os_family, conn.arch, conn.node_id)

    def _resolve_node(self, conn: _AgentConnection) -> dict | None:
        nodes = mdb.get_fleet_nodes()
        for n in nodes:
            if n["hostname"] == conn.hostname:
                return n
        return None

    def _update_fabric_addresses(self, conn: _AgentConnection):
        if not conn.node_id or not conn.fabrics:
            return
        for fab in conn.fabrics:
            fabric_name = fab.get("name", "")
            address = fab.get("address", "")
            if not fabric_name or not address:
                continue
            fabrics = mdb.get_conn().execute(
                "SELECT id FROM network_fabrics WHERE name = ?", (fabric_name,)
            ).fetchone()
            if fabrics:
                mdb.upsert_node_address(
                    conn.node_id, fabrics[0], address,
                    lane_status="up",
                )

    def _handle_output(self, msg: dict):
        cmd_id = msg.get("id", "")
        chunk = msg.get("chunk", "")
        with self._lock:
            cmd = self._pending_commands.get(cmd_id)
        if cmd and chunk:
            self._socketio.emit("broker_command_output", {
                "cmd_id": cmd_id,
                "dispatch_id": cmd.get("dispatch_id"),
                "chunk": chunk,
            }, to=cmd.get("user_sid") or None)

    def _handle_complete(self, msg: dict):
        cmd_id = msg.get("id", "")
        with self._lock:
            cmd = self._pending_commands.pop(cmd_id, None)
        if cmd:
            self._socketio.emit("broker_command_complete", {
                "cmd_id": cmd_id,
                "dispatch_id": cmd.get("dispatch_id"),
                "exit_code": msg.get("exit_code", -1),
                "output": msg.get("output", ""),
            }, to=cmd.get("user_sid") or None)

    def _remove_agent(self, agent_id: str):
        with self._lock:
            conn = self._agents.pop(agent_id, None)
        if conn and conn.node_id:
            mdb.update_fleet_node(conn.node_id, status="offline", last_seen=_now())
            self._socketio.emit("broker_agent_disconnected", {
                "agent_id": agent_id,
                "hostname": conn.hostname,
                "node_id": conn.node_id,
            })
            log.info("Agent disconnected: %s (node=%s)", conn.hostname, conn.node_id)

    # ------------------------------------------------------------------
    # Stale agent reaper
    # ------------------------------------------------------------------

    def _stale_loop(self):
        while self._running:
            self._check_stale()
            for _ in range(int(_STALE_CHECK_INTERVAL / 5)):
                if not self._running:
                    return
                time.sleep(5)

    def _check_stale(self):
        now = time.monotonic()
        stale = []
        with self._lock:
            for aid, conn in self._agents.items():
                if now - conn.last_heartbeat > _HEARTBEAT_TIMEOUT:
                    stale.append(aid)
        for aid in stale:
            log.info("Removing stale agent %s", aid)
            self._remove_agent(aid)

    # ------------------------------------------------------------------
    # Public API — command dispatch
    # ------------------------------------------------------------------

    def dispatch_command(self, node_id: int, cmd: str,
                         timeout: int = _COMMAND_TIMEOUT,
                         dispatch_id: str = "",
                         user_sid: str = "") -> dict:
        agent_id, conn = self._find_agent_for_node(node_id)
        if not agent_id or not conn:
            return {"ok": False, "error": "No agent connected for this node"}

        cmd_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._pending_commands[cmd_id] = {
                "node_id": node_id,
                "agent_id": agent_id,
                "dispatch_id": dispatch_id,
                "user_sid": user_sid,
                "cmd": cmd,
                "started": _now(),
            }

        payload = json.dumps({
            "type": "command",
            "id": cmd_id,
            "cmd": cmd,
            "timeout": timeout,
        })

        if self._loop and conn.ws:
            asyncio.run_coroutine_threadsafe(
                conn.ws.send_text(payload), self._loop
            )

        return {"ok": True, "cmd_id": cmd_id}

    def cancel_command(self, cmd_id: str) -> bool:
        with self._lock:
            cmd = self._pending_commands.pop(cmd_id, None)
        if not cmd:
            return False
        agent_id = cmd.get("agent_id")
        with self._lock:
            conn = self._agents.get(agent_id)
        if conn and conn.ws and self._loop:
            payload = json.dumps({"type": "cancel", "id": cmd_id})
            asyncio.run_coroutine_threadsafe(conn.ws.send_text(payload), self._loop)
        return True

    def _find_agent_for_node(self, node_id: int) -> tuple[str | None, _AgentConnection | None]:
        with self._lock:
            for aid, conn in self._agents.items():
                if conn.node_id == node_id:
                    return aid, conn
        return None, None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_connected_agents(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "agent_id": aid,
                    "hostname": c.hostname,
                    "node_id": c.node_id,
                    "os_family": c.os_family,
                    "arch": c.arch,
                    "agent_version": c.agent_version,
                    "fabrics": c.fabrics,
                    "last_heartbeat_ago": int(time.monotonic() - c.last_heartbeat),
                }
                for aid, c in self._agents.items()
            ]

    @property
    def agent_count(self) -> int:
        return len(self._agents)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
