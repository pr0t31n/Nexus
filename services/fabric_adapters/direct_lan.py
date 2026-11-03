"""Nexus fabric adapter — Direct LAN (ARP + subnet scan)."""
from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess

from .base import FabricAdapter, register_adapter


@register_adapter
class DirectLanAdapter(FabricAdapter):
    TYPE_ID = "direct_lan"
    DISPLAY_NAME = "Direct LAN"
    CATEGORY = "direct"
    FIELDS = [
        {"name": "interface", "type": "text", "label": "Interface",
         "required": True, "hint": "e.g. eth0", "default": "", "secret": False},
        {"name": "subnets", "type": "text", "label": "Subnets",
         "required": True, "hint": "CIDR, one per line", "default": "", "secret": False},
        {"name": "gateway", "type": "text", "label": "Gateway",
         "required": False, "hint": "e.g. 192.168.1.1", "default": "", "secret": False},
        {"name": "vlan_tag", "type": "text", "label": "VLAN tag",
         "required": False, "hint": "e.g. 100", "default": "", "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        iface = config.get("interface", "")
        up = self._iface_up(iface)
        checks.append({"label": f"Interface {iface} UP", "passed": up,
                        "detail": "Interface is active" if up else "Interface not found or DOWN"})

        subnets_raw = config.get("subnets", "")
        parsed = self._parse_subnets(subnets_raw)
        checks.append({"label": "Subnets parseable", "passed": len(parsed) > 0,
                        "detail": f"{len(parsed)} valid subnet(s)" if parsed else "No valid CIDR subnets"})

        gw = config.get("gateway", "").strip()
        if gw:
            reachable = self._ping(gw)
            checks.append({"label": f"Gateway {gw} reachable", "passed": reachable,
                            "detail": "Ping OK" if reachable else "No response"})

        ok = all(c["passed"] for c in checks)
        return {"ok": ok, "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        nodes = []
        try:
            seen = set()
            for entry in self._arp_table():
                ip = entry.get("ip", "")
                if ip and ip not in seen:
                    seen.add(ip)
                    subnets = self._parse_subnets(config.get("subnets", ""))
                    if self._ip_in_subnets(ip, subnets):
                        nodes.append({
                            "address": ip,
                            "hostname": ip,
                            "tags": [],
                            "metadata": {"mac": entry.get("mac", ""), "source": "arp"},
                        })
        except Exception:
            pass
        return nodes

    def check_health(self, config: dict) -> dict:
        iface = config.get("interface", "")
        up = self._iface_up(iface)
        gw = config.get("gateway", "").strip()
        gw_ok = True
        if gw:
            gw_ok = self._ping(gw)

        if up and gw_ok:
            status = "healthy"
        elif up:
            status = "degraded"
        else:
            status = "down"
        return {
            "status": status,
            "detail": {"interface_up": up, "gateway_reachable": gw_ok if gw else None},
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        return self._ping(address)

    # -- internals --

    @staticmethod
    def _iface_up(iface: str) -> bool:
        if not iface:
            return False
        try:
            if shutil.which("ip"):
                out = subprocess.run(
                    ["ip", "link", "show", iface], capture_output=True, text=True, timeout=10,
                )
                return out.returncode == 0 and "UP" in out.stdout
            out = subprocess.run(
                ["ifconfig", iface], capture_output=True, text=True, timeout=10,
            )
            return out.returncode == 0 and "UP" in out.stdout
        except Exception:
            return False

    @staticmethod
    def _ping(ip: str, count: int = 1, timeout: int = 2) -> bool:
        try:
            r = subprocess.run(
                ["ping", "-c", str(count), "-W", str(timeout), ip],
                capture_output=True, timeout=10,
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _arp_table() -> list[dict]:
        entries = []
        try:
            if shutil.which("ip"):
                out = subprocess.run(
                    ["ip", "neigh"], capture_output=True, text=True, timeout=10,
                )
                for line in out.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and parts[2] == "lladdr":
                        entries.append({"ip": parts[0], "mac": parts[4]})
                    elif len(parts) >= 5:
                        m = re.search(r"lladdr\s+(\S+)", line)
                        if m:
                            entries.append({"ip": parts[0], "mac": m.group(1)})
            else:
                out = subprocess.run(
                    ["arp", "-an"], capture_output=True, text=True, timeout=10,
                )
                for line in out.stdout.splitlines():
                    m = re.match(r"\?\s*\(([^)]+)\)\s+at\s+(\S+)", line)
                    if not m:
                        m = re.match(r"\S+\s+\(([^)]+)\)\s+at\s+(\S+)", line)
                    if m:
                        entries.append({"ip": m.group(1), "mac": m.group(2)})
        except Exception:
            pass
        return entries

    @staticmethod
    def _parse_subnets(raw: str) -> list[ipaddress.IPv4Network]:
        nets = []
        for line in raw.replace(",", "\n").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                nets.append(ipaddress.ip_network(s, strict=False))
            except ValueError:
                pass
        return nets

    @staticmethod
    def _ip_in_subnets(ip: str, subnets: list[ipaddress.IPv4Network]) -> bool:
        try:
            addr = ipaddress.ip_address(ip)
            return any(addr in net for net in subnets)
        except ValueError:
            return False
