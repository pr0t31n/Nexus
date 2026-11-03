"""Nexus v2.0 — Fabric adapter base class and registry."""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

log = logging.getLogger("nexus.fabric")

_REGISTRY: dict[str, type[FabricAdapter]] = {}


class FabricAdapter:
    TYPE_ID: str = ""
    DISPLAY_NAME: str = ""
    CATEGORY: str = ""
    FIELDS: list[dict] = []

    def test_connection(self, config: dict) -> dict:
        return {"ok": False, "checks": [{"label": "Not implemented", "passed": False, "detail": ""}]}

    def discover_nodes(self, config: dict) -> list[dict]:
        return []

    def check_health(self, config: dict) -> dict:
        return {"status": "unknown", "detail": {}}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        return False

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 300


def register_adapter(cls: type[FabricAdapter]) -> type[FabricAdapter]:
    if cls.TYPE_ID:
        _REGISTRY[cls.TYPE_ID] = cls
    return cls


def get_adapter(type_id: str) -> FabricAdapter | None:
    cls = _REGISTRY.get(type_id)
    return cls() if cls else None


def get_all_adapters() -> list[dict]:
    out = []
    for tid in sorted(_REGISTRY):
        cls = _REGISTRY[tid]
        out.append({
            "type_id": cls.TYPE_ID,
            "display_name": cls.DISPLAY_NAME,
            "category": cls.CATEGORY,
            "fields": cls.FIELDS,
            "dispatch_modes": cls().get_dispatch_modes(),
            "probe_interval": cls().get_probe_interval(),
        })
    return out


def load_adapters() -> None:
    pkg_dir = Path(__file__).parent
    pkg_name = __name__.rsplit(".", 1)[0]
    for f in sorted(pkg_dir.glob("*.py")):
        if f.name.startswith("_") or f.name == "base.py":
            continue
        mod_name = f".{f.stem}"
        try:
            importlib.import_module(mod_name, package=pkg_name)
        except Exception as e:
            log.warning("Failed to load adapter %s: %s", f.stem, e)


load_adapters()
