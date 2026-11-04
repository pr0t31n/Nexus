"""Nexus v2.5 — Application configuration."""
from __future__ import annotations

from pathlib import Path

NEXUS_DIR = Path(__file__).parent.resolve()
NEXUS_PORT = 6000
BROKER_PORT = 5401

# Tool registry — services Nexus discovers on the fleet.
TOOL_REGISTRY = [
    {"id": "crucible",   "name": "Crucible",   "port": 5100, "proto": "https",
     "desc": "Pentest Orchestration",       "status_api": True,  "icon": "crucible"},
    {"id": "gauntlet",   "name": "Gauntlet",   "port": 5700, "proto": "https",
     "desc": "Hardware Assessment",         "status_api": True,  "icon": "chip"},
    {"id": "quiver",     "name": "Quiver",     "port": 5200, "proto": "https",
     "desc": "Pentest Hardware Manager",    "status_api": False, "icon": "quiver"},
    {"id": "nexus",      "name": "Nexus",      "port": 6000, "proto": "https",
     "desc": "Fleet Management Hub",        "status_api": True,  "icon": "nexus"},
    {"id": "polaris",    "name": "Polaris",    "port": 5900, "proto": "https",
     "desc": "Tunnel Manager",              "status_api": True,  "icon": "tunnel"},
    {"id": "beacon",     "name": "Beacon",     "port": 9443, "proto": "https",
     "desc": "Engagement Status",           "status_api": False, "icon": "signal"},
    {"id": "nessus",     "name": "Nessus",     "port": 8834, "proto": "https",
     "desc": "Vulnerability Scanner",       "status_api": False, "icon": "scan"},
]

# Arsenal install map — maps tool IDs to install scripts
TOOL_INSTALL_MAP: dict[str, str] = {
    "crucible":     "/opt/pr0t31n/Crucible/install.py",
    "gauntlet":     "/opt/pr0t31n/Gauntlet/install.py",
    "bastion":      "/opt/pr0t31n/Bastion/install.py",
    "fracture":     "/opt/pr0t31n/Fracture/install.py",
    "siphon":       "/opt/pr0t31n/Siphon/install.py",
    "scattershot3": "/opt/pr0t31n/Scattershot3/install.py",
    "osift":        "/opt/pr0t31n/OSift/install.py",
    "gearbox":      "/opt/pr0t31n/Gearbox/install.py",
    "beacon":       "/opt/pr0t31n/Beacon/install.py",
    "polaris":      "/opt/pr0t31n/Polaris/install.py",
}

# RBAC roles
ROLES = ("admin", "operator", "viewer")

# Supported fabric types — all types Nexus knows about.
FABRIC_TYPES: dict[str, list[str]] = {
    "vpn":     ["openvpn_cloak", "amneziawg", "wireguard", "ipsec_ikev2", "openvpn", "cloudflare_tunnel"],
    "overlay": ["tailscale", "headscale", "zerotier", "nebula", "netbird", "netmaker"],
    "cloud":   ["aws_vpc", "azure_vnet", "gcp_vpc", "linode_vpc", "digitalocean_vpc"],
    "direct":  ["direct_lan", "proxmox"],
}

# Wizard field definitions per fabric type.
# Each field: name, type (text|password|textarea|number|select), label, required, hint, default, secret
FABRIC_TYPE_FIELDS: dict[str, list[dict]] = {
    "openvpn_cloak": [
        {"name": "server_host",              "type": "text",     "label": "Server Host",             "required": True,  "hint": "VPN server hostname or IP"},
        {"name": "vlan_id",                  "type": "select",   "label": "VLAN",                    "required": True,  "hint": "VLAN0 (admin) or VLAN1 (agent)", "options": ["vlan0", "vlan1"]},
        {"name": "management_socket_path",   "type": "text",     "label": "Management Socket Path",  "required": True,  "hint": "e.g. /run/openvpn/vlan0.sock",  "default": "/run/openvpn/management.sock"},
        {"name": "ca_cert_path",             "type": "text",     "label": "CA Certificate Path",     "required": False, "hint": "Path to CA cert for expiry check"},
        {"name": "crl_path",                 "type": "text",     "label": "CRL Path",                "required": False, "hint": "Path to CRL for freshness check"},
        {"name": "cloak_uid",               "type": "text",     "label": "Cloak UID",               "required": False, "hint": "Cloak obfuscation UID"},
        {"name": "tls_crypt_v2_key_path",   "type": "text",     "label": "tls-crypt-v2 Key Path",   "required": False, "hint": "Path to tls-crypt-v2 server key"},
    ],
    "amneziawg": [
        {"name": "interface",                "type": "text",     "label": "Interface",               "required": True,  "hint": "e.g. awg0",                     "default": "awg0"},
        {"name": "peer_registry_path",       "type": "text",     "label": "Peer Registry Path",      "required": False, "hint": "/etc/wgacl/peers.yaml",         "default": "/etc/wgacl/peers.yaml"},
        {"name": "policy_path",              "type": "text",     "label": "Policy Path",             "required": False, "hint": "/etc/wgacl/policy.json",        "default": "/etc/wgacl/policy.json"},
        {"name": "endpoint",                 "type": "text",     "label": "Endpoint",                "required": False, "hint": "Public endpoint host:port"},
    ],
    "wireguard": [
        {"name": "interface",                "type": "text",     "label": "Interface",               "required": True,  "hint": "e.g. wg0",                      "default": "wg0"},
        {"name": "endpoint",                 "type": "text",     "label": "Endpoint",                "required": False, "hint": "Public endpoint host:port"},
        {"name": "listen_port",              "type": "number",   "label": "Listen Port",             "required": False, "hint": "WireGuard listen port"},
        {"name": "private_key",              "type": "password", "label": "Private Key",             "required": False, "hint": "Interface private key",          "secret": True},
    ],
    "ipsec_ikev2": [
        {"name": "gateway",                  "type": "text",     "label": "Gateway",                 "required": True,  "hint": "IPSec gateway hostname/IP"},
        {"name": "identity",                 "type": "text",     "label": "Identity",                "required": True,  "hint": "IKEv2 identity string"},
        {"name": "certificate_path",         "type": "text",     "label": "Certificate Path",        "required": False, "hint": "Path to client certificate"},
        {"name": "ca_cert_path",             "type": "text",     "label": "CA Certificate Path",     "required": False},
        {"name": "psk",                      "type": "password", "label": "Pre-Shared Key",          "required": False, "secret": True},
    ],
    "openvpn": [
        {"name": "server_host",              "type": "text",     "label": "Server Host",             "required": True,  "hint": "OpenVPN server hostname/IP"},
        {"name": "server_port",              "type": "number",   "label": "Server Port",             "required": True,  "hint": "OpenVPN server port",           "default": "1194"},
        {"name": "protocol",                 "type": "select",   "label": "Protocol",                "required": False, "options": ["udp", "tcp"],               "default": "udp"},
        {"name": "management_socket_path",   "type": "text",     "label": "Management Socket Path",  "required": False},
        {"name": "ca_cert_path",             "type": "text",     "label": "CA Certificate Path",     "required": False},
    ],
    "cloudflare_tunnel": [
        {"name": "tunnel_token",             "type": "password", "label": "Tunnel Token",            "required": True,  "hint": "Cloudflare tunnel token",       "secret": True},
        {"name": "account_id",               "type": "text",     "label": "Account ID",              "required": True,  "hint": "Cloudflare account ID"},
        {"name": "tunnel_id",                "type": "text",     "label": "Tunnel ID",               "required": True,  "hint": "Cloudflare tunnel UUID"},
    ],
    "tailscale": [
        {"name": "api_key",                  "type": "password", "label": "API Key",                 "required": True,  "hint": "Tailscale API key",             "secret": True},
        {"name": "tailnet",                  "type": "text",     "label": "Tailnet",                 "required": True,  "hint": "Tailnet name or domain"},
    ],
    "headscale": [
        {"name": "control_url",              "type": "text",     "label": "Control URL",             "required": True,  "hint": "Headscale control server URL"},
        {"name": "api_key",                  "type": "password", "label": "API Key",                 "required": True,  "hint": "Headscale API key",             "secret": True},
        {"name": "namespace",                "type": "text",     "label": "Namespace",               "required": False},
    ],
    "zerotier": [
        {"name": "api_key",                  "type": "password", "label": "API Key",                 "required": True,  "hint": "ZeroTier Central API key",      "secret": True},
        {"name": "network_id",               "type": "text",     "label": "Network ID",              "required": True,  "hint": "16-char network ID"},
        {"name": "controller_url",           "type": "text",     "label": "Controller URL",          "required": False, "hint": "Self-hosted controller URL"},
    ],
    "nebula": [
        {"name": "ca_cert_path",             "type": "text",     "label": "CA Certificate Path",     "required": True,  "hint": "Path to Nebula CA cert"},
        {"name": "lighthouse_hosts",         "type": "textarea", "label": "Lighthouse Hosts",        "required": False, "hint": "One per line: ip:port"},
    ],
    "netbird": [
        {"name": "api_url",                  "type": "text",     "label": "API URL",                 "required": True,  "hint": "NetBird management API URL"},
        {"name": "api_key",                  "type": "password", "label": "API Key",                 "required": True,  "hint": "NetBird API key",               "secret": True},
    ],
    "netmaker": [
        {"name": "api_url",                  "type": "text",     "label": "API URL",                 "required": True,  "hint": "Netmaker API URL"},
        {"name": "api_key",                  "type": "password", "label": "API Key",                 "required": True,  "hint": "Netmaker API key",              "secret": True},
        {"name": "network_name",             "type": "text",     "label": "Network Name",            "required": True,  "hint": "Netmaker network name"},
    ],
    "aws_vpc": [
        {"name": "region",                   "type": "text",     "label": "Region",                  "required": True,  "hint": "e.g. us-east-1"},
        {"name": "vpc_id",                   "type": "text",     "label": "VPC ID",                  "required": True,  "hint": "vpc-xxxxxxxx"},
        {"name": "access_key_id",            "type": "password", "label": "Access Key ID",           "required": True,  "hint": "AWS access key",                "secret": True},
        {"name": "secret_access_key",        "type": "password", "label": "Secret Access Key",       "required": True,  "hint": "AWS secret key",                "secret": True},
        {"name": "session_token",            "type": "password", "label": "Session Token",           "required": False, "hint": "Temporary session token",       "secret": True},
        {"name": "role_arn",                 "type": "text",     "label": "Role ARN",                "required": False, "hint": "IAM role to assume"},
    ],
    "azure_vnet": [
        {"name": "subscription_id",          "type": "text",     "label": "Subscription ID",         "required": True},
        {"name": "resource_group",           "type": "text",     "label": "Resource Group",          "required": True},
        {"name": "vnet_name",                "type": "text",     "label": "VNet Name",               "required": True},
        {"name": "tenant_id",                "type": "text",     "label": "Tenant ID",               "required": True},
        {"name": "client_id",                "type": "text",     "label": "Client ID",               "required": True},
        {"name": "client_secret",            "type": "password", "label": "Client Secret",           "required": True,  "secret": True},
    ],
    "gcp_vpc": [
        {"name": "project_id",               "type": "text",     "label": "Project ID",              "required": True},
        {"name": "network_name",             "type": "text",     "label": "Network Name",            "required": True},
        {"name": "service_account_key",      "type": "textarea", "label": "Service Account Key JSON", "required": True, "secret": True},
    ],
    "linode_vpc": [
        {"name": "api_token",                "type": "password", "label": "API Token",               "required": True,  "hint": "Linode personal access token",  "secret": True},
        {"name": "vpc_id",                   "type": "text",     "label": "VPC ID",                  "required": True},
        {"name": "region",                   "type": "text",     "label": "Region",                  "required": False},
    ],
    "digitalocean_vpc": [
        {"name": "api_token",                "type": "password", "label": "API Token",               "required": True,  "hint": "DigitalOcean API token",        "secret": True},
        {"name": "vpc_id",                   "type": "text",     "label": "VPC ID",                  "required": True},
        {"name": "region",                   "type": "text",     "label": "Region",                  "required": False},
    ],
    "direct_lan": [
        {"name": "interface",                "type": "text",     "label": "Interface",               "required": True,  "hint": "e.g. eth0, en0"},
        {"name": "gateway",                  "type": "text",     "label": "Gateway",                 "required": False, "hint": "Default gateway IP"},
        {"name": "vlan_tag",                 "type": "number",   "label": "VLAN Tag",                "required": False, "hint": "802.1Q VLAN tag (if applicable)"},
    ],
    "proxmox": [
        {"name": "api_url",                  "type": "text",     "label": "API URL",                 "required": True,  "hint": "https://proxmox:8006/api2/json"},
        {"name": "api_token",                "type": "password", "label": "API Token",               "required": False, "hint": "user@pam!tokenid=secret",       "secret": True},
        {"name": "username",                 "type": "text",     "label": "Username",                "required": False, "hint": "Alternative: user@pam"},
        {"name": "password",                 "type": "password", "label": "Password",                "required": False, "secret": True},
        {"name": "node_name",               "type": "text",     "label": "PVE Node Name",           "required": False, "hint": "Proxmox node to query"},
    ],
}

# VM role classifications
VM_ROLES = (
    "vpn_concentrator", "exfil_listeners", "phishing",
    "internal_bridge", "windows_test", "scanner", "general",
)

# Device type enum values
DEVICE_TYPES = (
    "unknown", "laptop", "desktop", "server", "raspberry_pi",
    "vm", "container", "appliance", "nuc", "phone",
)

# OS family enum values
OS_FAMILIES = ("linux", "darwin", "windows", "freebsd", "unknown")

# Architecture enum values
ARCHITECTURES = ("x86_64", "aarch64", "armv7l", "i686", "riscv64", "unknown")

# Retention defaults (days) — Phase 6
RETENTION_SESSIONS_DAYS = 365
RETENTION_PORTS_DAYS = 1095
RETENTION_AUDIT_DAYS = 90
RETENTION_NODE_HISTORY_DAYS = 1095

# Monitoring intervals (seconds) — Phase 6
SESSION_MONITOR_INTERVAL = 30
PORT_MONITOR_INTERVAL = 60

# Audit event types — Phase 6
AUDIT_EVENT_TYPES = (
    "auth", "config", "fleet", "fabric", "tool",
    "credential", "tunnel", "arsenal", "enrollment",
    "dispatch", "session", "port", "system",
)

# Audit severity levels — Phase 6
AUDIT_SEVERITIES = ("debug", "info", "warning", "error", "critical")
