"""Nexus v2.2 — Terminal & Tunnel API routes + credential management."""
from __future__ import annotations

import io

from flask import Blueprint, current_app, jsonify, request, session

from models import db as mdb
from services import credential_store

terminal_bp = Blueprint("terminal", __name__)


def _tm():
    return current_app.config["ssh_terminal"]


def _tn():
    return current_app.config["ssh_tunnel"]


# ---------------------------------------------------------------------------
# SSH terminal sessions
# ---------------------------------------------------------------------------

@terminal_bp.route("/api/terminal/sessions")
def api_terminal_sessions():
    return jsonify({"sessions": _tm().list_sessions()})


@terminal_bp.route("/api/terminal/connect", methods=["POST"])
def api_terminal_connect():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"ok": False, "error": "node_id required"}), 400

    credential_id = data.get("credential_id")
    inline = data.get("inline_credential")

    if not credential_id and inline:
        username = str(inline.get("username", "")).strip()
        password = inline.get("password", "")
        ssh_key = inline.get("private_key", "")
        if not username:
            return jsonify({"ok": False, "error": "Username required"}), 400
        if ssh_key:
            cred_type = "ssh_key"
            secret = ssh_key
        elif password:
            cred_type = "password"
            secret = password
        else:
            return jsonify({"ok": False, "error": "Password or key required"}), 400
        encrypted = credential_store.encrypt(secret)
        credential_id = mdb.store_credential(
            node_id=int(node_id), label=f"{username}@inline",
            cred_type=cred_type, username=username, encrypted_value=encrypted,
        )
    elif not credential_id:
        return jsonify({"ok": False, "error": "credential_id or inline_credential required"}), 400

    user_sid = data.get("sid", "")
    result = _tm().connect(int(node_id), int(credential_id), user_sid)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@terminal_bp.route("/api/terminal/disconnect", methods=["POST"])
def api_terminal_disconnect():
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"ok": False, "error": "session_id required"}), 400
    ok = _tm().disconnect(session_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# SSH tunnels
# ---------------------------------------------------------------------------

@terminal_bp.route("/api/tunnels")
def api_tunnels():
    return jsonify({"tunnels": _tn().list_tunnels()})


@terminal_bp.route("/api/tunnels/socks", methods=["POST"])
def api_tunnel_socks():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    credential_id = data.get("credential_id")
    if not node_id or not credential_id:
        return jsonify({"ok": False, "error": "node_id and credential_id required"}), 400
    local_port = data.get("local_port")
    if local_port is not None:
        local_port = int(local_port)
    result = _tn().create_socks(int(node_id), int(credential_id), local_port)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@terminal_bp.route("/api/tunnels/forward", methods=["POST"])
def api_tunnel_forward():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    required = ("node_id", "credential_id", "local_port", "remote_host", "remote_port")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"ok": False, "error": f"Missing: {', '.join(missing)}"}), 400
    result = _tn().create_forward(
        int(data["node_id"]),
        int(data["credential_id"]),
        int(data["local_port"]),
        str(data["remote_host"]),
        int(data["remote_port"]),
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@terminal_bp.route("/api/tunnels/<tunnel_id>", methods=["DELETE"])
def api_tunnel_destroy(tunnel_id):
    ok = _tn().destroy(tunnel_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Credential management
# ---------------------------------------------------------------------------

@terminal_bp.route("/api/credentials/<int:node_id>")
def api_credentials(node_id):
    creds = mdb.get_credentials(node_id)
    return jsonify({"credentials": creds})


@terminal_bp.route("/api/credentials", methods=["POST"])
def api_credential_store():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    username = str(data.get("username", "")).strip()
    label = str(data.get("label", "")).strip() or f"{username}@node"
    password = data.get("password")
    ssh_key = data.get("ssh_key")

    if not node_id:
        return jsonify({"ok": False, "error": "node_id required"}), 400
    if not username:
        return jsonify({"ok": False, "error": "username required"}), 400

    if ssh_key:
        cred_type = "ssh_key"
        try:
            encrypted = credential_store.encrypt(ssh_key)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Encryption failed: {exc}"}), 500
    elif password:
        cred_type = "password"
        try:
            encrypted = credential_store.encrypt(password)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Encryption failed: {exc}"}), 500
    else:
        return jsonify({"ok": False, "error": "password or ssh_key required"}), 400

    cred_id = mdb.store_credential(
        node_id=int(node_id),
        label=label,
        cred_type=cred_type,
        username=username,
        encrypted_value=encrypted,
    )
    mdb.audit("credential_store",
              f"Stored {cred_type} credential '{label}' for node {node_id}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "credential_id": cred_id})


@terminal_bp.route("/api/credentials/<int:cred_id>", methods=["DELETE"])
def api_credential_delete(cred_id):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    mdb.delete_credential(cred_id)
    mdb.audit("credential_delete", f"Deleted credential {cred_id}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True})


@terminal_bp.route("/api/credentials/<int:cred_id>/test", methods=["POST"])
def api_credential_test(cred_id):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"ok": False, "error": "node_id required"}), 400

    cred = mdb.get_credential_raw(cred_id)
    if not cred:
        return jsonify({"ok": False, "error": "Credential not found"}), 404

    fm = current_app.config["fabric_manager"]
    dispatch = fm.get_best_dispatch(int(node_id))
    if not dispatch:
        return jsonify({"ok": False, "error": "No reachable path to node"}), 400

    address = dispatch["address"]
    username = cred.get("username", "")
    cred_type = cred.get("cred_type", "password")
    try:
        secret = credential_store.decrypt(cred["encrypted_value"])
    except Exception:
        return jsonify({"ok": False, "error": "Decryption failed"}), 500

    try:
        import paramiko
    except ImportError:
        return jsonify({"ok": False, "error": "paramiko not installed"}), 500

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": address,
        "port": 22,
        "username": username,
        "timeout": 8,
        "banner_timeout": 8,
        "auth_timeout": 8,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if cred_type == "ssh_key":
        try:
            pkey = paramiko.RSAKey.from_private_key(io.StringIO(secret))
        except Exception:
            try:
                pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(secret))
            except Exception:
                pkey = paramiko.ECDSAKey.from_private_key(io.StringIO(secret))
        connect_kwargs["pkey"] = pkey
    else:
        connect_kwargs["password"] = secret

    try:
        client.connect(**connect_kwargs)
        client.close()
        return jsonify({"ok": True, "message": f"SSH to {address} succeeded"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})
