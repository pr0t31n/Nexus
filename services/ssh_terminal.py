"""Nexus v2.2 — SSH Terminal Manager: paramiko sessions bridged to SocketIO."""
from __future__ import annotations

import io
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

try:
    import paramiko
except ImportError:
    paramiko = None

from models import db as mdb
from services import credential_store

log = logging.getLogger("nexus.ssh_terminal")

_BUF_SIZE = 4096


class SSHTerminalManager:

    def __init__(self, socketio, fabric_manager):
        self._sessions: dict[str, dict] = {}
        self._socketio = socketio
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
            target=self._reap_idle, daemon=True, name="nexus-ssh-reaper",
        )
        self._reaper.start()
        log.info("SSH terminal manager started")

    def stop(self):
        self._running = False
        with self._lock:
            for sid in list(self._sessions):
                self._close_session(sid)
        log.info("SSH terminal manager stopped")

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    def connect(self, node_id: int, credential_id: int, user_sid: str) -> dict:
        if paramiko is None:
            return {"ok": False, "error": "paramiko not installed"}

        max_sessions = int(mdb.get_setting("max_ssh_sessions", "10"))
        with self._lock:
            if len(self._sessions) >= max_sessions:
                return {"ok": False, "error": f"Max {max_sessions} concurrent sessions reached"}

        node = mdb.get_fleet_node(node_id)
        if not node:
            return {"ok": False, "error": "Node not found"}

        cred = mdb.get_credential_raw(credential_id)
        if not cred:
            return {"ok": False, "error": "Credential not found"}

        dispatch = self._fm.get_best_dispatch(node_id)
        if not dispatch:
            return {"ok": False, "error": "No reachable network path to node"}

        address = dispatch["address"]
        fabric_name = dispatch.get("fabric_name", "direct")
        fabric_id = dispatch.get("fabric_id")

        username = cred.get("username", "")
        cred_type = cred.get("cred_type", "password")
        try:
            secret = credential_store.decrypt(cred["encrypted_value"])
        except Exception as exc:
            return {"ok": False, "error": f"Credential decryption failed: {exc}"}

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": address,
            "port": 22,
            "username": username,
            "timeout": 10,
            "banner_timeout": 10,
            "auth_timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }

        if cred_type == "ssh_key":
            try:
                pkey = paramiko.RSAKey.from_private_key(io.StringIO(secret))
            except paramiko.ssh_exception.SSHException:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(secret))
                except paramiko.ssh_exception.SSHException:
                    pkey = paramiko.ECDSAKey.from_private_key(io.StringIO(secret))
            connect_kwargs["pkey"] = pkey
        else:
            connect_kwargs["password"] = secret

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            client.close()
            return {"ok": False, "error": f"SSH connection failed: {exc}"}

        try:
            channel = client.invoke_shell(
                term="xterm-256color", width=120, height=40,
            )
            channel.settimeout(0.0)
        except Exception as exc:
            client.close()
            return {"ok": False, "error": f"Shell failed: {exc}"}

        session_id = uuid.uuid4().hex[:16]
        now = datetime.now(timezone.utc)
        via = f"{fabric_name} → {address}"

        sess = {
            "session_id": session_id,
            "node_id": node_id,
            "hostname": node.get("hostname", address),
            "ip": address,
            "port": 22,
            "fabric_id": fabric_id,
            "fabric_name": fabric_name,
            "via": via,
            "user_sid": user_sid,
            "client": client,
            "channel": channel,
            "connected_at": now.isoformat(),
            "last_activity": now.isoformat(),
        }

        reader = threading.Thread(
            target=self._reader_loop,
            args=(session_id,),
            daemon=True,
            name=f"nexus-ssh-{session_id[:8]}",
        )
        sess["reader_thread"] = reader

        with self._lock:
            self._sessions[session_id] = sess

        reader.start()

        mdb.audit("ssh_connect",
                  f"SSH to {node.get('hostname', address)} via {via}",
                  username=mdb.get_setting("__last_user", "system"))

        return {
            "ok": True,
            "session_id": session_id,
            "hostname": sess["hostname"],
            "ip": address,
            "via": via,
            "fabric_name": fabric_name,
        }

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    def send_input(self, session_id: str, data: str) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return False
        try:
            sess["channel"].sendall(data.encode("utf-8", errors="replace"))
            sess["last_activity"] = datetime.now(timezone.utc).isoformat()
            return True
        except Exception:
            self.disconnect(session_id)
            return False

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return False
        try:
            sess["channel"].resize_pty(width=cols, height=rows)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Disconnect
    # ------------------------------------------------------------------

    def disconnect(self, session_id: str) -> bool:
        with self._lock:
            return self._close_session(session_id)

    def disconnect_all(self, user_sid: str) -> int:
        closed = 0
        with self._lock:
            for sid in list(self._sessions):
                if self._sessions[sid].get("user_sid") == user_sid:
                    self._close_session(sid)
                    closed += 1
        return closed

    def _close_session(self, session_id: str) -> bool:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return False
        try:
            sess["channel"].close()
        except Exception:
            pass
        try:
            sess["client"].close()
        except Exception:
            pass
        self._socketio.emit("term_closed", {"session_id": session_id},
                            to=sess.get("user_sid"))
        log.info("SSH session %s closed (%s)", session_id[:8], sess.get("hostname", "?"))
        return True

    # ------------------------------------------------------------------
    # Session listing
    # ------------------------------------------------------------------

    def list_sessions(self) -> list[dict]:
        with self._lock:
            out = []
            for sess in self._sessions.values():
                out.append({
                    "session_id": sess["session_id"],
                    "node_id": sess["node_id"],
                    "hostname": sess["hostname"],
                    "ip": sess["ip"],
                    "fabric_name": sess["fabric_name"],
                    "via": sess["via"],
                    "connected_at": sess["connected_at"],
                    "last_activity": sess["last_activity"],
                })
            return out

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            return self._sessions.get(session_id)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _reader_loop(self, session_id: str):
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return
        channel = sess["channel"]
        user_sid = sess["user_sid"]
        try:
            while self._running:
                if channel.closed or channel.exit_status_ready():
                    break
                try:
                    data = channel.recv(_BUF_SIZE)
                except Exception:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                self._socketio.emit("term_output", {
                    "session_id": session_id,
                    "data": text,
                }, to=user_sid)
                sess["last_activity"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            log.exception("Reader loop error for session %s", session_id[:8])
        finally:
            self.disconnect(session_id)

    # ------------------------------------------------------------------
    # Idle reaper
    # ------------------------------------------------------------------

    def _reap_idle(self):
        while self._running:
            try:
                timeout_s = int(mdb.get_setting("ssh_idle_timeout_s", "1800"))
                now = datetime.now(timezone.utc)
                with self._lock:
                    stale = []
                    for sid, sess in self._sessions.items():
                        try:
                            last = datetime.fromisoformat(sess["last_activity"])
                            if last.tzinfo is None:
                                last = last.replace(tzinfo=timezone.utc)
                            idle = (now - last).total_seconds()
                            if idle >= timeout_s:
                                stale.append(sid)
                        except Exception:
                            pass
                for sid in stale:
                    log.info("Reaping idle SSH session %s", sid[:8])
                    self.disconnect(sid)
            except Exception:
                log.exception("SSH idle reaper error")
            time.sleep(30)
