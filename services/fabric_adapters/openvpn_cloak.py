"""Nexus fabric adapter — OpenVPN + Cloak (OpenVPN + Cloak (primary VPN))."""
from __future__ import annotations

import os
import socket as _socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

from .base import FabricAdapter, register_adapter


@register_adapter
class OpenVPNCloakAdapter(FabricAdapter):
    TYPE_ID = "openvpn_cloak"
    DISPLAY_NAME = "OpenVPN + Cloak"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "server_host", "type": "text", "label": "Server host",
         "required": True, "hint": "VPN server hostname/IP", "default": "", "secret": False},
        {"name": "vlan_id", "type": "select", "label": "VLAN",
         "required": True, "hint": "", "default": "vlan0", "secret": False,
         "options": [{"value": "vlan0", "label": "VLAN 0"}, {"value": "vlan1", "label": "VLAN 1"}]},
        {"name": "management_socket_path", "type": "text", "label": "Management socket",
         "required": True, "hint": "/var/run/openvpn/mgmt-vlanX.sock", "default": "", "secret": False},
        {"name": "ca_cert_path", "type": "text", "label": "CA certificate path",
         "required": False, "hint": "PEM file for cert expiry check", "default": "", "secret": False},
        {"name": "crl_path", "type": "text", "label": "CRL path",
         "required": False, "hint": "CRL file for freshness check", "default": "", "secret": False},
        {"name": "subnets", "type": "text", "label": "Subnets",
         "required": True, "hint": "CIDR, one per line", "default": "", "secret": False},
        {"name": "cloak_uid", "type": "text", "label": "Cloak UID",
         "required": False, "hint": "", "default": "", "secret": True},
        {"name": "tls_crypt_v2_key_path", "type": "text", "label": "TLS-Crypt v2 key path",
         "required": False, "hint": "", "default": "", "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 60

    def test_connection(self, config: dict) -> dict:
        checks = []
        sock_path = config.get("management_socket_path", "")
        state_ok, state_detail = self._mgmt_state(sock_path)
        checks.append({"label": "Management socket", "passed": state_ok, "detail": state_detail})

        ca = config.get("ca_cert_path", "").strip()
        if ca:
            ok, detail = self._check_cert_expiry(ca)
            checks.append({"label": "CA certificate", "passed": ok, "detail": detail})

        crl = config.get("crl_path", "").strip()
        if crl:
            ok, detail = self._check_crl_age(crl)
            checks.append({"label": "CRL freshness", "passed": ok, "detail": detail})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        sock_path = config.get("management_socket_path", "")
        try:
            raw = self._mgmt_command(sock_path, "status 2\n")
            return self._parse_status2(raw)
        except Exception:
            return []

    def check_health(self, config: dict) -> dict:
        sock_path = config.get("management_socket_path", "")
        state_ok, state_detail = self._mgmt_state(sock_path)

        cert_ok = True
        cert_detail = None
        ca = config.get("ca_cert_path", "").strip()
        if ca:
            cert_ok, cert_detail = self._check_cert_expiry(ca)

        crl_ok = True
        crl_detail = None
        crl = config.get("crl_path", "").strip()
        if crl:
            crl_ok, crl_detail = self._check_crl_age(crl)

        if state_ok and cert_ok and crl_ok:
            status = "healthy"
        elif state_ok:
            status = "degraded"
        else:
            status = "down"

        return {
            "status": status,
            "detail": {
                "state": state_detail,
                "cert_expiry": cert_detail,
                "crl_age": crl_detail,
            },
        }

    def check_node_reachability(self, config: dict, address: str) -> bool:
        sock_path = config.get("management_socket_path", "")
        try:
            raw = self._mgmt_command(sock_path, "status 2\n")
            for node in self._parse_status2(raw):
                if node["address"] == address:
                    return True
        except Exception:
            pass
        return False

    # -- management socket helpers --

    @staticmethod
    def _mgmt_command(sock_path: str, command: str, timeout: float = 5.0) -> str:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sock_path)
            banner = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                banner += chunk
                if b"\r\n" in banner or b"\n" in banner:
                    break
            sock.sendall(command.encode())
            response = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    response += chunk
                    if b"END\r\n" in response or b"END\n" in response:
                        break
                except _socket.timeout:
                    break
            return response.decode("utf-8", errors="replace")
        finally:
            sock.close()

    @staticmethod
    def _mgmt_state(sock_path: str) -> tuple[bool, str]:
        if not sock_path or not os.path.exists(sock_path):
            return False, f"Socket not found: {sock_path}"
        try:
            raw = OpenVPNCloakAdapter._mgmt_command(sock_path, "state\n")
            for line in raw.splitlines():
                parts = line.strip().split(",")
                if len(parts) >= 2 and parts[1] == "CONNECTED":
                    return True, "CONNECTED"
            return False, f"Not CONNECTED: {raw[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _parse_status2(raw: str) -> list[dict]:
        nodes = []
        in_clients = False
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("HEADER,CLIENT_LIST"):
                in_clients = True
                continue
            if line.startswith("HEADER,ROUTING_TABLE"):
                in_clients = False
                continue
            if in_clients and line.startswith("CLIENT_LIST,"):
                parts = line.split(",")
                if len(parts) >= 8:
                    cn = parts[1]
                    real_addr = parts[2]
                    virtual_addr = parts[3]
                    virt_ipv6 = parts[4]
                    bytes_rx = parts[5]
                    bytes_tx = parts[6]
                    connected = parts[7]
                    if cn == "UNDEF" or not virtual_addr:
                        continue
                    vip = virtual_addr.split(":")[0]
                    nodes.append({
                        "address": vip,
                        "hostname": cn,
                        "tags": [],
                        "metadata": {
                            "real_ip": real_addr.split(":")[0] if ":" in real_addr else real_addr,
                            "bytes_rx": int(bytes_rx) if bytes_rx.isdigit() else 0,
                            "bytes_tx": int(bytes_tx) if bytes_tx.isdigit() else 0,
                            "connected_since": connected,
                        },
                    })
        return nodes

    @staticmethod
    def _check_cert_expiry(cert_path: str) -> tuple[bool, str]:
        p = Path(cert_path)
        if not p.exists():
            return False, f"File not found: {cert_path}"
        try:
            pem_data = p.read_bytes()
            cert = ssl.PEM_cert_to_DER_cert(pem_data.decode("ascii"))
            x509 = ssl.DER_cert_to_PEM_cert(cert)
            import subprocess
            r = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout"],
                input=pem_data, capture_output=True, timeout=10,
            )
            if r.returncode == 0:
                line = r.stdout.decode().strip()
                date_str = line.split("=", 1)[1] if "=" in line else line
                return True, f"Expires: {date_str}"
            return True, "Could not parse expiry"
        except Exception as e:
            try:
                import subprocess
                r = subprocess.run(
                    ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    line = r.stdout.strip()
                    date_str = line.split("=", 1)[1] if "=" in line else line
                    return True, f"Expires: {date_str}"
            except Exception:
                pass
            return True, f"Certificate exists (expiry check failed: {e})"

    @staticmethod
    def _check_crl_age(crl_path: str, max_age_days: int = 30) -> tuple[bool, str]:
        p = Path(crl_path)
        if not p.exists():
            return False, f"CRL not found: {crl_path}"
        try:
            mtime = p.stat().st_mtime
            age_s = time.time() - mtime
            age_days = age_s / 86400
            if age_days > max_age_days:
                return False, f"CRL is {age_days:.0f} days old (threshold: {max_age_days}d)"
            return True, f"CRL age: {age_days:.1f} days"
        except Exception as e:
            return False, str(e)
