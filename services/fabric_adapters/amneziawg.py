"""Nexus fabric adapter — AmneziaWG (AmneziaWG (fallback VPN))."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .base import FabricAdapter, register_adapter


def _wg_binary() -> str | None:
    return shutil.which("awg") or shutil.which("wg")


@register_adapter
class AmneziaWGAdapter(FabricAdapter):
    TYPE_ID = "amneziawg"
    DISPLAY_NAME = "AmneziaWG"
    CATEGORY = "vpn"
    FIELDS = [
        {"name": "interface", "type": "text", "label": "Interface",
         "required": True, "hint": "e.g. awg0", "default": "awg0", "secret": False},
        {"name": "subnets", "type": "text", "label": "Subnets",
         "required": True, "hint": "CIDR, one per line", "default": "", "secret": False},
        {"name": "peer_registry_path", "type": "text", "label": "Peer registry (YAML)",
         "required": False, "hint": "/etc/wgacl/peers.yaml", "default": "", "secret": False},
        {"name": "policy_path", "type": "text", "label": "wgacl policy (JSON)",
         "required": False, "hint": "/etc/wgacl/policy.json", "default": "", "secret": False},
        {"name": "endpoint", "type": "text", "label": "Endpoint",
         "required": False, "hint": "Public endpoint host:port", "default": "", "secret": False},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh", "broker"]

    def get_probe_interval(self) -> int:
        return 120

    def test_connection(self, config: dict) -> dict:
        checks = []
        iface = config.get("interface", "awg0")

        up, detail = self._iface_up(iface)
        checks.append({"label": f"Interface {iface} UP", "passed": up, "detail": detail})

        wg_bin = _wg_binary()
        if wg_bin:
            show_ok, show_detail = self._wg_show(wg_bin, iface)
            checks.append({"label": f"{Path(wg_bin).name} show", "passed": show_ok, "detail": show_detail})
        else:
            checks.append({"label": "awg/wg binary", "passed": False, "detail": "Neither awg nor wg found in PATH"})

        peer_reg = config.get("peer_registry_path", "").strip()
        if peer_reg:
            p = Path(peer_reg)
            ok = p.exists() and p.is_file()
            checks.append({"label": "Peer registry readable", "passed": ok,
                            "detail": f"{p}" if ok else "File not found"})

        policy = config.get("policy_path", "").strip()
        if policy:
            ok, detail = self._check_policy(policy)
            checks.append({"label": "wgacl policy", "passed": ok, "detail": detail})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        iface = config.get("interface", "awg0")
        wg_bin = _wg_binary()
        if not wg_bin:
            return []

        try:
            r = subprocess.run(
                [wg_bin, "show", iface, "dump"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return []
        except Exception:
            return []

        peer_map = self._load_peer_registry(config.get("peer_registry_path", ""))
        nodes = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            pubkey = parts[0]
            endpoint = parts[2]
            allowed_ips = parts[3]
            latest_handshake = parts[4]
            transfer_rx = parts[5] if len(parts) > 5 else "0"
            transfer_tx = parts[6] if len(parts) > 6 else "0"

            ip = self._first_ip_from_allowed(allowed_ips)
            if not ip:
                continue

            peer_info = peer_map.get(pubkey, {})
            hostname = peer_info.get("hostname", ip)
            tags = peer_info.get("tags", [])

            nodes.append({
                "address": ip,
                "hostname": hostname,
                "tags": tags,
                "metadata": {
                    "public_key": pubkey,
                    "latest_handshake": int(latest_handshake) if latest_handshake.isdigit() else 0,
                    "transfer_rx": int(transfer_rx) if transfer_rx.isdigit() else 0,
                    "transfer_tx": int(transfer_tx) if transfer_tx.isdigit() else 0,
                    "endpoint": endpoint,
                },
            })
        return nodes

    def check_health(self, config: dict) -> dict:
        iface = config.get("interface", "awg0")
        up, _ = self._iface_up(iface)
        if not up:
            return {"status": "down", "detail": {"interface_up": False, "peers_recent": 0}}

        wg_bin = _wg_binary()
        peers_recent = 0
        peers_total = 0
        now = time.time()
        if wg_bin:
            try:
                r = subprocess.run(
                    [wg_bin, "show", iface, "dump"],
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

        policy_ok = True
        policy_path = config.get("policy_path", "").strip()
        if policy_path:
            policy_ok, _ = self._check_policy(policy_path)

        if peers_recent > 0 and policy_ok:
            status = "healthy"
        elif up:
            status = "degraded"
        else:
            status = "down"

        return {
            "status": status,
            "detail": {
                "interface_up": up,
                "peers_total": peers_total,
                "peers_recent": peers_recent,
                "policy_ok": policy_ok,
            },
        }

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
                r = subprocess.run(
                    ["ip", "link", "show", iface],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode != 0:
                    return False, f"Interface {iface} not found"
                if "UP" in r.stdout:
                    return True, "UP"
                return False, "DOWN"
            r = subprocess.run(
                ["ifconfig", iface], capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                return False, f"Interface {iface} not found"
            if "UP" in r.stdout:
                return True, "UP"
            return False, "DOWN"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _wg_show(wg_bin: str, iface: str) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                [wg_bin, "show", iface],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                peers = r.stdout.count("peer:")
                return True, f"{peers} peer(s) configured"
            return False, r.stderr.strip()[:200] if r.stderr else "Command failed"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _check_policy(policy_path: str) -> tuple[bool, str]:
        p = Path(policy_path)
        if not p.exists():
            return False, f"Policy file not found: {policy_path}"
        try:
            data = json.loads(p.read_text())
            rules = len(data) if isinstance(data, list) else len(data.get("rules", []))
            return True, f"Policy compiles ({rules} rules)"
        except Exception as e:
            return False, f"Policy parse error: {e}"

    @staticmethod
    def _load_peer_registry(path: str) -> dict:
        if not path:
            return {}
        p = Path(path)
        if not p.exists():
            return {}
        try:
            import yaml
            data = yaml.safe_load(p.read_text())
            if not isinstance(data, dict):
                return {}
            out = {}
            peers = data.get("peers", data)
            if isinstance(peers, list):
                for entry in peers:
                    pk = entry.get("public_key", entry.get("publickey", ""))
                    if pk:
                        out[pk] = {
                            "hostname": entry.get("hostname", entry.get("name", "")),
                            "tags": entry.get("tags", []),
                        }
            elif isinstance(peers, dict):
                for pk, info in peers.items():
                    if isinstance(info, dict):
                        out[pk] = {
                            "hostname": info.get("hostname", info.get("name", "")),
                            "tags": info.get("tags", []),
                        }
            return out
        except ImportError:
            return {}
        except Exception:
            return {}

    @staticmethod
    def _first_ip_from_allowed(allowed_ips: str) -> str:
        for cidr in allowed_ips.split(","):
            cidr = cidr.strip()
            if "/" in cidr:
                ip = cidr.split("/")[0]
                if ip and ip != "0.0.0.0":
                    return ip
        return ""
