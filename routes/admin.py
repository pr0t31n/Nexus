"""Nexus v2.0 — Admin routes: settings, audit log, user management."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from models import db as mdb
from config import ROLES

admin_bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------

@admin_bp.route("/api/settings", methods=["GET"])
def api_settings():
    return jsonify(mdb.get_all_settings())


@admin_bp.route("/api/settings", methods=["POST"])
def api_settings_update():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    updated = []
    for key, value in data.items():
        mdb.set_setting(key, str(value))
        updated.append(key)
    mdb.audit("settings_update", f"Updated: {', '.join(updated)}",
              username=session.get("username", "unknown"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "updated": updated})


# ---------------------------------------------------------------------------
# Audit log API
# ---------------------------------------------------------------------------

@admin_bp.route("/api/audit")
def api_audit():
    limit = min(int(request.args.get("limit", 200)), 1000)
    offset = max(int(request.args.get("offset", 0)), 0)
    entries = mdb.get_audit_log(limit=limit, offset=offset)
    return jsonify({"entries": entries, "limit": limit, "offset": offset})


# ---------------------------------------------------------------------------
# Fleet stats API (used by sidebar counters)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/fleet/stats")
def api_fleet_stats():
    return jsonify(mdb.fleet_stats())


# ---------------------------------------------------------------------------
# Fleet nodes API (basic — Phase 2 expands this significantly)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/fleet/nodes")
def api_fleet_nodes():
    status = request.args.get("status")
    nodes = mdb.get_fleet_nodes(status=status)
    for n in nodes:
        n["tools"] = mdb.get_tool_instances(n["id"])
        n["addresses"] = mdb.get_node_addresses(n["id"])
    return jsonify({"nodes": nodes})


@admin_bp.route("/api/fleet/nodes/<int:node_id>")
def api_fleet_node(node_id: int):
    node = mdb.get_fleet_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    node["tools"] = mdb.get_tool_instances(node_id)
    node["addresses"] = mdb.get_node_addresses(node_id)
    return jsonify(node)


# ---------------------------------------------------------------------------
# Network fabrics API (basic — Phase 2 expands)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/fabrics")
def api_fabrics():
    return jsonify({"fabrics": mdb.get_fabrics()})


@admin_bp.route("/api/fabrics", methods=["POST"])
def api_fabric_create():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", "")).strip()
    ftype = str(data.get("fabric_type", "")).strip()
    if not name or not ftype:
        return jsonify({"ok": False, "error": "name and fabric_type required"}), 400
    import json
    fid = mdb.create_fabric(
        name=name,
        fabric_type=ftype,
        subnets=json.dumps(data.get("subnets", [])),
        config=json.dumps(data.get("config", {})),
        priority=int(data.get("priority", 100)),
    )
    mdb.audit("fabric_create", f"Created fabric '{name}' ({ftype})",
              username=session.get("username", "unknown"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "id": fid})


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

@admin_bp.route("/settings")
def settings_page():
    return render_template("settings.html")
