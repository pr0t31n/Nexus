"""Nexus v2.4 — Dispatch routes: Crucible fleet API for tool dispatch."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request, session

from models import db as mdb
from config import TOOL_REGISTRY

dispatch_bp = Blueprint("dispatch", __name__)


# ---------------------------------------------------------------------------
# POST /api/fleet/dispatch — dispatch a tool run to best appliance
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/dispatch", methods=["POST"])
def api_fleet_dispatch():
    data = request.get_json(force=True, silent=True) or {}
    tool = str(data.get("tool", "")).strip().lower()
    args = data.get("args", {})
    scope = str(data.get("scope", "")).strip()
    node_id = data.get("node_id")
    timeout = int(data.get("timeout", 0)) or 300

    if not tool:
        return jsonify({"ok": False, "error": "tool required"}), 400

    if node_id:
        node = mdb.get_fleet_node(int(node_id))
        if not node:
            return jsonify({"ok": False, "error": "Node not found"}), 404
    else:
        node = _select_best_appliance(tool, scope)
        if not node:
            return jsonify({"ok": False, "error": f"No online appliance with {tool}"}), 404
        node_id = node["id"]

    tools_on_node = mdb.get_tool_instances(int(node_id))
    tool_inst = next((t for t in tools_on_node if t["tool_name"] == tool), None)
    if not tool_inst:
        return jsonify({"ok": False, "error": f"Tool '{tool}' not found on node {node['hostname']}"}), 404

    job_id = mdb.create_dispatch_job(
        node_id=int(node_id),
        tool=tool,
        args=json.dumps(args),
        scope=scope,
        requested_by=session.get("username", "api"),
    )

    fm = current_app.config.get("fabric_manager")
    fabric_path = None
    if fm and hasattr(fm, "resolve_dispatch_path"):
        fabric_path = fm.resolve_dispatch_path(int(node_id))

    user_sid = getattr(request, "sid", "") or ""

    def _run_dispatch():
        mdb.update_dispatch_job(job_id, status="running",
                                started_at=datetime.now(timezone.utc).isoformat())

        broker = current_app.config.get("dispatch_broker")
        if not broker:
            mdb.update_dispatch_job(
                job_id, status="failed",
                output="No transport available (broker not initialised)",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            _emit_update(job_id)
            return

        raw_args = ""
        if isinstance(args, dict):
            raw_args = args.get("raw", "")
        elif isinstance(args, str):
            raw_args = args
        cmd = f"{tool} {raw_args}".strip() if raw_args else tool

        try:
            result = broker.dispatch_command(
                node_id=int(node_id),
                cmd=cmd,
                timeout=timeout,
                dispatch_id=str(job_id),
                user_sid=user_sid,
            )

            if not result.get("ok"):
                mdb.update_dispatch_job(
                    job_id, status="failed",
                    output=result.get("error", "No agent connected for this node"),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                _emit_update(job_id)
                return

            cmd_id = result.get("cmd_id", "")
            mdb.update_dispatch_job(job_id, broker_cmd_id=cmd_id)

        except Exception as e:
            mdb.update_dispatch_job(
                job_id, status="failed",
                output=str(e)[:2000],
                finished_at=datetime.now(timezone.utc).isoformat(),
            )

        _emit_update(job_id)

    threading.Thread(target=_run_dispatch, daemon=True,
                     name=f"dispatch-{job_id}").start()

    mdb.audit("dispatch", f"Dispatched {tool} to {node['hostname']}",
              username=session.get("username", "api"),
              ip=request.remote_addr or "")

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "node_id": node_id,
        "node_hostname": node.get("hostname", ""),
        "fabric_path": fabric_path,
    })


def _emit_update(job_id: int):
    sio = current_app.extensions.get("socketio")
    if sio:
        job = mdb.get_dispatch_job(job_id)
        sio.emit("dispatch_update", job)


# ---------------------------------------------------------------------------
# GET /api/fleet/dispatch/<id> — poll status + output
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/dispatch/<int:job_id>")
def api_dispatch_status(job_id: int):
    job = mdb.get_dispatch_job(job_id)
    if not job:
        return jsonify({"error": "Dispatch job not found"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------------
# POST /api/fleet/dispatch/<id>/stop — cancel dispatch
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/dispatch/<int:job_id>/stop", methods=["POST"])
def api_dispatch_stop(job_id: int):
    job = mdb.get_dispatch_job(job_id)
    if not job:
        return jsonify({"error": "Dispatch job not found"}), 404

    if job["status"] not in ("pending", "running"):
        return jsonify({"ok": False, "error": f"Job already {job['status']}"}), 400

    broker = current_app.config.get("dispatch_broker")
    cmd_id = job.get("broker_cmd_id", "")
    if broker and cmd_id:
        broker.cancel_command(cmd_id)

    mdb.update_dispatch_job(job_id, status="cancelled",
                            finished_at=datetime.now(timezone.utc).isoformat())

    mdb.audit("dispatch_cancel", f"Cancelled dispatch job {job_id}",
              username=session.get("username", "api"),
              ip=request.remote_addr or "")

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# GET /api/fleet/appliances — available appliances with reachability
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/appliances")
def api_fleet_appliances():
    nodes = mdb.get_fleet_nodes(status="online")
    appliances = []
    for n in nodes:
        tools = mdb.get_tool_instances(n["id"])
        if not tools:
            continue
        addresses = mdb.get_node_addresses(n["id"])
        appliances.append({
            "id": n["id"],
            "hostname": n["hostname"],
            "display_name": n.get("display_name") or n["hostname"],
            "ip_primary": n.get("ip_primary", ""),
            "os_family": n.get("os_family", ""),
            "arch": n.get("arch", ""),
            "device_type": n.get("device_type", ""),
            "status": n["status"],
            "enrolled": bool(n.get("enrolled")),
            "agent_version": n.get("agent_version", ""),
            "tools": [{"name": t["tool_name"], "port": t["port"],
                        "version": t.get("version", ""), "status": t["status"]}
                       for t in tools],
            "fabrics": [{"fabric_id": a["fabric_id"],
                          "fabric_name": a.get("fabric_name", ""),
                          "address": a["address"],
                          "lane_status": a.get("lane_status", "unknown")}
                         for a in addresses],
        })
    return jsonify({"appliances": appliances})


# ---------------------------------------------------------------------------
# GET /api/fleet/appliances/<id>/tools — tools on specific appliance
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/appliances/<int:node_id>/tools")
def api_appliance_tools(node_id: int):
    node = mdb.get_fleet_node(node_id)
    if not node:
        return jsonify({"error": "Node not found"}), 404
    tools = mdb.get_tool_instances(node_id)
    return jsonify({
        "node_id": node_id,
        "hostname": node["hostname"],
        "tools": [{"name": t["tool_name"], "port": t["port"],
                    "version": t.get("version", ""), "status": t["status"],
                    "scanning": bool(t.get("scanning")), "last_seen": t.get("last_seen", "")}
                   for t in tools],
    })


# ---------------------------------------------------------------------------
# GET /api/fleet/dispatch/jobs — list recent dispatch jobs
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/fleet/dispatch/jobs")
def api_dispatch_jobs():
    limit = min(int(request.args.get("limit", 50)), 200)
    jobs = mdb.get_dispatch_jobs(limit=limit)
    return jsonify({"jobs": jobs})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select_best_appliance(tool: str, scope: str = "") -> dict | None:
    nodes = mdb.get_fleet_nodes(status="online")
    candidates = []
    for n in nodes:
        tools = mdb.get_tool_instances(n["id"])
        has_tool = any(t["tool_name"] == tool and t["status"] == "online" for t in tools)
        if not has_tool:
            continue
        is_scanning = any(t.get("scanning") for t in tools if t["tool_name"] == tool)
        addresses = mdb.get_node_addresses(n["id"])
        healthy_lanes = sum(1 for a in addresses if a.get("lane_status") == "up")
        candidates.append((n, healthy_lanes, not is_scanning))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[2], x[1]), reverse=True)
    return candidates[0][0]
