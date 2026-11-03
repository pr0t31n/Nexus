"""Nexus fabric adapter — DigitalOcean VPC."""
from __future__ import annotations

import logging

try:
    import requests as _req
except ImportError:
    _req = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.digitalocean_vpc")

_API = "https://api.digitalocean.com/v2"


def _headers(config: dict) -> dict:
    return {"Authorization": f"Bearer {config.get('api_token', '')}",
            "Content-Type": "application/json"}


@register_adapter
class DigitalOceanVPCAdapter(FabricAdapter):
    TYPE_ID = "digitalocean_vpc"
    DISPLAY_NAME = "DigitalOcean VPC"
    CATEGORY = "cloud"
    FIELDS = [
        {"name": "api_token", "type": "password", "label": "API Token",
         "required": True, "hint": "DigitalOcean API token", "secret": True},
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
            r = _req.get(f"{_API}/account", headers=_headers(config), timeout=10)
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
        vpc_id = config.get("vpc_id", "")
        try:
            r = _req.get(f"{_API}/droplets?per_page=200", headers=_headers(config), timeout=15)
            if r.status_code != 200:
                return []
            droplets = r.json().get("droplets", [])
        except Exception:
            return []

        nodes = []
        for d in droplets:
            d_vpc = d.get("vpc_uuid", "")
            if d_vpc != vpc_id:
                continue
            nets = d.get("networks", {})
            priv_ip = ""
            pub_ip = ""
            for v4 in nets.get("v4", []):
                if v4.get("type") == "private":
                    priv_ip = priv_ip or v4.get("ip_address", "")
                elif v4.get("type") == "public":
                    pub_ip = pub_ip or v4.get("ip_address", "")
            if not priv_ip:
                continue
            nodes.append({
                "address": priv_ip,
                "hostname": d.get("name", priv_ip),
                "tags": d.get("tags", []),
                "metadata": {
                    "droplet_id": d.get("id"),
                    "status": d.get("status", ""),
                    "size_slug": d.get("size_slug", ""),
                    "region": d.get("region", {}).get("slug", ""),
                    "public_ip": pub_ip,
                    "image": d.get("image", {}).get("slug", ""),
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        if _req is None:
            return {"status": "down", "detail": {"error": "requests not installed"}}
        vpc_id = config.get("vpc_id", "")
        try:
            r = _req.get(f"{_API}/vpcs/{vpc_id}", headers=_headers(config), timeout=10)
            if r.status_code != 200:
                return {"status": "down", "detail": {"error": f"HTTP {r.status_code}"}}
        except Exception as e:
            return {"status": "down", "detail": {"error": str(e)[:200]}}
        nodes = self.discover_nodes(config)
        active = sum(1 for n in nodes if n["metadata"].get("status") == "active")
        status = "healthy" if active > 0 else ("degraded" if nodes else "unknown")
        return {
            "status": status,
            "detail": {"droplets_total": len(nodes), "droplets_active": active},
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("status") == "active"
        return False
