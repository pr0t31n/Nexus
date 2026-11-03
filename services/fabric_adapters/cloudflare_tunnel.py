"""Nexus fabric adapter — Cloudflare Tunnel."""
from __future__ import annotations

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_API_BASE = "https://api.cloudflare.com/client/v4"
_TIMEOUT = 15


@register_adapter
class CloudflareTunnelAdapter(FabricAdapter):
    TYPE_ID = "cloudflare_tunnel"
    DISPLAY_NAME = "Cloudflare Tunnel"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "tunnel_token", "type": "password", "label": "Tunnel Token",
         "required": True,  "hint": "Cloudflare tunnel token",  "secret": True},
        {"name": "account_id",  "type": "text",     "label": "Account ID",
         "required": True,  "hint": "Cloudflare account ID"},
        {"name": "tunnel_id",   "type": "text",     "label": "Tunnel ID",
         "required": True,  "hint": "Cloudflare tunnel UUID"},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 300

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests library", "passed": False,
                            "detail": "Python requests not installed"})
            return {"ok": False, "checks": checks}

        token = config.get("tunnel_token", "")
        acct = config.get("account_id", "")
        tid = config.get("tunnel_id", "")

        ok, detail = self._get_tunnel(token, acct, tid)
        checks.append({"label": "Tunnel API", "passed": ok, "detail": detail})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if _req is None:
            return []
        token = config.get("tunnel_token", "")
        acct = config.get("account_id", "")
        tid = config.get("tunnel_id", "")

        conns = self._get_connections(token, acct, tid)
        nodes = []
        seen = set()
        for c in conns:
            origin_ip = c.get("origin_ip", "")
            if not origin_ip or origin_ip in seen:
                continue
            seen.add(origin_ip)
            nodes.append({
                "address": origin_ip,
                "hostname": origin_ip,
                "tags": [],
                "metadata": {
                    "colo": c.get("colo_name", ""),
                    "connector_id": c.get("id", ""),
                    "is_pending_reconnect": c.get("is_pending_reconnect", False),
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        if _req is None:
            return {"status": "unknown", "detail": {"error": "requests not installed"}}

        token = config.get("tunnel_token", "")
        acct = config.get("account_id", "")
        tid = config.get("tunnel_id", "")

        ok, detail = self._get_tunnel(token, acct, tid)
        if not ok:
            return {"status": "down", "detail": {"api_reachable": False, "error": detail}}

        conns = self._get_connections(token, acct, tid)
        active = sum(1 for c in conns if not c.get("is_pending_reconnect", True))

        status = "healthy" if active > 0 else "degraded" if conns else "down"
        return {"status": status, "detail": {
            "api_reachable": True, "connections": len(conns), "active": active,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        return any(n["address"] == address for n in nodes)

    # -- internals --

    @staticmethod
    def _headers(token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @staticmethod
    def _get_tunnel(token: str, acct: str, tid: str) -> tuple[bool, str]:
        try:
            r = _req.get(
                f"{_API_BASE}/accounts/{acct}/tunnels/{tid}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json().get("result", {})
                return True, f"Tunnel '{data.get('name', tid)}' status: {data.get('status', 'unknown')}"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _get_connections(token: str, acct: str, tid: str) -> list[dict]:
        try:
            r = _req.get(
                f"{_API_BASE}/accounts/{acct}/tunnels/{tid}/connections",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("result", [])
        except Exception:
            pass
        return []
