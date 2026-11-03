"""Nexus v2.1 — Fleet routes: discovery, nodes, checkin."""
from __future__ import annotations

import ipaddress
import json
import threading

from flask import Blueprint, current_app, jsonify, request, session

from models import db as mdb
from config import TOOL_REGISTRY

fleet_bp = Blueprint("fleet", __name__)


# ---------------------------------------------------------------------------
# Full fleet payload
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet")
def api_fleet():
    nodes = mdb.get_fleet_nodes()
    for n in nodes:
        n["tools"] = mdb.get_tool_instances(n["id"])
        n["addresses"] = mdb.get_node_addresses(n["id"])
    fabrics = mdb.get_fabrics()
    return jsonify({
        "nodes": nodes,
        "fabrics": fabrics,
        "stats": mdb.fleet_stats(),
        "last_scan": _last_scan_iso(),
    })


# ---------------------------------------------------------------------------
# Manual scan trigger
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/scan", methods=["POST"])
def api_fleet_scan():
    fd = current_app.config.get("fleet_discovery")
    if fd:
        threading.Thread(target=fd.full_scan, daemon=True).start()
        return jsonify({"ok": True, "status": "scan_started"})
    return jsonify({"ok": False, "error": "Fleet discovery not initialised"}), 503


# ---------------------------------------------------------------------------
# Fleet nodes — list with search/filter
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/nodes")
def api_fleet_nodes():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()
    fabric = request.args.get("fabric", "").strip()
    role = request.args.get("role", "").strip()

    fabric_id = 0
    if fabric:
        try:
            fabric_id = int(fabric)
        except ValueError:
            pass

    nodes = mdb.search_fleet_nodes(
        search=search, status=status,
        fabric_id=fabric_id, role=role,
    )
    for n in nodes:
        n["tools"] = mdb.get_tool_instances(n["id"])
        n["addresses"] = mdb.get_node_addresses(n["id"])
    return jsonify({"nodes": nodes})


# ---------------------------------------------------------------------------
# Single node detail
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/nodes/<int:node_id>")
def api_fleet_node(node_id: int):
    node = mdb.get_fleet_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    node["tools"] = mdb.get_tool_instances(node_id)
    node["addresses"] = mdb.get_node_addresses(node_id)
    node["credentials"] = mdb.get_credentials(node_id)
    return jsonify(node)


# ---------------------------------------------------------------------------
# Update node
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/nodes/<int:node_id>", methods=["POST", "PATCH"])
def api_fleet_node_update(node_id: int):
    node = mdb.get_fleet_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"display_name", "role", "device_type", "notes", "vm_role",
               "enrolled", "os_family", "arch"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"ok": False, "error": "No valid fields to update"}), 400
    mdb.update_fleet_node(node_id, **updates)
    mdb.audit("node_update", f"Updated node {node['hostname']}: {list(updates.keys())}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "updated": list(updates.keys())})


# ---------------------------------------------------------------------------
# Delete node
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/nodes/<int:node_id>", methods=["DELETE"])
def api_fleet_node_delete(node_id: int):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    node = mdb.get_fleet_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    mdb.delete_fleet_node(node_id)
    mdb.audit("node_delete", f"Deleted node {node['hostname']}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Nexus status (self-report for fleet discovery)
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/nexus-status")
def api_nexus_status():
    from pathlib import Path
    version = Path(__file__).resolve().parent.parent.joinpath("VERSION").read_text().strip()
    fd = current_app.config.get("fleet_discovery")
    scanning = fd.is_scanning if fd and hasattr(fd, "is_scanning") else False
    return jsonify({
        "tool": "nexus",
        "version": version,
        "scanning": scanning,
    })


# ---------------------------------------------------------------------------
# Tier 2: Agent / tool checkin
# ---------------------------------------------------------------------------

@fleet_bp.route("/api/fleet/checkin", methods=["POST"])
def api_fleet_checkin():
    api_key = mdb.get_setting("checkin_api_key", "")
    if api_key:
        provided = request.headers.get("X-Nexus-Key", "")
        if provided != api_key:
            return jsonify({"ok": False, "error": "Invalid or missing API key"}), 401

    data = request.get_json(force=True, silent=True) or {}
    hostname = str(data.get("hostname", "")).strip()
    ip = str(data.get("ip", "")).strip()
    if not hostname or not ip:
        return jsonify({"ok": False, "error": "hostname and ip required"}), 400

    node_kwargs = {"ip_primary": ip, "status": "online"}
    for field in ("os_family", "arch", "device_type", "agent_version"):
        val = data.get(field)
        if val:
            node_kwargs[field] = str(val).strip()

    node_id = mdb.upsert_fleet_node(hostname, **node_kwargs)

    _resolve_fabric_membership(node_id, ip)

    tools = data.get("tools") or []
    for t in tools:
        name = str(t.get("name", "")).strip()
        port = int(t.get("port", 0))
        if not name or not port:
            continue
        mdb.upsert_tool_instance(
            node_id, name, port,
            version=str(t.get("version", "")),
            status=str(t.get("status", "online")),
            proto="https",
        )

    fm = current_app.config.get("fabric_manager")
    if fm and hasattr(fm, "socketio") and fm.socketio:
        try:
            fm.socketio.emit("fleet_update", {"type": "checkin", "node_id": node_id})
        except Exception:
            pass

    interval = int(mdb.get_setting("discovery_interval_s", "300"))
    return jsonify({"ok": True, "node_id": node_id, "next_checkin": interval})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_fabric_membership(node_id: int, ip_str: str) -> None:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return
    fabrics = mdb.get_fabrics(enabled_only=True)
    for fab in fabrics:
        try:
            subnets = json.loads(fab.get("subnets", "[]"))
        except (json.JSONDecodeError, TypeError):
            continue
        for subnet_str in subnets:
            try:
                net = ipaddress.ip_network(subnet_str, strict=False)
            except ValueError:
                continue
            if addr in net:
                mdb.upsert_node_address(node_id, fab["id"], ip_str, lane_status="up")
                break


def _last_scan_iso() -> str | None:
    fd = current_app.config.get("fleet_discovery")
    if fd and hasattr(fd, "last_scan") and fd.last_scan:
        return fd.last_scan.isoformat()
    return None
