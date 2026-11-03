"""Nexus v2.5 — Audit & monitoring routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session
from models import db as mdb

audit_bp = Blueprint("audit", __name__)


def _build_filters(args, extra_keys=None):
    f = {}
    v = args.get("from")
    if v:
        f["from_ts"] = v
    v = args.get("to")
    if v:
        f["to_ts"] = v
    v = args.get("search")
    if v:
        f["search"] = v
    for k in (extra_keys or []):
        v = args.get(k)
        if v:
            f[k] = v
    return f


def _int(val, default):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ───────────────────────────────── Sessions ──────────────────────────────────

@audit_bp.route("/api/audit/sessions")
def api_sessions():
    filters = _build_filters(request.args,
                             ["method", "source_ip", "auth_result", "username"])
    limit = _int(request.args.get("limit"), 50)
    offset = _int(request.args.get("offset"), 0)
    rows = mdb.get_sessions(limit, offset, filters)
    total = mdb.count_sessions(filters)
    return jsonify({"sessions": rows, "total": total})


@audit_bp.route("/api/audit/sessions/<int:sid>")
def api_session_detail(sid):
    row = mdb.get_session(sid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row)


# ──────────────────────────────── Ports/Services ─────────────────────────────

@audit_bp.route("/api/audit/ports")
def api_ports():
    filters = _build_filters(request.args, ["is_open", "whitelisted", "protocol"])
    limit = _int(request.args.get("limit"), 100)
    offset = _int(request.args.get("offset"), 0)
    rows = mdb.get_service_ports(limit, offset, filters)
    total = mdb.count_service_ports(filters)
    return jsonify({"ports": rows, "total": total})


@audit_bp.route("/api/audit/ports/<int:pid>")
def api_port_detail(pid):
    row = mdb.get_service_port(pid)
    if not row:
        return jsonify({"error": "Not found"}), 404
    return jsonify(row)


@audit_bp.route("/api/audit/ports/<int:pid>/whitelist", methods=["POST"])
def api_whitelist_port(pid):
    data = request.get_json(force=True, silent=True) or {}
    reason = str(data.get("reason", "")).strip()
    username = session.get("username", "system")
    mdb.whitelist_port(pid, by=username, reason=reason)
    mdb.audit("whitelist_port", f"Port {pid} whitelisted: {reason}",
              username=username, ip=request.remote_addr or "")
    return jsonify({"ok": True})


@audit_bp.route("/api/audit/ports/<int:pid>/unwhitelist", methods=["POST"])
def api_unwhitelist_port(pid):
    username = session.get("username", "system")
    mdb.unwhitelist_port(pid)
    mdb.audit("unwhitelist_port", f"Port {pid} unwhitelisted",
              username=username, ip=request.remote_addr or "")
    return jsonify({"ok": True})


@audit_bp.route("/api/audit/ports/<int:pid>/notes", methods=["POST"])
def api_update_port_notes(pid):
    data = request.get_json(force=True, silent=True) or {}
    notes = str(data.get("notes", ""))
    mdb.update_port_notes(pid, notes)
    return jsonify({"ok": True})


# ──────────────────────────────── Audit Log ──────────────────────────────────

@audit_bp.route("/api/audit/log")
def api_audit_log():
    filters = _build_filters(request.args,
                             ["event_type", "severity", "username", "action"])
    limit = _int(request.args.get("limit"), 100)
    offset = _int(request.args.get("offset"), 0)
    rows = mdb.get_audit_log_v2(limit, offset, filters)
    total = mdb.count_audit_log(filters)
    return jsonify({"entries": rows, "total": total})


# ──────────────────────────────── Node History ───────────────────────────────

@audit_bp.route("/api/audit/node-history")
def api_node_history_all():
    filters = _build_filters(request.args, ["node_id", "event_type"])
    limit = _int(request.args.get("limit"), 100)
    offset = _int(request.args.get("offset"), 0)
    rows = mdb.get_all_node_history(limit, offset, filters)
    total = mdb.count_node_history(filters)
    return jsonify({"events": rows, "total": total})


@audit_bp.route("/api/audit/node-history/<node_id>")
def api_node_history_detail(node_id):
    filters = _build_filters(request.args, ["event_type"])
    filters["node_id"] = node_id
    limit = _int(request.args.get("limit"), 100)
    offset = _int(request.args.get("offset"), 0)
    rows = mdb.get_node_history(node_id, limit, offset, filters)
    return jsonify({"events": rows})


# ──────────────────────────────── Stats ──────────────────────────────────────

@audit_bp.route("/api/audit/stats")
def api_audit_stats():
    conn = mdb.get_conn()
    active_sessions = conn.execute(
        "SELECT COUNT(*) FROM remote_sessions WHERE timestamp_end IS NULL"
    ).fetchone()[0]
    open_ports = conn.execute(
        "SELECT COUNT(*) FROM service_ports WHERE is_open = 1"
    ).fetchone()[0]
    whitelisted = conn.execute(
        "SELECT COUNT(*) FROM service_ports WHERE whitelisted = 1"
    ).fetchone()[0]
    unexpected = conn.execute(
        "SELECT COUNT(*) FROM service_ports "
        "WHERE is_open = 1 AND whitelisted = 0 AND "
        "(arsenal_tool_match IS NULL OR arsenal_tool_match = '')"
    ).fetchone()[0]
    failed_auth_24h = conn.execute(
        "SELECT COUNT(*) FROM remote_sessions "
        "WHERE auth_result != 'success' "
        "AND timestamp_start > datetime('now', '-1 day')"
    ).fetchone()[0]
    audit_entries_24h = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE ts > datetime('now', '-1 day')"
    ).fetchone()[0]
    return jsonify({
        "active_sessions": active_sessions,
        "open_ports": open_ports,
        "whitelisted_ports": whitelisted,
        "unexpected_ports": unexpected,
        "failed_auth_24h": failed_auth_24h,
        "audit_entries_24h": audit_entries_24h,
    })
