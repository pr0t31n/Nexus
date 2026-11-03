"""Nexus v2.1 — Admin routes: settings, audit log."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from models import db as mdb

admin_bp = Blueprint("admin", __name__)


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------

@admin_bp.route("/api/settings", methods=["GET"])
def api_settings():
    settings = mdb.get_all_settings()
    return jsonify({"settings": settings})


@admin_bp.route("/api/settings", methods=["POST"])
def api_settings_update():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    payload = data.get("settings", data)
    updated = []
    for key, value in payload.items():
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
# Fleet stats API (sidebar counters)
# ---------------------------------------------------------------------------

@admin_bp.route("/api/fleet/stats")
def api_fleet_stats():
    return jsonify(mdb.fleet_stats())


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------

@admin_bp.route("/settings")
def settings_page():
    return render_template("settings.html")
