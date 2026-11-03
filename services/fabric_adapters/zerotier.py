"""Nexus fabric adapter — ZeroTier."""
from __future__ import annotations

from .base import FabricAdapter, register_adapter

try:
    import requests as _req
except ImportError:
    _req = None

_LOCAL_API = "http://localhost:9993"
_CENTRAL_API = "https://api.zerotier.com/api/v1"
_TIMEOUT = 10


@register_adapter
class ZeroTierAdapter(FabricAdapter):
    TYPE_ID = "zerotier"
    DISPLAY_NAME = "ZeroTier"
    CATEGORY = "overlay"
    FIELDS = [
        {"name": "network_id",    "type": "text",     "label": "Network ID",
         "required": True,  "hint": "16-char ZeroTier network ID"},
        {"name": "api_token",     "type": "password", "label": "API Token",
         "required": False, "hint": "ZeroTier Central API token (optional)", "secret": True},
        {"name": "local_token",   "type": "password", "label": "Local Auth Token",
         "required": False, "hint": "From /var/lib/zerotier-one/authtoken.secret", "secret": True},
    ]

    def get_dispatch_modes(self) -> list[str]:
        return ["ssh"]

    def get_probe_interval(self) -> int:
        return 180

    def test_connection(self, config: dict) -> dict:
        checks = []
        if _req is None:
            checks.append({"label": "requests library", "passed": False,
                            "detail": "Python requests not installed"})
            return {"ok": False, "checks": checks}

        local_token = config.get("local_token", "").strip()
        if local_token:
            ok, detail = self._local_status(local_token)
            checks.append({"label": "Local ZeroTier service", "passed": ok, "detail": detail})

        api_token = config.get("api_token", "").strip()
        nid = config.get("network_id", "")
        if api_token and nid:
            ok, detail = self._central_network(api_token, nid)
            checks.append({"label": "Central API", "passed": ok, "detail": detail})

        if not checks:
            checks.append({"label": "Configuration", "passed": True,
                            "detail": "No tokens provided; passive mode"})
        return {"ok": all(c["passed"] for c in checks), "checks": checks}

    def discover_nodes(self, config: dict) -> list[dict]:
        api_token = config.get("api_token", "").strip()
        nid = config.get("network_id", "")
        if not api_token or not nid:
            return self._discover_local(config)
        return self._discover_central(api_token, nid)

    def check_health(self, config: dict) -> dict:
        api_token = config.get("api_token", "").strip()
        nid = config.get("network_id", "")
        if api_token and nid:
            members = self._fetch_central_members(api_token, nid)
            if members is None:
                return {"status": "down", "detail": {"api_reachable": False}}
            online = sum(1 for m in members if m.get("online", False))
            total = len(members)
            status = "healthy" if online > 0 else ("degraded" if total > 0 else "down")
            return {"status": status, "detail": {
                "api_reachable": True, "total_members": total, "online": online,
            }}

        local_token = config.get("local_token", "").strip()
        if local_token:
            ok, detail = self._local_status(local_token)
            return {"status": "healthy" if ok else "down", "detail": {"local_service": ok, "info": detail}}

        return {"status": "unknown", "detail": {"error": "No tokens configured"}}

    def check_node_reachability(self, config: dict, address: str) -> bool:
        nodes = self.discover_nodes(config)
        return any(n["address"] == address for n in nodes
                   if n.get("metadata", {}).get("online", False))

    # -- internals --

    @staticmethod
    def _local_status(local_token: str) -> tuple[bool, str]:
        try:
            r = _req.get(f"{_LOCAL_API}/status",
                          headers={"X-ZT1-Auth": local_token}, timeout=_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return True, f"Node {data.get('address', '?')} online={data.get('online', '?')}"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _central_network(api_token: str, nid: str) -> tuple[bool, str]:
        try:
            r = _req.get(f"{_CENTRAL_API}/network/{nid}",
                          headers={"Authorization": f"token {api_token}"}, timeout=_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return True, f"Network '{data.get('config', {}).get('name', nid)}'"
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _fetch_central_members(api_token: str, nid: str):
        try:
            r = _req.get(f"{_CENTRAL_API}/network/{nid}/member",
                          headers={"Authorization": f"token {api_token}"}, timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def _discover_central(self, api_token: str, nid: str) -> list[dict]:
        members = self._fetch_central_members(api_token, nid)
        if not members:
            return []
        nodes = []
        for m in members:
            cfg = m.get("config", {})
            addrs = cfg.get("ipAssignments", [])
            ip = addrs[0] if addrs else ""
            if not ip:
                continue
            nodes.append({
                "address": ip,
                "hostname": m.get("name", "") or m.get("description", "") or ip,
                "tags": [],
                "metadata": {
                    "node_id": m.get("nodeId", ""),
                    "online": m.get("online", False),
                    "authorized": cfg.get("authorized", False),
                    "addresses": addrs,
                },
            })
        return nodes

    def _discover_local(self, config: dict) -> list[dict]:
        local_token = config.get("local_token", "").strip()
        nid = config.get("network_id", "")
        if not local_token or not nid or _req is None:
            return []
        try:
            r = _req.get(f"{_LOCAL_API}/network/{nid}",
                          headers={"X-ZT1-Auth": local_token}, timeout=_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                addrs = data.get("assignedAddresses", [])
                ip = addrs[0].split("/")[0] if addrs else ""
                if ip:
                    return [{"address": ip, "hostname": ip, "tags": [],
                             "metadata": {"local": True, "addresses": addrs}}]
        except Exception:
            pass
        return []
