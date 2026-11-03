"""Nexus fabric adapter package — auto-discovers and registers adapters."""
from __future__ import annotations

from .base import (
    FabricAdapter,
    get_adapter,
    get_all_adapters,
    load_adapters,
    register_adapter,
)
