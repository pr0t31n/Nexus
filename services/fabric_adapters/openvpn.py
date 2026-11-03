"""Nexus fabric adapter — OpenVPN (vanilla, no Cloak)."""
from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

from .base import FabricAdapter, register_adapter


@register_adapter
class OpenVPNAdapter(FabricAdapter):
    TYPE_ID = "openvpn"
    DISPLAY_NAME = "OpenVPN"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "server_host",            "type": "text",   "label": "Server Host",
         "required": True,  "hint": "OpenVPN server hostname/IP"},
        {"name": "server_port",            "type": "number", "label": "Server Port",
         "required": True,  "hint": "OpenVPN server port",         "default": "1194"},
        {"name": "protocol",               "type": "select", "label": "Protocol",
         "required": False, "options": ["udp", "tcp"],              "default": "udp"},
        {"name": "management_socket_path", "type": "text",   "label": "Management Socket Path",
         "required": False},
        {"name": "ca_cert_path",           "type": "text",   "label": "CA Certificate Path",
         "required": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        sock_path = config.get("management_socket_path", "").strip()
        if sock_path:
            ok, detail = self._mgmt_connect(sock_path)
            checks.append({"label": "Management socket", "passed": ok, "detail": detail})
        else:
            host = config.get("server_host", "")
            if host:
                ok = self._ping(host)
                checks.append({"label": f"Server {host} reachable",
                                "passed": ok, "detail": "ICMP OK" if ok else "Unreachable"})

        ca = config.get("ca_cert_path", "").strip()
        if ca:
            ok = Path(ca).exists()
            checks.append({"label": "CA cert readable", "passed": ok,
                            "detail": ca if ok else "File not found"})

        if not checks:
            checks.append({"label": "Configuration", "passed": True, "detail": "OK"})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        sock_path = config.get("management_socket_path", "").strip()
        if not sock_path:
            return []
        try:
            raw = self._mgmt_command(sock_path, "status 2\n")
        except Exception:
            return []

        nodes = []
        in_client = False
        for line in raw.splitlines():
            if line.startswith("ROUTING TABLE") or line.startswith("GLOBAL STATS"):
                in_client = False
            if line.startswith("Virtual Address"):
                in_client = True
                continue
            if in_client:
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                virtual_ip = parts[0].strip()
                cn = parts[1].strip()
                real_addr = parts[2].strip()
                if not re.match(r"\d+\.\d+\.\d+\.\d+", virtual_ip):
                    continue
                nodes.append({
                    "address": virtual_ip,
                    "hostname": cn or virtual_ip,
                    "tags": [],
                    "metadata": {"common_name": cn, "real_address": real_addr},
                })
        return nodes

    def check_health(self, config: dict) -> dict:
        sock_path = config.get("management_socket_path", "").strip()
        if sock_path:
            ok, detail = self._mgmt_connect(sock_path)
            if ok:
                nodes = self.discover_nodes(config)
                return {"status": "healthy", "detail": {
                    "management_socket": True, "clients": len(nodes),
                }}
            return {"status": "down", "detail": {"management_socket": False, "error": detail}}

        host = config.get("server_host", "")
        if host and self._ping(host):
            return {"status": "healthy", "detail": {"server_reachable": True}}
        return {"status": "down", "detail": {"server_reachable": False}}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        return any(n["address"] == address for n in nodes)

    # -- internals --

    @staticmethod
    def _mgmt_connect(sock_path: str) -> tuple[bool, str]:
        p = Path(sock_path)
        if not p.exists():
            return False, f"Socket not found: {sock_path}"
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(str(p))
            banner = s.recv(1024).decode("utf-8", errors="replace")
            s.close()
            return True, banner.strip()[:100]
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _mgmt_command(sock_path: str, cmd: str) -> str:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(sock_path)
        s.recv(1024)
        s.sendall(cmd.encode())
        data = b""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"END\r\n" in data or b"END\n" in data:
                    break
            except socket.timeout:
                break
        s.close()
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _ping(host: str) -> bool:
        if not host:
            return False
        try:
            r = subprocess.run(["ping", "-c", "1", "-W", "3", host],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False
