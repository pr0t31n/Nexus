"""Nexus fabric adapter — IPSec / IKEv2."""
from __future__ import annotations

import re
import shutil
import subprocess

from .base import FabricAdapter, register_adapter


@register_adapter
class IPSecIKEv2Adapter(FabricAdapter):
    TYPE_ID = "ipsec_ikev2"
    DISPLAY_NAME = "IPSec / IKEv2"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "gateway",          "type": "text",     "label": "Gateway",
         "required": True,  "hint": "IPSec gateway hostname/IP"},
        {"name": "identity",         "type": "text",     "label": "Identity",
         "required": True,  "hint": "IKEv2 identity string"},
        {"name": "certificate_path", "type": "text",     "label": "Certificate Path",
         "required": False, "hint": "Path to client certificate"},
        {"name": "ca_cert_path",     "type": "text",     "label": "CA Certificate Path",
         "required": False},
        {"name": "psk",              "type": "password", "label": "Pre-Shared Key",
         "required": False, "secret": True},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        tool, tool_label = self._detect_tool()
        checks.append({"label": "IPSec tool", "passed": tool is not None,
                        "detail": tool_label})

        gateway = config.get("gateway", "")
        if gateway:
            ok = self._ping(gateway)
            checks.append({"label": f"Gateway {gateway} reachable",
                            "passed": ok, "detail": "ICMP OK" if ok else "Unreachable"})

        if tool:
            sa_ok, sa_detail = self._check_sas(tool)
            checks.append({"label": "Active SAs", "passed": sa_ok, "detail": sa_detail})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        tool, _ = self._detect_tool()
        if not tool:
            return []
        try:
            if tool == "swanctl":
                r = subprocess.run(
                    ["swanctl", "--list-sas", "--raw"],
                    capture_output=True, text=True, timeout=15,
                )
            else:
                r = subprocess.run(
                    ["ipsec", "statusall"],
                    capture_output=True, text=True, timeout=15,
                )
            if r.returncode != 0:
                return []
        except Exception:
            return []

        nodes = []
        seen = set()
        for ip in re.findall(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", r.stdout):
            if ip in seen or ip.startswith("0.") or ip == "127.0.0.1":
                continue
            gateway = config.get("gateway", "")
            if ip == gateway:
                continue
            seen.add(ip)
            nodes.append({
                "address": ip, "hostname": ip, "tags": [],
                "metadata": {"source": tool},
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        tool, _ = self._detect_tool()
        if not tool:
            return {"status": "down", "detail": {"tool_found": False}}

        sa_ok, sa_detail = self._check_sas(tool)
        gateway = config.get("gateway", "")
        gw_ok = self._ping(gateway) if gateway else True

        if sa_ok and gw_ok:
            status = "healthy"
        elif sa_ok or gw_ok:
            status = "degraded"
        else:
            status = "down"

        return {"status": status, "detail": {
            "tool": tool, "sas": sa_detail, "gateway_reachable": gw_ok,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        return self._ping(address)

    # -- internals --

    @staticmethod
    def _detect_tool() -> tuple[str | None, str]:
        if shutil.which("swanctl"):
            return "swanctl", "strongSwan (swanctl)"
        if shutil.which("ipsec"):
            return "ipsec", "strongSwan (ipsec)"
        return None, "No IPSec tool found (swanctl, ipsec)"

    @staticmethod
    def _check_sas(tool: str) -> tuple[bool, str]:
        try:
            if tool == "swanctl":
                r = subprocess.run(["swanctl", "--list-sas"],
                                   capture_output=True, text=True, timeout=15)
            else:
                r = subprocess.run(["ipsec", "statusall"],
                                   capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return False, "Command failed"
            established = r.stdout.lower().count("established")
            if established > 0:
                return True, f"{established} SA(s) established"
            return False, "No established SAs"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _ping(host: str) -> bool:
        if not host:
            return False
        try:
            r = subprocess.run(
                ["ping", "-c", "1", "-W", "3", host],
                capture_output=True, timeout=5,
            )
            return r.returncode == 0
        except Exception:
            return False
