"""Nexus fabric adapter — GCP VPC."""
from __future__ import annotations

import json
import logging

try:
    import requests as _req
except ImportError:
    _req = None

try:
    from google.oauth2 import service_account as _sa
    import google.auth.transport.requests as _ga_req
except ImportError:
    _sa = None
    _ga_req = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.gcp_vpc")

_COMPUTE = "https://compute.googleapis.com/compute/v1"


def _get_credentials(config: dict):
    key_json = config.get("service_account_key", "")
    if not key_json or _sa is None:
        return None
    try:
        info = json.loads(key_json) if isinstance(key_json, str) else key_json
        creds = _sa.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/compute.readonly"])
        creds.refresh(_ga_req.Request())
        return creds
    except Exception:
        return None


def _headers(creds) -> dict:
    return {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}


@register_adapter
class GCPVPCAdapter(FabricAdapter):
    TYPE_ID = "gcp_vpc"
    DISPLAY_NAME = "GCP VPC"
    CATEGORY = "cloud"
    FIELDS = [
        {"name": "project_id", "type": "text", "label": "Project ID",
         "required": True, "secret": False},
        {"name": "network_name", "type": "text", "label": "Network Name",
         "required": True, "secret": False},
        {"name": "service_account_key", "type": "textarea", "label": "Service Account Key JSON",
         "required": True, "secret": True},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 300

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests installed", "passed": False, "detail": "pip install requests"})
            return {"ok": False, "checks": checks}
        if _sa is None:
            checks.append({"label": "google-auth installed", "passed": False,
                            "detail": "pip install google-auth"})
            return {"ok": False, "checks": checks}
        checks.append({"label": "Dependencies", "passed": True, "detail": "google-auth + requests"})

        creds = _get_credentials(config)
        ok = creds is not None
        checks.append({"label": "GCP auth", "passed": ok,
                        "detail": "Authenticated" if ok else "Auth failed"})

        if creds:
            proj = config.get("project_id", "")
            net = config.get("network_name", "")
            url = f"{_COMPUTE}/projects/{proj}/global/networks/{net}"
            try:
                r = _req.get(url, headers=_headers(creds), timeout=15)
                net_ok = r.status_code == 200
                checks.append({"label": "Network accessible", "passed": net_ok,
                                "detail": net if net_ok else f"HTTP {r.status_code}"})
            except Exception as e:
                checks.append({"label": "Network accessible", "passed": False, "detail": str(e)[:200]})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if _req is None or _sa is None:
            return []
        creds = _get_credentials(config)
        if not creds:
            return []
        proj = config.get("project_id", "")
        net_name = config.get("network_name", "")
        url = f"{_COMPUTE}/projects/{proj}/aggregated/instances"
        try:
            r = _req.get(url, headers=_headers(creds), timeout=20)
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception:
            return []

        nodes = []
        for zone_data in data.get("items", {}).values():
            for inst in zone_data.get("instances", []):
                ifaces = inst.get("networkInterfaces", [])
                for iface in ifaces:
                    net_url = iface.get("network", "")
                    if net_name not in net_url:
                        continue
                    priv_ip = iface.get("networkIP", "")
                    pub_ip = ""
                    for ac in iface.get("accessConfigs", []):
                        if ac.get("natIP"):
                            pub_ip = ac["natIP"]
                            break
                    if not priv_ip:
                        continue
                    labels = inst.get("labels", {})
                    nodes.append({
                        "address": priv_ip,
                        "hostname": inst.get("name", priv_ip),
                        "tags": list(labels.keys()),
                        "metadata": {
                            "instance_id": inst.get("id", ""),
                            "machine_type": inst.get("machineType", "").rsplit("/", 1)[-1],
                            "status": inst.get("status", ""),
                            "zone": inst.get("zone", "").rsplit("/", 1)[-1],
                            "public_ip": pub_ip,
                            "labels": labels,
                        },
                    })
                    break
        return nodes

    def check_health(self, config: dict) -> dict:
        creds = _get_credentials(config)
        if not creds:
            return {"status": "down", "detail": {"error": "Auth failed"}}
        nodes = self.discover_nodes(config)
        running = sum(1 for n in nodes if n["metadata"].get("status") == "RUNNING")
        status = "healthy" if running > 0 else ("degraded" if nodes else "down")
        return {
            "status": status,
            "detail": {"instances_total": len(nodes), "instances_running": running},
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("status") == "RUNNING"
        return False
