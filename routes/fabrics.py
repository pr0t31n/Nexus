"""Nexus v2.1 — Fabric routes: CRUD, test, health, types."""
from __future__ import annotations

import ipaddress
import json

from flask import Blueprint, current_app, jsonify, request, session

from models import db as mdb
from config import FABRIC_TYPES, FABRIC_TYPE_FIELDS

fabrics_bp = Blueprint("fabrics", __name__)


# ---------------------------------------------------------------------------
# List all fabrics
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics")
def api_fabrics():
    fabrics = mdb.get_fabrics()
    for f in fabrics:
        f["node_count"] = mdb.get_fabric_node_count(f["id"])
    return jsonify({"fabrics": fabrics})


# ---------------------------------------------------------------------------
# Create fabric (Add Network wizard)
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics", methods=["POST"])
def api_fabric_create():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", "")).strip()
    ftype = str(data.get("fabric_type", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    if not ftype or not _valid_fabric_type(ftype):
        return jsonify({"ok": False, "error": f"Invalid fabric_type: {ftype}"}), 400

    subnets = data.get("subnets", [])
    err = _validate_subnets(subnets)
    if err:
        return jsonify({"ok": False, "error": err}), 400

    existing = mdb.get_conn().execute(
        "SELECT id FROM network_fabrics WHERE name = ?", (name,)
    ).fetchone()
    if existing:
        return jsonify({"ok": False, "error": f"Fabric '{name}' already exists"}), 409

    config = data.get("config", {})
    config_public, config_enc = _split_config(ftype, config)

    fid = mdb.create_fabric(
        name=name,
        fabric_type=ftype,
        subnets=json.dumps(subnets),
        config=json.dumps(config_public),
        config_enc=_encrypt_config(config_enc),
        priority=int(data.get("priority", 100)),
        probe_interval=int(data.get("probe_interval", 300)),
    )
    mdb.audit("fabric_create", f"Created fabric '{name}' ({ftype})",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "id": fid})


# ---------------------------------------------------------------------------
# Get single fabric (masks secrets)
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/<int:fabric_id>")
def api_fabric(fabric_id: int):
    f = mdb.get_fabric(fabric_id)
    if not f:
        return jsonify({"error": "Fabric not found"}), 404
    f["node_count"] = mdb.get_fabric_node_count(fabric_id)
    f.pop("config_enc", None)
    return jsonify(f)


# ---------------------------------------------------------------------------
# Update fabric
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/<int:fabric_id>", methods=["PUT"])
def api_fabric_update(fabric_id: int):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    f = mdb.get_fabric(fabric_id)
    if not f:
        return jsonify({"error": "Fabric not found"}), 404
    data = request.get_json(force=True, silent=True) or {}

    updates = {}
    if "name" in data:
        new_name = str(data["name"]).strip()
        if new_name and new_name != f["name"]:
            dup = mdb.get_conn().execute(
                "SELECT id FROM network_fabrics WHERE name = ? AND id != ?",
                (new_name, fabric_id),
            ).fetchone()
            if dup:
                return jsonify({"ok": False, "error": f"Name '{new_name}' already in use"}), 409
            updates["name"] = new_name

    if "subnets" in data:
        err = _validate_subnets(data["subnets"])
        if err:
            return jsonify({"ok": False, "error": err}), 400
        updates["subnets"] = json.dumps(data["subnets"])

    if "config" in data:
        config_public, config_enc = _split_config(f["fabric_type"], data["config"])
        updates["config"] = json.dumps(config_public)
        updates["config_enc"] = _encrypt_config(config_enc)

    for key in ("priority", "probe_interval", "enabled"):
        if key in data:
            updates[key] = data[key]

    if updates:
        mdb.update_fabric(fabric_id, **updates)

    mdb.audit("fabric_update", f"Updated fabric '{f['name']}'",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Delete fabric
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/<int:fabric_id>", methods=["DELETE"])
def api_fabric_delete(fabric_id: int):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    f = mdb.get_fabric(fabric_id)
    if not f:
        return jsonify({"error": "Fabric not found"}), 404
    mdb.delete_fabric(fabric_id)
    mdb.audit("fabric_delete", f"Deleted fabric '{f['name']}'",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Test connection (wizard step 3)
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/test", methods=["POST"])
def api_fabric_test():
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    data = request.get_json(force=True, silent=True) or {}
    ftype = str(data.get("fabric_type", "")).strip()
    config = data.get("config", {})
    if not ftype or not _valid_fabric_type(ftype):
        return jsonify({"ok": False, "error": f"Invalid fabric_type: {ftype}"}), 400

    fm = current_app.config.get("fabric_manager")
    if fm:
        result = fm.test_fabric_connection(ftype, config)
        return jsonify(result)

    return jsonify({
        "ok": True,
        "checks": [{"label": "Adapter not loaded", "passed": False,
                     "detail": f"No adapter for {ftype} (Phase 2 ships 3 adapters)"}],
    })


# ---------------------------------------------------------------------------
# Trigger immediate health check on a fabric
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/<int:fabric_id>/health", methods=["POST"])
def api_fabric_health(fabric_id: int):
    f = mdb.get_fabric(fabric_id)
    if not f:
        return jsonify({"error": "Fabric not found"}), 404
    fm = current_app.config.get("fabric_manager")
    if fm:
        result = fm.check_fabric_health(fabric_id)
        return jsonify(result)
    return jsonify({"ok": False, "error": "Fabric manager not initialised"}), 503


# ---------------------------------------------------------------------------
# Enable/disable fabric toggle
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabrics/<int:fabric_id>/toggle", methods=["POST"])
def api_fabric_toggle(fabric_id: int):
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    f = mdb.get_fabric(fabric_id)
    if not f:
        return jsonify({"error": "Fabric not found"}), 404
    new_state = not bool(f.get("enabled", 1))
    mdb.toggle_fabric(fabric_id, new_state)
    action = "enabled" if new_state else "disabled"
    mdb.audit("fabric_toggle", f"Fabric '{f['name']}' {action}",
              username=session.get("username", "system"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "enabled": new_state})


# ---------------------------------------------------------------------------
# Fabric types (for Add Network wizard step 1)
# ---------------------------------------------------------------------------

@fabrics_bp.route("/api/fabric-types")
def api_fabric_types():
    fm = current_app.config.get("fabric_manager")
    if fm and hasattr(fm, "get_fabric_types"):
        return jsonify({"types": fm.get_fabric_types()})

    types = []
    for category, type_ids in FABRIC_TYPES.items():
        for tid in type_ids:
            entry = {
                "type_id": tid,
                "category": category,
                "display_name": _type_display_name(tid),
                "fields": FABRIC_TYPE_FIELDS.get(tid, []),
                "has_adapter": tid in _PHASE2_ADAPTERS,
            }
            types.append(entry)
    return jsonify({"types": types})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PHASE2_ADAPTERS = {"openvpn_cloak", "amneziawg", "direct_lan"}


def _valid_fabric_type(ftype: str) -> bool:
    for types in FABRIC_TYPES.values():
        if ftype in types:
            return True
    return False


def _validate_subnets(subnets: list) -> str | None:
    if not isinstance(subnets, list):
        return "subnets must be a list"
    for s in subnets:
        try:
            ipaddress.ip_network(str(s), strict=False)
        except ValueError:
            return f"Invalid CIDR: {s}"
    return None


def _type_display_name(tid: str) -> str:
    names = {
        "openvpn_cloak": "OpenVPN + Cloak",
        "amneziawg": "AmneziaWG",
        "wireguard": "WireGuard",
        "ipsec_ikev2": "IPSec / IKEv2",
        "openvpn": "OpenVPN",
        "cloudflare_tunnel": "Cloudflare Tunnel",
        "tailscale": "Tailscale",
        "headscale": "Headscale",
        "zerotier": "ZeroTier",
        "nebula": "Nebula",
        "netbird": "NetBird",
        "netmaker": "Netmaker",
        "aws_vpc": "AWS VPC",
        "azure_vnet": "Azure VNet",
        "gcp_vpc": "GCP VPC",
        "linode_vpc": "Linode VPC",
        "digitalocean_vpc": "DigitalOcean VPC",
        "direct_lan": "Direct LAN",
        "proxmox": "Proxmox",
    }
    return names.get(tid, tid.replace("_", " ").title())


def _split_config(ftype: str, config: dict) -> tuple[dict, dict]:
    secret_fields = set()
    for fd in FABRIC_TYPE_FIELDS.get(ftype, []):
        if fd.get("secret"):
            secret_fields.add(fd["name"])
    public = {k: v for k, v in config.items() if k not in secret_fields}
    secret = {k: v for k, v in config.items() if k in secret_fields}
    return public, secret


def _encrypt_config(secret_config: dict) -> str:
    if not secret_config:
        return ""
    try:
        from services import credential_store
        return credential_store.encrypt(json.dumps(secret_config))
    except Exception:
        return json.dumps(secret_config)
