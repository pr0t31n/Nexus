"""Nexus v2.3 — Arsenal Bridge: fabric-aware remote tool management."""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

try:
    import paramiko
except ImportError:
    paramiko = None

from models import db as mdb
from services import credential_store
from config import TOOL_INSTALL_MAP

log = logging.getLogger("nexus.arsenal_bridge")

_MAX_CONCURRENT = 10
_CMD_TIMEOUT = 300


def _tool_dir(tool_name: str) -> str:
    script = TOOL_INSTALL_MAP.get(tool_name, "")
    if script:
        return str(os.path.dirname(script))
    canon = tool_name.capitalize()
    return f"/opt/pr0t31n/{canon}"


def _service_name(tool_name: str) -> str:
    return f"pr0t31n-{tool_name}"


def _build_command(operation: str, tool_name: str) -> str | None:
    d = _tool_dir(tool_name)
    svc = _service_name(tool_name)
    cmds = {
        "install": f"cd {d} && python3 install.py 2>&1",
        "update":  f"cd {d} && git pull 2>&1 && python3 install.py --skip-system 2>&1",
        "status":  f"cd {d} && python3 install.py --check 2>&1",
        "restart": f"systemctl restart {svc} 2>&1 || launchctl kickstart -k system/com.pr0t31n.{tool_name} 2>&1",
        "version": f"cat {d}/VERSION 2>/dev/null || echo unknown",
    }
    return cmds.get(operation)


def _parse_pkey(secret: str):
    try:
        return paramiko.RSAKey.from_private_key(io.StringIO(secret))
    except paramiko.ssh_exception.SSHException:
        pass
    try:
        return paramiko.Ed25519Key.from_private_key(io.StringIO(secret))
    except paramiko.ssh_exception.SSHException:
        pass
    return paramiko.ECDSAKey.from_private_key(io.StringIO(secret))


class ArsenalBridge:

    def __init__(self, socketio, fabric_manager):
        self._socketio = socketio
        self._fm = fabric_manager
        self._lock = threading.Lock()
        self._active: dict[str, dict] = {}
        self._pool = ThreadPoolExecutor(max_workers=_MAX_CONCURRENT,
                                        thread_name_prefix="nexus-arsenal")
        self._running = False

    def start(self):
        self._running = True
        log.info("Arsenal bridge started")

    def stop(self):
        self._running = False
        with self._lock:
            for info in self._active.values():
                client = info.get("client")
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass
            self._active.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        log.info("Arsenal bridge stopped")

    # ------------------------------------------------------------------
    # Run single operation
    # ------------------------------------------------------------------

    def run_operation(self, node_id: int, operation: str, tool_name: str,
                      credential_id: int, user_sid: str = "") -> dict:
        if paramiko is None:
            return {"ok": False, "error": "paramiko not installed"}

        cmd = _build_command(operation, tool_name)
        if not cmd:
            return {"ok": False, "error": f"Unknown operation: {operation}"}

        dispatch = self._fm.get_best_dispatch(node_id)
        if not dispatch:
            return {"ok": False, "error": "No reachable path to node"}

        cred = mdb.get_credential_raw(credential_id)
        if not cred:
            return {"ok": False, "error": "Credential not found"}

        op_id = uuid.uuid4().hex[:16]
        db_id = mdb.create_arsenal_op(node_id, operation, tool_name)

        self._pool.submit(
            self._execute, op_id, db_id, node_id, credential_id,
            cred, dispatch, cmd, operation, tool_name, user_sid,
        )

        return {"ok": True, "op_id": op_id, "db_id": db_id}

    def _execute(self, op_id, db_id, node_id, credential_id,
                 cred, dispatch, cmd, operation, tool_name, user_sid):
        address = dispatch["address"]
        username = cred.get("username", "")
        cred_type = cred.get("cred_type", "password")

        try:
            secret = credential_store.decrypt(cred["encrypted_value"])
        except Exception as exc:
            self._finish(op_id, db_id, user_sid, "failed",
                         f"Credential decryption failed: {exc}", -1)
            return

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": address, "port": 22, "username": username,
            "timeout": 15, "banner_timeout": 15, "auth_timeout": 15,
            "allow_agent": False, "look_for_keys": False,
        }

        if cred_type == "ssh_key":
            try:
                connect_kwargs["pkey"] = _parse_pkey(secret)
            except Exception as exc:
                self._finish(op_id, db_id, user_sid, "failed",
                             f"Key parse failed: {exc}", -1)
                return
        else:
            connect_kwargs["password"] = secret

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            client.close()
            self._finish(op_id, db_id, user_sid, "failed",
                         f"SSH connect failed: {exc}", -1)
            return

        with self._lock:
            self._active[db_id] = {
                "client": client, "node_id": node_id, "op_id": op_id,
                "operation": operation, "tool_name": tool_name,
            }

        mdb.update_arsenal_op(db_id, status="running",
                              started_at=datetime.now(timezone.utc).isoformat())

        self._socketio.emit("arsenal_started", {
            "op_id": op_id, "db_id": db_id,
            "node_id": node_id, "operation": operation, "tool_name": tool_name,
        }, to=user_sid or None)

        output_buf = []
        exit_code = -1
        try:
            _, stdout, stderr = client.exec_command(cmd, timeout=_CMD_TIMEOUT)
            while True:
                if stdout.channel.recv_ready():
                    chunk = stdout.channel.recv(4096).decode("utf-8", errors="replace")
                    if chunk:
                        output_buf.append(chunk)
                        self._socketio.emit("arsenal_output", {
                            "op_id": op_id, "chunk": chunk,
                        }, to=user_sid or None)
                elif stdout.channel.exit_status_ready():
                    remaining = stdout.read().decode("utf-8", errors="replace")
                    if remaining:
                        output_buf.append(remaining)
                    err_data = stderr.read().decode("utf-8", errors="replace")
                    if err_data:
                        output_buf.append(err_data)
                    exit_code = stdout.channel.recv_exit_status()
                    break
                else:
                    time.sleep(0.1)

        except Exception as exc:
            output_buf.append(f"\n[ERROR] {exc}")
        finally:
            client.close()
            with self._lock:
                self._active.pop(db_id, None)

        status = "completed" if exit_code == 0 else "failed"
        full_output = "".join(output_buf)
        self._finish(op_id, db_id, user_sid, status, full_output, exit_code)

    def _finish(self, op_id, db_id, user_sid, status, output, exit_code):
        mdb.update_arsenal_op(
            db_id, status=status, output=output,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        self._socketio.emit("arsenal_complete", {
            "op_id": op_id, "db_id": db_id,
            "status": status, "exit_code": exit_code, "output": output,
        }, to=user_sid or None)

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def batch_operation(self, node_ids: list[int], operation: str,
                        tool_name: str, credential_id: int,
                        user_sid: str = "") -> dict:
        results = []
        for nid in node_ids[:_MAX_CONCURRENT]:
            r = self.run_operation(nid, operation, tool_name,
                                   credential_id, user_sid)
            results.append({"node_id": nid, **r})
        return {"ok": True, "operations": results}

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel_operation(self, db_id: int) -> bool:
        with self._lock:
            info = self._active.pop(db_id, None)
        if not info:
            return False
        client = info.get("client")
        if client:
            try:
                client.close()
            except Exception:
                pass
        mdb.update_arsenal_op(
            db_id, status="cancelled",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_operations(self, node_id: int | None = None,
                        limit: int = 50) -> list[dict]:
        return mdb.get_arsenal_ops(node_id=node_id, limit=limit)

    def get_operation(self, op_id: int) -> dict | None:
        return mdb.get_arsenal_op(op_id)

    @property
    def active_count(self) -> int:
        return len(self._active)
