"""Nexus fabric adapter — Netmaker."""
from __future__ import annotations

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_TIMEOUT = 15


@register_adapter
class NetmakerAdapter(FabricAdapter):
    TYPE_ID = "netmaker"
    DISPLAY_NAME = "Netmaker"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "server_url",   "type": "text",     "label": "Server URL",
         "required": True,  "hint": "https://api.netmaker.example.com"},
        {"name": "api_key",      "type": "password", "label": "API Key / Master Key",
         "required": True,  "hint": "Netmaker master key or API key", "secret": True},
        {"name": "network_name", "type": "text",     "label": "Network Name",
         "required": True,  "hint": "Target network name"},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 180

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests library", "passed": False,
                            "detail": "Python requests not installed"})
            return {"ok": False, "checks": checks}

        ok, detail = self._list_nodes(config)
        checks.append({"label": "Netmaker API", "passed": ok, "detail": detail})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        raw_nodes = self._fetch_nodes(config)
        nodes = []
        for n in raw_nodes:
            ip = n.get("address", "").split("/")[0]
            if not ip:
                continue
            hostname = n.get("name", "") or n.get("hostname", "") or ip
            nodes.append({
                "address": ip,
                "hostname": hostname,
                "tags": [],
                "metadata": {
                    "id": n.get("id", ""),
                    "os": n.get("os", ""),
                    "version": n.get("version", ""),
                    "connected": n.get("connected", False),
                    "endpoint": n.get("endpoint", ""),
                    "is_relay": n.get("isrelay", False),
                    "is_ingress": n.get("isingressgateway", False),
                    "is_egress": n.get("isegressgateway", False),
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        raw_nodes = self._fetch_nodes(config)
        if not raw_nodes and _req is not None:
            return {"status": "down", "detail": {"api_reachable": False}}

        connected = sum(1 for n in raw_nodes if n.get("connected", False))
        total = len(raw_nodes)
        status = "healthy" if connected > 0 else ("degraded" if total > 0 else "down")
        return {"status": status, "detail": {
            "api_reachable": True, "total_nodes": total, "connected": connected,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        raw_nodes = self._fetch_nodes(config)
        for n in raw_nodes:
            ip = n.get("address", "").split("/")[0]
            if ip == address and n.get("connected", False):
                return True
        return False

    # -- internals --

    @staticmethod
    def _headers(config: dict) -> dict:
        return {"Authorization": f"Bearer {config.get('api_key', '')}"}

    @staticmethod
    def _list_nodes(config: dict) -> tuple[bool, str]:
        base = config.get("server_url", "").rstrip("/")
        net = config.get("network_name", "")
        try:
            r = _req.get(
                f"{base}/api/nodes",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                params={"network": net} if net else {},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                nodes = r.json()
                if isinstance(nodes, list):
                    return True, f"{len(nodes)} node(s)"
                return True, "OK"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _fetch_nodes(config: dict) -> list[dict]:
        if _req is None:
            return []
        base = config.get("server_url", "").rstrip("/")
        net = config.get("network_name", "")
        try:
            r = _req.get(
                f"{base}/api/nodes",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                params={"network": net} if net else {},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []
