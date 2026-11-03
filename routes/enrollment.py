"""Nexus v2.4 — Enrollment routes: agent download + node enrollment + broker status."""
from __future__ import annotations

import json
import os
import platform
import threading
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file, session

from models import db as mdb

enrollment_bp = Blueprint("enrollment", __name__)

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent_dist"

_BINARY_MAP = {
    ("linux", "x86_64"):   "nexus-agent-linux-x86_64",
    ("linux", "aarch64"):  "nexus-agent-linux-aarch64",
    ("linux", "armv7l"):   "nexus-agent-linux-armv7l",
    ("darwin", "x86_64"):  "nexus-agent-darwin-x86_64",
    ("darwin", "arm64"):   "nexus-agent-darwin-arm64",
    ("darwin", "aarch64"): "nexus-agent-darwin-arm64",
    ("windows", "x86_64"): "nexus-agent-windows-x86_64.exe",
    ("freebsd", "x86_64"): "nexus-agent-freebsd-x86_64",
}


# ---------------------------------------------------------------------------
# Agent binary download
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/agent/download")
def api_agent_download():
    os_name = request.args.get("os", "").strip().lower()
    arch = request.args.get("arch", "").strip().lower()
    if not os_name or not arch:
        return jsonify({"error": "os and arch query params required"}), 400

    binary_name = _BINARY_MAP.get((os_name, arch))
    if not binary_name:
        return jsonify({
            "error": f"No agent binary for os={os_name} arch={arch}",
            "available": sorted(f"{k[0]}/{k[1]}" for k in _BINARY_MAP),
        }), 404

    binary_path = _AGENT_DIR / binary_name
    if not binary_path.is_file():
        return jsonify({"error": f"Binary not built yet: {binary_name}"}), 404

    return send_file(
        binary_path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=binary_name,
    )


@enrollment_bp.route("/api/agent/binaries")
def api_agent_binaries():
    available = []
    for key, fname in sorted(_BINARY_MAP.items()):
        p = _AGENT_DIR / fname
        available.append({
            "os": key[0],
            "arch": key[1],
            "filename": fname,
            "built": p.is_file(),
            "size": p.stat().st_size if p.is_file() else 0,
        })
    return jsonify({"binaries": available, "dist_dir": str(_AGENT_DIR)})


# ---------------------------------------------------------------------------
# GET /api/enrollment/status — combined enrollment + agent connection status
# (Consumed by enrollment.js)
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/enrollment/status")
def api_enrollment_status_combined():
    nodes = mdb.get_fleet_nodes()
    broker = current_app.config.get("dispatch_broker")

    connected_set = set()
    agent_info = {}
    if broker:
        for a in broker.get_connected_agents():
            nid = a.get("node_id")
            if nid is not None:
                connected_set.add(nid)
                agent_info[nid] = a

    enrollments = []
    for n in nodes:
        nid = n["id"]
        conn = nid in connected_set
        ai = agent_info.get(nid, {})
        enrollments.append({
            "node_id": nid,
            "hostname": n.get("hostname", ""),
            "enrolled": bool(n.get("enrolled")),
            "agent_connected": conn,
            "agent_version": ai.get("agent_version") or n.get("agent_version", ""),
            "os": ai.get("os_family") or n.get("os_family", ""),
            "arch": ai.get("arch") or n.get("arch", ""),
            "last_seen": n.get("last_seen", ""),
            "ip_primary": n.get("ip_primary", ""),
        })

    return jsonify({"enrollments": enrollments})


# ---------------------------------------------------------------------------
# GET /api/broker/status — broker running state + connected agents
# (Consumed by enrollment.js)
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/broker/status")
def api_broker_status():
    broker = current_app.config.get("dispatch_broker")
    if not broker:
        return jsonify({"running": False, "connected_count": 0, "agents": []})

    agents_raw = broker.get_connected_agents()
    agents = []
    for a in agents_raw:
        agents.append({
            "agent_id": a.get("agent_id", ""),
            "hostname": a.get("hostname", ""),
            "node_id": a.get("node_id"),
            "os_family": a.get("os_family", ""),
            "arch": a.get("arch", ""),
            "agent_version": a.get("agent_version", ""),
            "alive": (a.get("last_heartbeat_ago", 999) < 90),
            "last_heartbeat": f"{a.get('last_heartbeat_ago', 0)}s ago",
        })

    return jsonify({
        "running": True,
        "connected_count": len(agents),
        "agents": agents,
    })


# ---------------------------------------------------------------------------
# POST /api/enrollment/issue — issue a broker CA cert for a hostname
# (Consumed by enrollment.js)
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/enrollment/issue", methods=["POST"])
def api_enrollment_issue():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403

    data = request.get_json(force=True, silent=True) or {}
    hostname = str(data.get("hostname", "")).strip()
    if not hostname:
        return jsonify({"ok": False, "error": "hostname required"}), 400

    try:
        from services.broker_ca import issue_agent_cert
        from config import NEXUS_DIR
        cert_dir = NEXUS_DIR / "certs"
        cert_path, key_path = issue_agent_cert(cert_dir, hostname)
    except ImportError:
        return jsonify({"ok": False, "error": "Broker CA not available"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:500]}), 500

    mdb.audit("enrollment_issue", f"Issued agent cert for {hostname}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")

    return jsonify({
        "ok": True,
        "hostname": hostname,
        "cert_path": str(cert_path),
        "key_path": str(key_path),
    })


# ---------------------------------------------------------------------------
# POST /api/enrollment/revoke — mark a node as unenrolled
# (Consumed by enrollment.js)
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/enrollment/revoke", methods=["POST"])
def api_enrollment_revoke():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403

    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"ok": False, "error": "node_id required"}), 400

    node = mdb.get_fleet_node(int(node_id))
    if not node:
        return jsonify({"ok": False, "error": "Node not found"}), 404

    mdb.update_fleet_node(int(node_id), enrolled=0)

    mdb.audit("enrollment_revoke", f"Revoked enrollment for {node['hostname']}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")

    return jsonify({"ok": True, "node_id": int(node_id)})


# ---------------------------------------------------------------------------
# Enrollment — enroll a node into fabric(s)
# ---------------------------------------------------------------------------

@enrollment_bp.route("/api/fleet/enroll", methods=["POST"])
def api_fleet_enroll():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403

    data = request.get_json(force=True, silent=True) or {}
    node_id = data.get("node_id")
    fabric_ids = data.get("fabric_ids", [])
    if not node_id:
        return jsonify({"ok": False, "error": "node_id required"}), 400

    node = mdb.get_fleet_node(int(node_id))
    if not node:
        return jsonify({"ok": False, "error": "Node not found"}), 404

    if not fabric_ids:
        return jsonify({"ok": False, "error": "At least one fabric_id required"}), 400

    job_id = mdb.create_enrollment_job(
        node_id=int(node_id),
        fabric_ids=json.dumps(fabric_ids),
        requested_by=session.get("username", "system"),
    )

    fm = current_app.config.get("fabric_manager")
    broker = current_app.config.get("dispatch_broker")

    def _run_enrollment():
        mdb.update_enrollment_job(job_id, status="running")
        results = []
        for fid in fabric_ids:
            fab = mdb.get_fabric(int(fid))
            if not fab:
                results.append({"fabric_id": fid, "ok": False, "error": "Fabric not found"})
                continue

            adapter = None
            if fm:
                adapter = fm.get_adapter_for_fabric(fab["fabric_type"])

            if adapter and hasattr(adapter, "enroll_node"):
                try:
                    config = _resolve_fabric_config(fab)
                    r = adapter.enroll_node(config, node)
                    results.append({"fabric_id": fid, "ok": r.get("ok", False),
                                    "detail": r.get("detail", "")})
                    if r.get("ok"):
                        addr = r.get("address", "")
                        if addr:
                            mdb.upsert_node_address(int(node_id), int(fid), addr, lane_status="up")
                except Exception as e:
                    results.append({"fabric_id": fid, "ok": False, "error": str(e)[:500]})
            else:
                mdb.upsert_node_address(int(node_id), int(fid),
                                        node.get("ip_primary", ""),
                                        lane_status="pending")
                results.append({"fabric_id": fid, "ok": True,
                                "detail": "Registered (manual enrollment required)"})

        all_ok = all(r.get("ok") for r in results)
        mdb.update_enrollment_job(
            job_id,
            status="complete" if all_ok else "partial",
            result=json.dumps(results),
        )

        if all_ok:
            mdb.update_fleet_node(int(node_id), enrolled=1)

        sio = current_app.extensions.get("socketio")
        if sio:
            sio.emit("enrollment_update", {
                "job_id": job_id, "node_id": node_id,
                "status": "complete" if all_ok else "partial",
                "results": results,
            })

    threading.Thread(target=_run_enrollment, daemon=True,
                     name=f"enroll-{node_id}").start()

    mdb.audit("enrollment_start",
              f"Enrolling node {node['hostname']} into fabrics {fabric_ids}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")

    return jsonify({"ok": True, "job_id": job_id})


@enrollment_bp.route("/api/fleet/enroll/<int:job_id>")
def api_enrollment_job_status(job_id: int):
    job = mdb.get_enrollment_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@enrollment_bp.route("/api/fleet/enrollments")
def api_enrollments():
    limit = min(int(request.args.get("limit", 50)), 200)
    jobs = mdb.get_enrollment_jobs(limit=limit)
    return jsonify({"jobs": jobs})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_fabric_config(fab: dict) -> dict:
    config = {}
    try:
        config = json.loads(fab.get("config", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    enc = fab.get("config_enc", "")
    if enc:
        try:
            from services import credential_store
            secret = json.loads(credential_store.decrypt(enc))
            config.update(secret)
        except Exception:
            pass
    return config
