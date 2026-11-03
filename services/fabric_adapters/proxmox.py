"""Nexus fabric adapter — Proxmox VE."""
from __future__ import annotations

import logging

try:
    import requests as _req
except ImportError:
    _req = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.proxmox")


def _auth_headers(config: dict) -> tuple[dict, str]:
    api_url = config.get("api_url", "").rstrip("/")
    token = config.get("api_token", "").strip()
    if token:
        parts = token.split("=", 1)
        if len(parts) == 2:
            return {"Authorization": f"PVEAPIToken={token}"}, api_url
        return {"Authorization": f"PVEAPIToken={token}"}, api_url

    user = config.get("username", "")
    pw = config.get("password", "")
    if user and pw:
        try:
            r = _req.post(f"{api_url}/access/ticket",
                          data={"username": user, "password": pw},
                          verify=False, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data", {})
                ticket = data.get("ticket", "")
                csrf = data.get("CSRFPreventionToken", "")
                return {"Cookie": f"PVEAuthCookie={ticket}",
                        "CSRFPreventionToken": csrf}, api_url
        except Exception:
            pass
    return {}, api_url


@register_adapter
class ProxmoxAdapter(FabricAdapter):
    TYPE_ID = "proxmox"
    DISPLAY_NAME = "Proxmox VE"
    CATEGORY = "direct"
    FIELDS = [
        {"name": "api_url", "type": "text", "label": "API URL",
         "required": True, "hint": "https://proxmox:8006/api2/json", "secret": False},
        {"name": "api_token", "type": "password", "label": "API Token",
         "required": False, "hint": "user@pam!tokenid=secret", "secret": True},
        {"name": "username", "type": "text", "label": "Username",
         "required": False, "hint": "Alternative: user@pam", "secret": False},
        {"name": "password", "type": "password", "label": "Password",
         "required": False, "secret": True},
        {"name": "node_name", "type": "text", "label": "PVE Node Name",
         "required": False, "hint": "Proxmox node to query", "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "hypervisor_exec"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests installed", "passed": False, "detail": "pip install requests"})
            return {"ok": False, "checks": checks}

        headers, api_url = _auth_headers(config)
        if not headers:
            checks.append({"label": "Auth", "passed": False, "detail": "No valid credentials"})
            return {"ok": False, "checks": checks}

        try:
            r = _req.get(f"{api_url}/version", headers=headers, verify=False, timeout=10)
            ok = r.status_code == 200
            ver = r.json().get("data", {}).get("version", "") if ok else ""
            checks.append({"label": "API auth", "passed": ok,
                            "detail": f"PVE {ver}" if ok else f"HTTP {r.status_code}"})
        except Exception as e:
            checks.append({"label": "API auth", "passed": False, "detail": str(e)[:200]})
            return {"ok": False, "checks": checks}

        node_name = config.get("node_name", "").strip()
        if node_name:
            try:
                r = _req.get(f"{api_url}/nodes/{node_name}/status",
                             headers=headers, verify=False, timeout=10)
                ok = r.status_code == 200
                checks.append({"label": f"Node {node_name}", "passed": ok,
                                "detail": "online" if ok else f"HTTP {r.status_code}"})
            except Exception as e:
                checks.append({"label": f"Node {node_name}", "passed": False, "detail": str(e)[:200]})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if _req is None:
            return []
        headers, api_url = _auth_headers(config)
        if not headers:
            return []

        pve_nodes = self._list_pve_nodes(headers, api_url, config.get("node_name", ""))
        nodes = []
        for pve_node in pve_nodes:
            vms = self._list_vms(headers, api_url, pve_node)
            for vm in vms:
                ip = self._get_vm_ip(headers, api_url, pve_node, vm)
                if not ip:
                    continue
                vm_type = vm.get("type", "qemu")
                nodes.append({
                    "address": ip,
                    "hostname": vm.get("name", f"vm-{vm.get('vmid')}"),
                    "tags": [vm_type],
                    "metadata": {
                        "vmid": vm.get("vmid"),
                        "status": vm.get("status", ""),
                        "type": vm_type,
                        "pve_node": pve_node,
                        "cpu": vm.get("cpu", 0),
                        "maxmem": vm.get("maxmem", 0),
                        "maxdisk": vm.get("maxdisk", 0),
                        "uptime": vm.get("uptime", 0),
                    },
                })
        return nodes

    def check_health(self, config: dict) -> dict:
        if _req is None:
            return {"status": "down", "detail": {"error": "requests not installed"}}
        headers, api_url = _auth_headers(config)
        if not headers:
            return {"status": "down", "detail": {"error": "Auth failed"}}

        try:
            r = _req.get(f"{api_url}/cluster/status", headers=headers, verify=False, timeout=10)
            if r.status_code != 200:
                return {"status": "down", "detail": {"error": f"HTTP {r.status_code}"}}
            cluster = r.json().get("data", [])
            nodes_online = sum(1 for n in cluster if n.get("type") == "node" and n.get("online"))
            nodes_total = sum(1 for n in cluster if n.get("type") == "node")
        except Exception as e:
            return {"status": "down", "detail": {"error": str(e)[:200]}}

        vms = self.discover_nodes(config)
        running = sum(1 for n in vms if n["metadata"].get("status") == "running")

        if nodes_online > 0 and running > 0:
            status = "healthy"
        elif nodes_online > 0:
            status = "degraded"
        else:
            status = "down"
        return {
            "status": status,
            "detail": {
                "pve_nodes_online": nodes_online,
                "pve_nodes_total": nodes_total,
                "vms_total": len(vms),
                "vms_running": running,
            },
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("status") == "running"
        return False

    @staticmethod
    def _list_pve_nodes(headers: dict, api_url: str, filter_node: str) -> list[str]:
        try:
            r = _req.get(f"{api_url}/nodes", headers=headers, verify=False, timeout=10)
            if r.status_code != 200:
                return []
            data = r.json().get("data", [])
            names = [n["node"] for n in data if n.get("status") == "online"]
            if filter_node:
                return [n for n in names if n == filter_node]
            return names
        except Exception:
            return []

    @staticmethod
    def _list_vms(headers: dict, api_url: str, node: str) -> list[dict]:
        vms = []
        for vtype in ("qemu", "lxc"):
            try:
                r = _req.get(f"{api_url}/nodes/{node}/{vtype}",
                             headers=headers, verify=False, timeout=10)
                if r.status_code == 200:
                    for vm in r.json().get("data", []):
                        vm["type"] = vtype
                        vms.append(vm)
            except Exception:
                pass
        return vms

    @staticmethod
    def _get_vm_ip(headers: dict, api_url: str, node: str, vm: dict) -> str:
        vmid = vm.get("vmid")
        vtype = vm.get("type", "qemu")
        if vm.get("status") != "running":
            return ""
        try:
            if vtype == "qemu":
                r = _req.get(
                    f"{api_url}/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
                    headers=headers, verify=False, timeout=5)
                if r.status_code == 200:
                    for iface in r.json().get("data", {}).get("result", []):
                        for addr in iface.get("ip-addresses", []):
                            ip = addr.get("ip-address", "")
                            if ip and not ip.startswith("127.") and ":" not in ip:
                                return ip
            else:
                r = _req.get(f"{api_url}/nodes/{node}/lxc/{vmid}/interfaces",
                             headers=headers, verify=False, timeout=5)
                if r.status_code == 200:
                    for iface in r.json().get("data", []):
                        ip = iface.get("inet", "").split("/")[0]
                        if ip and not ip.startswith("127."):
                            return ip
        except Exception:
            pass
        return ""
