"""Nexus fabric adapter — Headscale (self-hosted Tailscale control)."""
from __future__ import annotations

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_TIMEOUT = 15


@register_adapter
class HeadscaleAdapter(FabricAdapter):
    TYPE_ID = "headscale"
    DISPLAY_NAME = "Headscale"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "server_url", "type": "text",     "label": "Server URL",
         "required": True,  "hint": "https://headscale.example.com"},
        {"name": "api_key",    "type": "password", "label": "API Key",
         "required": True,  "hint": "Headscale API key", "secret": True},
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

        ok, detail = self._list_nodes(config)
        checks.append({"label": "Headscale API", "passed": ok, "detail": detail})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        machines = self._fetch_nodes(config)
        nodes = []
        for m in machines:
            addrs = m.get("ipAddresses", [])
            ip = addrs[0] if addrs else ""
            if not ip:
                continue
            hostname = m.get("givenName", m.get("name", ip))
            online = m.get("online", False)
            nodes.append({
                "address": ip,
                "hostname": hostname,
                "tags": m.get("forcedTags", []),
                "metadata": {
                    "id": m.get("id", ""),
                    "user": m.get("user", {}).get("name", ""),
                    "online": online,
                    "last_seen": m.get("lastSeen", ""),
                    "addresses": addrs,
                    "namespace": m.get("namespace", ""),
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        machines = self._fetch_nodes(config)
        if not machines and _req is not None:
            return {"status": "down", "detail": {"api_reachable": False}}

        online = sum(1 for m in machines if m.get("online"))
        total = len(machines)
        status = "healthy" if online > 0 else ("degraded" if total > 0 else "down")
        return {"status": status, "detail": {
            "api_reachable": True, "total_nodes": total, "online": online,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        machines = self._fetch_nodes(config)
        for m in machines:
            if address in m.get("ipAddresses", []) and m.get("online"):
                return True
        return False

    # -- internals --

    @staticmethod
    def _headers(config: dict) -> dict:
        return {"Authorization": f"Bearer {config.get('api_key', '')}"}

    @staticmethod
    def _list_nodes(config: dict) -> tuple[bool, str]:
        base = config.get("server_url", "").rstrip("/")
        try:
            r = _req.get(
                f"{base}/api/v1/machine",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                machines = r.json().get("machines", [])
                return True, f"{len(machines)} node(s)"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _fetch_nodes(config: dict) -> list[dict]:
        if _req is None:
            return []
        base = config.get("server_url", "").rstrip("/")
        try:
            r = _req.get(
                f"{base}/api/v1/machine",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("machines", [])
        except Exception:
            pass
        return []
