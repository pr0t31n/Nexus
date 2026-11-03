"""Nexus v2.0 — Application configuration."""
from __future__ import annotations

from pathlib import Path

NEXUS_DIR = Path(__file__).parent.resolve()
NEXUS_PORT = 5400

# Tool registry — services Nexus discovers on the fleet.
# Phase 2 fleet_discovery uses this to know what ports to probe.
TOOL_REGISTRY = [
    {"id": "gauntlet",   "name": "Gauntlet",   "port": 5000, "proto": "https",
     "desc": "Hardware Assessment",         "status_api": True,  "icon": "chip"},
    {"id": "vantage",    "name": "Vantage",    "port": 5100, "proto": "https",
     "desc": "Network & OSINT Recon",       "status_api": True,  "icon": "scope"},
    {"id": "quiver",     "name": "Quiver",     "port": 5200, "proto": "https",
     "desc": "Pentest Hardware Manager",    "status_api": False, "icon": "quiver"},
    {"id": "spectre",    "name": "Spectre",    "port": 5300, "proto": "https",
     "desc": "Wireless Assessment",         "status_api": True,  "icon": "wave"},
    {"id": "nexus",      "name": "Nexus",      "port": 5400, "proto": "https",
     "desc": "Fleet Management Hub",        "status_api": True,  "icon": "nexus"},
    {"id": "crucible",   "name": "Crucible",   "port": 5500, "proto": "https",
     "desc": "Web & API Assessment",        "status_api": True,  "icon": "crucible"},
    {"id": "nessus",     "name": "Nessus",     "port": 8834, "proto": "https",
     "desc": "Vulnerability Scanner",       "status_api": False, "icon": "scan"},
    {"id": "hitman",     "name": "HiTMan",     "port": 8443, "proto": "https",
     "desc": "Security Management",         "status_api": False, "icon": "lock"},
]

# Arsenal install map — maps tool IDs to install scripts
TOOL_INSTALL_MAP: dict[str, str] = {
    "crucible":     "/opt/pr0t31n/Crucible/install.py",
    "vantage":      "/opt/pr0t31n/Vantage/install.py",
    "spectre":      "/opt/pr0t31n/Spectre/install.py",
    "gauntlet":     "/opt/pr0t31n/Gauntlet/install.py",
    "bastion":      "/opt/pr0t31n/Bastion/install.py",
    "fracture":     "/opt/pr0t31n/Fracture/install.py",
    "siphon":       "/opt/pr0t31n/Siphon/install.py",
    "scattershot3": "/opt/pr0t31n/Scattershot3/install.py",
    "osift":        "/opt/pr0t31n/OSift/install.py",
    "gearbox":      "/opt/pr0t31n/Gearbox/install.py",
}

# RBAC roles
ROLES = ("admin", "operator", "viewer")

# Supported fabric types (Phase 2+ adapters implement these)
FABRIC_TYPES = {
    "vpn":     ["openvpn_cloak", "amneziawg", "wireguard", "ipsec_ikev2", "openvpn", "cloudflare_tunnel"],
    "overlay": ["tailscale", "headscale", "zerotier", "nebula", "netbird", "netmaker"],
    "cloud":   ["aws_vpc", "azure_vnet", "gcp_vpc", "linode_vpc", "digitalocean_vpc"],
    "direct":  ["direct_lan", "proxmox"],
}

# Device type enum values
DEVICE_TYPES = (
    "unknown", "laptop", "desktop", "server", "raspberry_pi",
    "vm", "container", "appliance", "nuc", "phone",
)

# OS family enum values
OS_FAMILIES = ("linux", "darwin", "windows", "freebsd", "unknown")

# Architecture enum values
ARCHITECTURES = ("x86_64", "aarch64", "armv7l", "i686", "riscv64", "unknown")
