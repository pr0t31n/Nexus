"""Nexus fabric adapter — WireGuard."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .base import FabricAdapter, register_adapter


def _wg_binary() -> str | None:
    return shutil.which("wg")


@register_adapter
class WireGuardAdapter(FabricAdapter):
    TYPE_ID = "wireguard"
    DISPLAY_NAME = "WireGuard"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "interface",   "type": "text",     "label": "Interface",
         "required": True,  "hint": "e.g. wg0",                "default": "wg0"},
        {"name": "endpoint",    "type": "text",     "label": "Endpoint",
         "required": False, "hint": "Public endpoint host:port"},
        {"name": "listen_port", "type": "number",   "label": "Listen Port",
         "required": False, "hint": "WireGuard listen port"},
        {"name": "private_key", "type": "password", "label": "Private Key",
         "required": False, "hint": "Interface private key",    "secret": True},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        iface = config.get("interface", "wg0")
        up, detail = self._iface_up(iface)
        checks.append({"label": f"Interface {iface} UP", "passed": up, "detail": detail})

        wg = _wg_binary()
        if wg:
            ok, detail = self._wg_show(wg, iface)
            checks.append({"label": "wg show", "passed": ok, "detail": detail})
        else:
            checks.append({"label": "wg binary", "passed": False,
                            "detail": "wg not found in PATH"})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        iface = config.get("interface", "wg0")
        wg = _wg_binary()
        if not wg:
            return []
        try:
            r = subprocess.run(
                [wg, "show", iface, "dump"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return []
        except Exception:
            return []

        nodes = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            pubkey = parts[0]
            endpoint = parts[2]
            allowed_ips = parts[3]
            latest_hs = parts[4]
            ip = self._first_ip(allowed_ips)
            if not ip:
                continue
            nodes.append({
                "address": ip,
                "hostname": ip,
                "tags": [],
                "metadata": {
                    "public_key": pubkey,
                    "endpoint": endpoint,
                    "latest_handshake": int(latest_hs) if latest_hs.isdigit() else 0,
                    "transfer_rx": int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0,
                    "transfer_tx": int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0,
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        iface = config.get("interface", "wg0")
        up, _ = self._iface_up(iface)
        if not up:
            return {"status": "down", "detail": {"interface_up": False, "peers_recent": 0}}

        wg = _wg_binary()
        peers_total = 0
        peers_recent = 0
        now = time.time()
        if wg:
            try:
                r = subprocess.run(
                    [wg, "show", iface, "dump"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in r.stdout.splitlines()[1:]:
                    parts = line.split("\t")
                    if len(parts) < 5:
                        continue
                    peers_total += 1
                    hs = int(parts[4]) if parts[4].isdigit() else 0
                    if hs > 0 and (now - hs) < 300:
                        peers_recent += 1
            except Exception:
                pass

        status = "healthy" if peers_recent > 0 else ("degraded" if up else "down")
        return {"status": status, "detail": {
            "interface_up": up, "peers_total": peers_total, "peers_recent": peers_recent,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        now = time.time()
        for n in nodes:
            if n["address"] == address:
                hs = n["metadata"].get("latest_handshake", 0)
                return hs > 0 and (now - hs) < 300
        return False

    # -- internals --

    @staticmethod
    def _iface_up(iface: str) -> tuple[bool, str]:
        try:
            if shutil.which("ip"):
                r = subprocess.run(["ip", "link", "show", iface],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode != 0:
                    return False, f"Interface {iface} not found"
                return ("UP" in r.stdout, "UP" if "UP" in r.stdout else "DOWN")
            r = subprocess.run(["ifconfig", iface],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return False, f"Interface {iface} not found"
            return ("UP" in r.stdout, "UP" if "UP" in r.stdout else "DOWN")
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _wg_show(wg: str, iface: str) -> tuple[bool, str]:
        try:
            r = subprocess.run([wg, "show", iface],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                peers = r.stdout.count("peer:")
                return True, f"{peers} peer(s) configured"
            return False, r.stderr.strip()[:200] if r.stderr else "Command failed"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _first_ip(allowed_ips: str) -> str:
        for cidr in allowed_ips.split(","):
            cidr = cidr.strip()
            if "/" in cidr:
                ip = cidr.split("/")[0]
                if ip and ip != "0.0.0.0":
                    return ip
        return ""
