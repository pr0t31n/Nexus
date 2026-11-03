"""Nexus v2.3 — Arsenal + Versions API routes."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session

from models import db as mdb

arsenal_bp = Blueprint("arsenal", __name__)


def _bridge():
    return current_app.config["arsenal_bridge"]


def _tracker():
    return current_app.config["version_tracker"]


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

@arsenal_bp.route("/api/arsenal/operations")
def api_arsenal_operations():
    node_id = request.args.get("node_id", type=int)
    limit = request.args.get("limit", 50, type=int)
    ops = _bridge().list_operations(node_id=node_id, limit=limit)
    return jsonify({"ok": True, "operations": ops})


@arsenal_bp.route("/api/arsenal/operations/<int:op_id>")
def api_arsenal_operation(op_id):
    op = _bridge().get_operation(op_id)
    if not op:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "operation": op})


@arsenal_bp.route("/api/arsenal/run", methods=["POST"])
def api_arsenal_run():
    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    operation = data.get("operation", "").strip()
    tool_name = data.get("tool_name", "").strip()
    credential_id = data.get("credential_id")

    if not all([node_id, operation, tool_name, credential_id]):
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    result = _bridge().run_operation(
        node_id=int(node_id),
        operation=operation,
        tool_name=tool_name,
        credential_id=int(credential_id),
        user_sid=request.sid if hasattr(request, "sid") else "",
    )
    mdb.audit("arsenal_run", f"{operation} {tool_name} on node {node_id}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    status = 200 if result.get("ok") else 400
    return jsonify(result), status


@arsenal_bp.route("/api/arsenal/batch", methods=["POST"])
def api_arsenal_batch():
    data = request.get_json(force=True, silent=True) or {}
    node_ids = data.get("node_ids", [])
    operation = data.get("operation", "").strip()
    tool_name = data.get("tool_name", "").strip()
    credential_id = data.get("credential_id")

    if not all([node_ids, operation, tool_name, credential_id]):
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    if not isinstance(node_ids, list) or len(node_ids) > 10:
        return jsonify({"ok": False, "error": "node_ids must be a list (max 10)"}), 400

    result = _bridge().batch_operation(
        node_ids=[int(n) for n in node_ids],
        operation=operation,
        tool_name=tool_name,
        credential_id=int(credential_id),
        user_sid=request.sid if hasattr(request, "sid") else "",
    )
    mdb.audit("arsenal_batch", f"{operation} {tool_name} on {len(node_ids)} nodes",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify(result)


@arsenal_bp.route("/api/arsenal/cancel/<int:op_id>", methods=["POST"])
def api_arsenal_cancel(op_id):
    ok = _bridge().cancel_operation(op_id)
    if ok:
        mdb.audit("arsenal_cancel", f"Cancelled operation {op_id}",
                  username=session.get("username", "system"),
                  ip=request.remote_addr or "")
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Tools (instances)
# ---------------------------------------------------------------------------

@arsenal_bp.route("/api/arsenal/tools")
def api_arsenal_tools():
    node_id = request.args.get("node_id", type=int)
    tools = mdb.get_tool_instances(node_id=node_id)
    return jsonify({"ok": True, "tools": tools})


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@arsenal_bp.route("/api/arsenal/versions")
def api_arsenal_versions():
    return jsonify({"ok": True, **_tracker().get_matrix()})


@arsenal_bp.route("/api/arsenal/versions/summary")
def api_arsenal_version_summary():
    return jsonify({"ok": True, **_tracker().get_summary()})


@arsenal_bp.route("/api/arsenal/versions/refresh", methods=["POST"])
def api_arsenal_version_refresh():
    import threading
    def _do():
        _tracker().refresh()
    threading.Thread(target=_do, daemon=True, name="nexus-ver-refresh").start()
    mdb.audit("version_refresh", "Manual version refresh triggered",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "status": "refresh_started"})
