"""Nexus fabric adapter — Tailscale."""
from __future__ import annotations

import time

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_API_BASE = "https://api.tailscale.com/api/v2"
_TIMEOUT = 15
_STALE_SECS = 600


@register_adapter
class TailscaleAdapter(FabricAdapter):
    TYPE_ID = "tailscale"
    DISPLAY_NAME = "Tailscale"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "api_key",  "type": "password", "label": "API Key",
         "required": True,  "hint": "Tailscale API key",       "secret": True},
        {"name": "tailnet",  "type": "text",     "label": "Tailnet",
         "required": True,  "hint": "Tailnet name or domain"},
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

        ok, detail = self._list_devices(config)
        checks.append({"label": "Tailscale API", "passed": ok, "detail": detail})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        devices = self._fetch_devices(config)
        nodes = []
        for d in devices:
            addrs = d.get("addresses", [])
            ip = addrs[0] if addrs else ""
            if not ip:
                continue
            hostname = d.get("hostname", d.get("name", ip))
            tags = d.get("tags", [])
            nodes.append({
                "address": ip,
                "hostname": hostname,
                "tags": tags,
                "metadata": {
                    "tailscale_name": d.get("name", ""),
                    "os": d.get("os", ""),
                    "last_seen": d.get("lastSeen", ""),
                    "online": d.get("online", False),
                    "addresses": addrs,
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        devices = self._fetch_devices(config)
        if not devices and _req is not None:
            return {"status": "down", "detail": {"api_reachable": False}}

        online = sum(1 for d in devices if d.get("online"))
        total = len(devices)
        status = "healthy" if online > 0 else ("degraded" if total > 0 else "down")
        return {"status": status, "detail": {
            "api_reachable": True, "total_devices": total, "online": online,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        devices = self._fetch_devices(config)
        for d in devices:
            if address in d.get("addresses", []) and d.get("online"):
                return True
        return False

    # -- internals --

    @staticmethod
    def _headers(config: dict) -> dict:
        return {"Authorization": f"Bearer {config.get('api_key', '')}"}

    @staticmethod
    def _list_devices(config: dict) -> tuple[bool, str]:
        tailnet = config.get("tailnet", "")
        try:
            r = _req.get(
                f"{_API_BASE}/tailnet/{tailnet}/devices",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                devices = r.json().get("devices", [])
                return True, f"{len(devices)} device(s)"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _fetch_devices(config: dict) -> list[dict]:
        if _req is None:
            return []
        tailnet = config.get("tailnet", "")
        try:
            r = _req.get(
                f"{_API_BASE}/tailnet/{tailnet}/devices",
                headers={"Authorization": f"Bearer {config.get('api_key', '')}"},
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json().get("devices", [])
        except Exception:
            pass
        return []
