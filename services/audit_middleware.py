"""Nexus v2.5 — Audit middleware.

Flask before/after request hooks that log every API call, config changes,
failed auth attempts, and export/download events to the audit_log table.
"""
from __future__ import annotations

import json
import logging
import time
from functools import wraps

from flask import Flask, request, session, g

from models import db as mdb

log = logging.getLogger("nexus.audit_middleware")

_SKIP_PATHS = frozenset({
    "/static/", "/favicon.ico",
})

_SENSITIVE_ACTIONS = frozenset({
    "/api/settings", "/api/credentials", "/api/fabrics",
    "/api/fleet/delete", "/api/fleet/update",
    "/api/audit/ports/", "/api/enrollment/issue",
    "/api/dispatch/submit",
})


def init_audit_middleware(app: Flask) -> None:
    @app.before_request
    def _audit_before():
        g.audit_start = time.monotonic()

    @app.after_request
    def _audit_after(response):
        path = request.path
        if any(path.startswith(s) for s in _SKIP_PATHS):
            return response

        if request.method == "GET" and not path.startswith("/api/"):
            return response

        elapsed = 0.0
        start = getattr(g, "audit_start", None)
        if start is not None:
            elapsed = round(time.monotonic() - start, 3)

        username = session.get("username", "anonymous")
        ip = request.remote_addr or ""
        method = request.method
        status = response.status_code

        severity = "info"
        if status >= 500:
            severity = "error"
        elif status >= 400:
            severity = "warning"

        is_sensitive = any(path.startswith(s) for s in _SENSITIVE_ACTIONS)

        event_type = "api_request"
        if status == 401 or status == 403:
            event_type = "auth_failure"
            severity = "warning"
        elif is_sensitive and method in ("POST", "PUT", "DELETE", "PATCH"):
            event_type = "config_change"
        elif "export" in path or "download" in path:
            event_type = "export"

        details = {
            "method": method,
            "path": path,
            "status": status,
            "elapsed_ms": int(elapsed * 1000),
        }
        if request.query_string:
            details["query"] = request.query_string.decode("utf-8", errors="replace")[:500]

        try:
            mdb.audit(
                action=f"{method} {path}",
                detail=f"{method} {path} → {status} ({int(elapsed*1000)}ms)",
                username=username,
                ip=ip,
                event_type=event_type,
                severity=severity,
                resource_type=_extract_resource_type(path),
                resource_id=_extract_resource_id(path),
                user_agent=request.user_agent.string[:200] if request.user_agent else "",
                hostname=request.host or "",
                details_json=json.dumps(details),
            )
        except Exception:
            log.debug("Failed to audit %s %s", method, path, exc_info=True)

        return response

    log.info("Audit middleware installed")


def _extract_resource_type(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "api":
        return parts[1]
    return ""


def _extract_resource_id(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "api":
        candidate = parts[2]
        if candidate.isdigit():
            return candidate
    return ""
