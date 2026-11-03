"""Nexus fabric adapter — Linode VPC."""
from __future__ import annotations

import logging

try:
    import requests as _req
except ImportError:
    _req = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.linode_vpc")

_API = "https://api.linode.com/v4"


def _headers(config: dict) -> dict:
    return {"Authorization": f"Bearer {config.get('api_token', '')}",
            "Content-Type": "application/json"}


@register_adapter
class LinodeVPCAdapter(FabricAdapter):
    TYPE_ID = "linode_vpc"
    DISPLAY_NAME = "Linode VPC"
    CATEGORY = "cloud"
    FIELDS = [
        {"name": "api_token", "type": "password", "label": "API Token",
         "required": True, "hint": "Linode personal access token", "secret": True},
        {"name": "vpc_id", "type": "text", "label": "VPC ID",
         "required": True, "secret": False},
        {"name": "region", "type": "text", "label": "Region",
         "required": False, "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 300

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests installed", "passed": False, "detail": "pip install requests"})
            return {"ok": False, "checks": checks}

        try:
            r = _req.get(f"{_API}/profile", headers=_headers(config), timeout=10)
            ok = r.status_code == 200
            checks.append({"label": "API auth", "passed": ok,
                            "detail": "Authenticated" if ok else f"HTTP {r.status_code}"})
        except Exception as e:
            checks.append({"label": "API auth", "passed": False, "detail": str(e)[:200]})
            return {"ok": False, "checks": checks}

        vpc_id = config.get("vpc_id", "")
        try:
            r = _req.get(f"{_API}/vpcs/{vpc_id}", headers=_headers(config), timeout=10)
            ok = r.status_code == 200
            checks.append({"label": "VPC accessible", "passed": ok,
                            "detail": vpc_id if ok else f"HTTP {r.status_code}"})
        except Exception as e:
            checks.append({"label": "VPC accessible", "passed": False, "detail": str(e)[:200]})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if _req is None:
            return []
        try:
            r = _req.get(f"{_API}/linode/instances", headers=_headers(config), timeout=15)
            if r.status_code != 200:
                return []
            instances = r.json().get("data", [])
        except Exception:
            return []

        vpc_id = config.get("vpc_id", "")
        region = config.get("region", "").strip()
        nodes = []
        for inst in instances:
            if region and inst.get("region") != region:
                continue
            ipv4 = inst.get("ipv4", [])
            priv_ip = ""
            pub_ip = ""
            for ip in ipv4:
                if ip.startswith("192.168.") or ip.startswith("10."):
                    priv_ip = ip
                else:
                    pub_ip = pub_ip or ip
            if not priv_ip and ipv4:
                priv_ip = ipv4[0]
            if not priv_ip:
                continue
            nodes.append({
                "address": priv_ip,
                "hostname": inst.get("label", priv_ip),
                "tags": inst.get("tags", []),
                "metadata": {
                    "linode_id": inst.get("id"),
                    "status": inst.get("status", ""),
                    "type": inst.get("type", ""),
                    "region": inst.get("region", ""),
                    "public_ip": pub_ip,
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        if _req is None:
            return {"status": "down", "detail": {"error": "requests not installed"}}
        try:
            r = _req.get(f"{_API}/vpcs/{config.get('vpc_id', '')}",
                         headers=_headers(config), timeout=10)
            if r.status_code != 200:
                return {"status": "down", "detail": {"error": f"HTTP {r.status_code}"}}
        except Exception as e:
            return {"status": "down", "detail": {"error": str(e)[:200]}}
        nodes = self.discover_nodes(config)
        running = sum(1 for n in nodes if n["metadata"].get("status") == "running")
        status = "healthy" if running > 0 else ("degraded" if nodes else "unknown")
        return {
            "status": status,
            "detail": {"instances_total": len(nodes), "instances_running": running},
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("status") == "running"
        return False
