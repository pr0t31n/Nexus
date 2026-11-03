"""Nexus fabric adapter — NetBird."""
from __future__ import annotations

import json
import shutil
import subprocess

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_TIMEOUT = 15


@register_adapter
class NetBirdAdapter(FabricAdapter):
    TYPE_ID = "netbird"
    DISPLAY_NAME = "NetBird"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "management_url", "type": "text",     "label": "Management URL",
         "required": False, "hint": "https://api.netbird.io (or self-hosted)"},
        {"name": "api_token",      "type": "password", "label": "API Token",
         "required": False, "hint": "NetBird management API token", "secret": True},
        {"name": "setup_key",      "type": "password", "label": "Setup Key",
         "required": False, "hint": "For enrolling new nodes",      "secret": True},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 180

    def test_connection(self, config: dict) -> dict:
        checks = []
        nb = shutil.which("netbird")
        checks.append({"label": "netbird CLI", "passed": nb is not None,
                        "detail": nb or "Not found in PATH"})

        if nb:
            ok, detail = self._cli_status()
            checks.append({"label": "NetBird status", "passed": ok, "detail": detail})

        mgmt = config.get("management_url", "").strip()
        token = config.get("api_token", "").strip()
        if mgmt and token and _req is not None:
            ok, detail = self._api_peers(mgmt, token)
            checks.append({"label": "Management API", "passed": ok, "detail": detail})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        mgmt = config.get("management_url", "").strip()
        token = config.get("api_token", "").strip()
        if mgmt and token and _req is not None:
            return self._discover_api(mgmt, token)
        return self._discover_cli()

    def check_health(self, config: dict) -> dict:
        nb = shutil.which("netbird")
        if not nb:
            return {"status": "down", "detail": {"cli_found": False}}

        ok, detail = self._cli_status()
        if not ok:
            return {"status": "down", "detail": {"connected": False, "info": detail}}

        mgmt = config.get("management_url", "").strip()
        token = config.get("api_token", "").strip()
        if mgmt and token and _req is not None:
            peers = self._fetch_peers(mgmt, token)
            if peers is not None:
                connected = sum(1 for p in peers if p.get("connected", False))
                total = len(peers)
                status = "healthy" if connected > 0 else ("degraded" if total > 0 else "down")
                return {"status": status, "detail": {
                    "connected": True, "total_peers": total, "connected_peers": connected,
                }}

        return {"status": "healthy" if ok else "degraded", "detail": {"connected": ok, "info": detail}}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        return any(n["address"] == address and n.get("metadata", {}).get("connected", False)
                   for n in nodes)

    # -- internals --

    @staticmethod
    def _cli_status() -> tuple[bool, str]:
        nb = shutil.which("netbird")
        if not nb:
            return False, "netbird not found"
        try:
            r = subprocess.run([nb, "status", "--json"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                mgmt = data.get("management", {}).get("connected", False)
                signal = data.get("signal", {}).get("connected", False)
                return mgmt and signal, f"mgmt={mgmt} signal={signal}"
            return False, r.stderr.strip()[:200] if r.stderr else "Command failed"
        except json.JSONDecodeError:
            return True, "Connected (non-JSON output)"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _discover_cli() -> list[dict]:
        nb = shutil.which("netbird")
        if not nb:
            return []
        try:
            r = subprocess.run([nb, "status", "--json"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return []
            data = json.loads(r.stdout)
        except Exception:
            return []

        nodes = []
        for peer in data.get("peers", {}).get("details", []):
            ip = peer.get("netbirdIp", "") or peer.get("ip", "")
            if not ip:
                continue
            nodes.append({
                "address": ip.split("/")[0],
                "hostname": peer.get("fqdn", "") or peer.get("hostname", "") or ip,
                "tags": [],
                "metadata": {
                    "connected": peer.get("connected", False),
                    "direct": peer.get("direct", False),
                    "last_seen": peer.get("lastStatusUpdate", ""),
                },
            })
        return nodes

    @staticmethod
    def _api_peers(mgmt: str, token: str) -> tuple[bool, str]:
        base = mgmt.rstrip("/")
        try:
            r = _req.get(f"{base}/api/peers",
                          headers={"Authorization": f"Token {token}",
                                   "Accept": "application/json"},
                          timeout=_TIMEOUT)
            if r.status_code == 200:
                peers = r.json()
                return True, f"{len(peers)} peer(s)"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _fetch_peers(mgmt: str, token: str):
        if _req is None:
            return None
        base = mgmt.rstrip("/")
        try:
            r = _req.get(f"{base}/api/peers",
                          headers={"Authorization": f"Token {token}",
                                   "Accept": "application/json"},
                          timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _discover_api(self, mgmt: str, token: str) -> list[dict]:
        peers = self._fetch_peers(mgmt, token)
        if not peers:
            return []
        nodes = []
        for p in peers:
            ip = p.get("ip", "")
            if not ip:
                continue
            nodes.append({
                "address": ip.split("/")[0],
                "hostname": p.get("dns_label", "") or p.get("hostname", "") or p.get("name", "") or ip,
                "tags": p.get("groups", []),
                "metadata": {
                    "connected": p.get("connected", False),
                    "os": p.get("os", ""),
                    "version": p.get("version", ""),
                    "last_seen": p.get("last_seen", ""),
                },
            })
        return nodes
