"""Nexus fabric adapter — Nebula (Slack/Defined overlay)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import FabricAdapter, register_adapter


@register_adapter
class NebulaAdapter(FabricAdapter):
    TYPE_ID = "nebula"
    DISPLAY_NAME = "Nebula"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "config_path",  "type": "text", "label": "Config Path",
         "required": True,  "hint": "/etc/nebula/config.yml"},
        {"name": "lighthouse",   "type": "text", "label": "Lighthouse IP",
         "required": False, "hint": "Lighthouse nebula IP for health check"},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 180

    def test_connection(self, config: dict) -> dict:
        checks = []
        nebula = shutil.which("nebula")
        checks.append({"label": "nebula binary", "passed": nebula is not None,
                        "detail": nebula or "Not found in PATH"})

        cfg = config.get("config_path", "")
        if cfg:
            ok = Path(cfg).exists()
            checks.append({"label": "Config file", "passed": ok,
                            "detail": cfg if ok else "File not found"})

        lh = config.get("lighthouse", "").strip()
        if lh:
            ok = self._ping(lh)
            checks.append({"label": f"Lighthouse {lh} reachable",
                            "passed": ok, "detail": "ICMP OK" if ok else "Unreachable"})

        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        nebula_cert = shutil.which("nebula-cert")
        cfg_path = config.get("config_path", "")
        if not cfg_path or not Path(cfg_path).exists():
            return []

        try:
            import yaml
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            return []

        nodes = []
        lhs = cfg.get("lighthouse", {}).get("hosts", [])
        if isinstance(lhs, list):
            for ip in lhs:
                if isinstance(ip, str):
                    nodes.append({
                        "address": ip, "hostname": ip,
                        "tags": ["lighthouse"],
                        "metadata": {"role": "lighthouse"},
                    })

        static_map = cfg.get("static_host_map", {})
        if isinstance(static_map, dict):
            for neb_ip, endpoints in static_map.items():
                if neb_ip not in {n["address"] for n in nodes}:
                    nodes.append({
                        "address": neb_ip, "hostname": neb_ip,
                        "tags": [],
                        "metadata": {"static_endpoints": endpoints},
                    })
        return nodes

    def check_health(self, config: dict) -> dict:
        nebula = shutil.which("nebula")
        if not nebula:
            return {"status": "down", "detail": {"binary_found": False}}

        cfg = config.get("config_path", "")
        cfg_exists = Path(cfg).exists() if cfg else False

        lh = config.get("lighthouse", "").strip()
        lh_ok = self._ping(lh) if lh else None

        if cfg_exists and (lh_ok is True or lh_ok is None):
            status = "healthy"
        elif cfg_exists:
            status = "degraded"
        else:
            status = "down"

        return {"status": status, "detail": {
            "binary_found": True, "config_exists": cfg_exists,
            "lighthouse_reachable": lh_ok,
        }}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        return self._ping(address)

    # -- internals --

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
