"""Nexus fabric adapter — Azure VNet."""
from __future__ import annotations

import logging

try:
    import requests as _req
except ImportError:
    _req = None

from .base import FabricAdapter, register_adapter

log = logging.getLogger("nexus.fabric.azure_vnet")

_LOGIN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_MGMT = "https://management.azure.com"
_API_VER_NET = "2023-09-01"
_API_VER_COMPUTE = "2023-09-01"


def _get_token(config: dict) -> str | None:
    if _req is None:
        return None
    url = _LOGIN_URL.format(tenant=config.get("tenant_id", ""))
    data = {
        "grant_type": "client_credentials",
        "client_id": config.get("client_id", ""),
        "client_secret": config.get("client_secret", ""),
        "scope": "https://management.azure.com/.default",
    }
    try:
        r = _req.post(url, data=data, timeout=15)
        if r.status_code == 200:
            return r.json().get("access_token")
    except Exception:
        pass
    return None


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@register_adapter
class AzureVNetAdapter(FabricAdapter):
    TYPE_ID = "azure_vnet"
    DISPLAY_NAME = "Azure VNet"
    CATEGORY = "cloud"
    FIELDS = [
        {"name": "subscription_id", "type": "text", "label": "Subscription ID",
         "required": True, "secret": False},
        {"name": "resource_group", "type": "text", "label": "Resource Group",
         "required": True, "secret": False},
        {"name": "vnet_name", "type": "text", "label": "VNet Name",
         "required": True, "secret": False},
        {"name": "tenant_id", "type": "text", "label": "Tenant ID",
         "required": True, "secret": False},
        {"name": "client_id", "type": "text", "label": "Client ID",
         "required": True, "secret": False},
        {"name": "client_secret", "type": "password", "label": "Client Secret",
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
        checks.append({"label": "requests installed", "passed": True, "detail": ""})

        token = _get_token(config)
        ok = token is not None
        checks.append({"label": "Azure auth", "passed": ok, "detail": "Token obtained" if ok else "Auth failed"})

        if token:
            sub = config.get("subscription_id", "")
            rg = config.get("resource_group", "")
            vnet = config.get("vnet_name", "")
            url = (f"{_MGMT}/subscriptions/{sub}/resourceGroups/{rg}"
                   f"/providers/Microsoft.Network/virtualNetworks/{vnet}"
                   f"?api-version={_API_VER_NET}")
            try:
                r = _req.get(url, headers=_headers(token), timeout=15)
                vnet_ok = r.status_code == 200
                checks.append({"label": "VNet accessible", "passed": vnet_ok,
                                "detail": vnet if vnet_ok else f"HTTP {r.status_code}"})
            except Exception as e:
                checks.append({"label": "VNet accessible", "passed": False, "detail": str(e)[:200]})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        if _req is None:
            return []
        token = _get_token(config)
        if not token:
            return []
        sub = config.get("subscription_id", "")
        rg = config.get("resource_group", "")
        url = (f"{_MGMT}/subscriptions/{sub}/resourceGroups/{rg}"
               f"/providers/Microsoft.Compute/virtualMachines"
               f"?api-version={_API_VER_COMPUTE}")
        try:
            r = _req.get(url, headers=_headers(token), timeout=20)
            if r.status_code != 200:
                return []
            vms = r.json().get("value", [])
        except Exception:
            return []

        nodes = []
        for vm in vms:
            props = vm.get("properties", {})
            name = vm.get("name", "")
            state = props.get("provisioningState", "")
            nics = props.get("networkProfile", {}).get("networkInterfaces", [])
            priv_ip = ""
            pub_ip = ""
            for nic_ref in nics:
                nic_id = nic_ref.get("id", "")
                if nic_id:
                    priv_ip, pub_ip = self._get_nic_ips(token, nic_id)
                    if priv_ip:
                        break
            if not priv_ip:
                continue
            nodes.append({
                "address": priv_ip,
                "hostname": name,
                "tags": list((vm.get("tags") or {}).keys()),
                "metadata": {
                    "vm_id": vm.get("id", ""),
                    "vm_size": props.get("hardwareProfile", {}).get("vmSize", ""),
                    "state": state,
                    "public_ip": pub_ip,
                    "os_type": props.get("storageProfile", {}).get("osDisk", {}).get("osType", ""),
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        token = _get_token(config)
        if not token:
            return {"status": "down", "detail": {"error": "Auth failed"}}
        nodes = self.discover_nodes(config)
        running = sum(1 for n in nodes if n["metadata"].get("state") == "Succeeded")
        status = "healthy" if running > 0 else ("degraded" if nodes else "down")
        return {
            "status": status,
            "detail": {"vms_total": len(nodes), "vms_running": running},
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        for n in nodes:
            if n["address"] == address:
                return n["metadata"].get("state") == "Succeeded"
        return False

    @staticmethod
    def _get_nic_ips(token: str, nic_id: str) -> tuple[str, str]:
        url = f"{_MGMT}{nic_id}?api-version={_API_VER_NET}"
        try:
            r = _req.get(url, headers=_headers(token), timeout=10)
            if r.status_code != 200:
                return "", ""
            props = r.json().get("properties", {})
            for ip_cfg in props.get("ipConfigurations", []):
                ip_props = ip_cfg.get("properties", {})
                priv = ip_props.get("privateIPAddress", "")
                pub_ref = ip_props.get("publicIPAddress", {}).get("id", "")
                pub = ""
                if pub_ref:
                    pr = _req.get(f"{_MGMT}{pub_ref}?api-version={_API_VER_NET}",
                                  headers=_headers(token), timeout=10)
                    if pr.status_code == 200:
                        pub = pr.json().get("properties", {}).get("ipAddress", "")
                if priv:
                    return priv, pub
        except Exception:
            pass
        return "", ""
