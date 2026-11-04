"""Nexus v2.6 — Custom services routes: CRUD + webhook receiver."""
from __future__ import annotations

import hashlib
import hmac
import re
import socket

import requests
from flask import Blueprint, jsonify, request, session
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from models import db as mdb
from services import credential_store

custom_services_bp = Blueprint("custom_services", __name__)

_VALID_INTEGRATIONS = ("tcp_only", "api_poll", "webhook")
_VALID_AUTH_TYPES = ("none", "api_key", "bearer", "basic", "custom_header")
_VALID_RESPONSE_TYPES = ("json", "text", "status_code")
_SID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}$")

_PASSTHROUGH_FIELDS = (
    "proto", "description", "icon", "integration",
    "api_endpoint", "api_method", "api_auth_type", "api_auth_header",
    "api_auth_username", "api_response_type", "api_status_path",
    "api_scanning_value", "api_verify_tls", "poll_interval_s",
    "webhook_sig_header", "webhook_payload_path", "webhook_scanning_value",
)

_SENSITIVE_KEYS = ("api_auth_value_enc", "webhook_secret_enc")


def _require_auth():
    if not session.get("authenticated"):
        return jsonify({"ok": False, "error": "Authentication required"}), 401
    return None


def _require_admin():
    err = _require_auth()
    if err:
        return err
    if session.get("role") != "admin":
        return jsonify({"ok": False, "error": "Admin required"}), 403
    return None


def _extract_json_path(data, path: str):
    if not path:
        return data
    for key in path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def _sanitize_for_response(svc: dict) -> dict:
    out = dict(svc)
    for k in _SENSITIVE_KEYS:
        val = out.pop(k, "")
        label = k.replace("_enc", "")
        out[label + "_configured"] = bool(val)
    return out


def _build_auth_kwargs(data: dict, existing: dict | None = None) -> dict:
    kwargs = {}
    raw_auth = data.get("api_auth_value", "")
    if raw_auth:
        kwargs["api_auth_value_enc"] = credential_store.encrypt(raw_auth)
    elif existing and "api_auth_value" in data and raw_auth == "":
        pass

    raw_secret = data.get("webhook_secret", "")
    if raw_secret:
        kwargs["webhook_secret_enc"] = credential_store.encrypt(raw_secret)
    elif existing and "webhook_secret" in data and raw_secret == "":
        pass

    return kwargs


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@custom_services_bp.route("/api/custom-services", methods=["GET"])
def api_list():
    err = _require_auth()
    if err:
        return err
    svcs = mdb.get_custom_services()
    return jsonify({"services": [_sanitize_for_response(s) for s in svcs]})


@custom_services_bp.route("/api/custom-services", methods=["POST"])
def api_create():
    err = _require_admin()
    if err:
        return err
    data = request.get_json(force=True, silent=True) or {}

    service_id = str(data.get("service_id", "")).strip().lower()
    name = str(data.get("name", "")).strip()
    port = data.get("port")
    integration = str(data.get("integration", "tcp_only")).strip()

    if not service_id or not _SID_RE.match(service_id):
        return jsonify({"ok": False, "error": "service_id must be lowercase alphanumeric + hyphens, 1-63 chars"}), 400
    if not name:
        return jsonify({"ok": False, "error": "name is required"}), 400
    try:
        port = int(port)
        if port < 1 or port > 65535:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "port must be 1-65535"}), 400
    if integration not in _VALID_INTEGRATIONS:
        return jsonify({"ok": False, "error": f"integration must be one of: {', '.join(_VALID_INTEGRATIONS)}"}), 400

    if mdb.get_custom_service_by_sid(service_id):
        return jsonify({"ok": False, "error": f"service_id '{service_id}' already exists"}), 409

    kwargs = {}
    for field in _PASSTHROUGH_FIELDS:
        if field in data:
            val = data[field]
            if field == "api_verify_tls":
                val = 1 if val else 0
            elif field == "poll_interval_s":
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    val = 0
            else:
                val = str(val).strip()
            kwargs[field] = val

    kwargs.update(_build_auth_kwargs(data))

    svc_id = mdb.create_custom_service(service_id, name, port, **kwargs)
    mdb.audit("custom_service_create", f"Created custom service: {name} ({service_id}:{port})",
              username=session.get("username", "unknown"),
              ip=request.remote_addr or "")
    svc = mdb.get_custom_service(svc_id)
    return jsonify({"ok": True, "service": _sanitize_for_response(svc)}), 201


@custom_services_bp.route("/api/custom-services/<int:svc_id>", methods=["PUT"])
def api_update(svc_id: int):
    err = _require_admin()
    if err:
        return err
    existing = mdb.get_custom_service(svc_id)
    if not existing:
        return jsonify({"ok": False, "error": "Not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    kwargs = {}

    if "name" in data:
        name = str(data["name"]).strip()
        if not name:
            return jsonify({"ok": False, "error": "name cannot be empty"}), 400
        kwargs["name"] = name

    if "port" in data:
        try:
            port = int(data["port"])
            if port < 1 or port > 65535:
                raise ValueError
            kwargs["port"] = port
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "port must be 1-65535"}), 400

    if "integration" in data:
        integration = str(data["integration"]).strip()
        if integration not in _VALID_INTEGRATIONS:
            return jsonify({"ok": False, "error": f"integration must be one of: {', '.join(_VALID_INTEGRATIONS)}"}), 400
        kwargs["integration"] = integration

    for field in _PASSTHROUGH_FIELDS:
        if field in ("integration",):
            continue
        if field in data:
            val = data[field]
            if field == "api_verify_tls":
                val = 1 if val else 0
            elif field == "poll_interval_s":
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    val = 0
            else:
                val = str(val).strip()
            kwargs[field] = val

    kwargs.update(_build_auth_kwargs(data, existing))

    if kwargs:
        mdb.update_custom_service(svc_id, **kwargs)
        mdb.audit("custom_service_update",
                  f"Updated custom service: {existing['name']} (id={svc_id})",
                  username=session.get("username", "unknown"),
                  ip=request.remote_addr or "")

    svc = mdb.get_custom_service(svc_id)
    return jsonify({"ok": True, "service": _sanitize_for_response(svc)})


@custom_services_bp.route("/api/custom-services/<int:svc_id>", methods=["DELETE"])
def api_delete(svc_id: int):
    err = _require_admin()
    if err:
        return err
    existing = mdb.get_custom_service(svc_id)
    if not existing:
        return jsonify({"ok": False, "error": "Not found"}), 404
    mdb.delete_custom_service(svc_id)
    mdb.audit("custom_service_delete",
              f"Deleted custom service: {existing['name']} ({existing['service_id']})",
              username=session.get("username", "unknown"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True})


@custom_services_bp.route("/api/custom-services/<int:svc_id>/toggle", methods=["POST"])
def api_toggle(svc_id: int):
    err = _require_admin()
    if err:
        return err
    existing = mdb.get_custom_service(svc_id)
    if not existing:
        return jsonify({"ok": False, "error": "Not found"}), 404
    new_state = 0 if existing["enabled"] else 1
    mdb.update_custom_service(svc_id, enabled=new_state)
    label = "enabled" if new_state else "disabled"
    mdb.audit("custom_service_toggle",
              f"{label.title()} custom service: {existing['name']}",
              username=session.get("username", "unknown"),
              ip=request.remote_addr or "")
    return jsonify({"ok": True, "enabled": bool(new_state)})


@custom_services_bp.route("/api/custom-services/<int:svc_id>/test", methods=["POST"])
def api_test(svc_id: int):
    err = _require_auth()
    if err:
        return err
    svc = mdb.get_custom_service(svc_id)
    if not svc:
        return jsonify({"ok": False, "error": "Not found"}), 404

    port = svc["port"]
    proto = svc.get("proto", "https")
    tcp_ok = False
    api_ok = None
    detail = ""

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            tcp_ok = True
    except OSError as exc:
        detail = f"TCP probe failed: {exc}"

    if tcp_ok and svc["integration"] == "api_poll" and svc.get("api_endpoint"):
        api_ok = False
        endpoint = svc["api_endpoint"]
        url = f"{proto}://127.0.0.1:{port}{endpoint}"
        method = (svc.get("api_method") or "GET").upper()
        verify = bool(svc.get("api_verify_tls"))
        headers = {}

        auth_type = svc.get("api_auth_type", "none")
        enc_val = svc.get("api_auth_value_enc", "")
        if enc_val and auth_type != "none":
            try:
                cred = credential_store.decrypt(enc_val)
            except Exception:
                cred = ""
            if auth_type == "bearer":
                headers["Authorization"] = f"Bearer {cred}"
            elif auth_type == "basic":
                import base64
                username = svc.get("api_auth_username", "")
                headers["Authorization"] = "Basic " + base64.b64encode(
                    f"{username}:{cred}".encode()
                ).decode()
            elif auth_type in ("api_key", "custom_header"):
                hdr = svc.get("api_auth_header", "X-Api-Key")
                headers[hdr] = cred

        try:
            if method == "POST":
                r = requests.post(url, headers=headers, timeout=5, verify=verify)
            else:
                r = requests.get(url, headers=headers, timeout=5, verify=verify)

            if r.ok:
                api_ok = True
                resp_type = svc.get("api_response_type", "json")
                status_path = svc.get("api_status_path", "")
                if resp_type == "json" and status_path:
                    try:
                        extracted = _extract_json_path(r.json(), status_path)
                        detail = f"API OK — {status_path}={extracted}"
                    except Exception:
                        detail = f"API OK — could not parse JSON path '{status_path}'"
                elif resp_type == "status_code":
                    detail = f"API OK — HTTP {r.status_code}"
                else:
                    detail = f"API OK — HTTP {r.status_code}"
            else:
                detail = f"API returned HTTP {r.status_code}"
        except requests.RequestException as exc:
            detail = f"API request failed: {exc}"

    overall = tcp_ok and (api_ok is None or api_ok)
    result = {"ok": overall, "tcp": tcp_ok, "detail": detail}
    if api_ok is not None:
        result["api"] = api_ok
    return jsonify(result)


# ---------------------------------------------------------------------------
# Webhook receiver (unauthenticated — like /api/nexus-status)
# ---------------------------------------------------------------------------

@custom_services_bp.route("/api/webhooks/<service_id>", methods=["POST"])
def webhook_receive(service_id: str):
    svc = mdb.get_custom_service_by_sid(service_id)
    if not svc:
        return jsonify({"ok": False, "error": "Unknown service"}), 404

    if svc["integration"] != "webhook":
        return jsonify({"ok": False, "error": "Service not configured for webhooks"}), 400

    secret_enc = svc.get("webhook_secret_enc", "")
    if secret_enc:
        try:
            secret = credential_store.decrypt(secret_enc)
        except Exception:
            return jsonify({"ok": False, "error": "Server credential error"}), 500

        sig_header = svc.get("webhook_sig_header", "X-Signature-256")
        received_sig = request.headers.get(sig_header, "")
        body = request.get_data()
        computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        prefixed = received_sig
        if prefixed.startswith("sha256="):
            prefixed = prefixed[7:]

        if not hmac.compare_digest(computed, prefixed):
            return jsonify({"ok": False, "error": "Invalid signature"}), 403

    data = request.get_json(force=True, silent=True) or {}
    payload_path = svc.get("webhook_payload_path", "")
    extracted = _extract_json_path(data, payload_path)

    status_str = str(extracted) if extracted is not None else "unknown"
    mdb.update_custom_service_status(service_id, status_str)

    return jsonify({"ok": True, "status": status_str})
