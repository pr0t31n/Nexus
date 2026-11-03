"""Nexus v2.3 — Database schema and connection management."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

_DB_PATH: Path | None = None
_local = threading.local()

SCHEMA_VERSION = 4


def init_db(db_path: Path) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    _migrate(conn)
    _seed_defaults(conn)
    conn.commit()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        if _DB_PATH is None:
            raise RuntimeError("Database not initialised — call init_db() first")
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def dict_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return dict(row)


def dict_rows(rows: list) -> list[dict]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_TABLES = """
-- Fleet nodes (physical or virtual machines)
CREATE TABLE IF NOT EXISTS fleet_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT NOT NULL,
    display_name    TEXT,
    os_family       TEXT DEFAULT '',
    arch            TEXT DEFAULT '',
    device_type     TEXT DEFAULT 'unknown',
    role            TEXT DEFAULT '',
    vm_role         TEXT,
    ip_primary      TEXT,
    last_seen       TEXT,
    status          TEXT DEFAULT 'offline',
    enrolled        INTEGER DEFAULT 0,
    agent_version   TEXT,
    agent_dist      TEXT DEFAULT 'none',
    winrm_port      INTEGER,
    dispatch_methods TEXT DEFAULT '[]',
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(hostname)
);

-- Network fabrics (VPN tunnels, overlays, LANs, cloud VPCs)
CREATE TABLE IF NOT EXISTS network_fabrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    fabric_type     TEXT NOT NULL,
    subnets         TEXT DEFAULT '[]',
    config          TEXT DEFAULT '{}',
    config_enc      TEXT DEFAULT '',
    priority        INTEGER DEFAULT 100,
    enabled         INTEGER DEFAULT 1,
    status          TEXT DEFAULT 'unknown',
    health_detail   TEXT DEFAULT '',
    probe_interval  INTEGER DEFAULT 300,
    last_health     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(name)
);

-- Node-to-fabric address mappings (a node can appear on multiple fabrics)
CREATE TABLE IF NOT EXISTS node_network_addresses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL REFERENCES fleet_nodes(id) ON DELETE CASCADE,
    fabric_id       INTEGER NOT NULL REFERENCES network_fabrics(id) ON DELETE CASCADE,
    address         TEXT NOT NULL,
    lane_status     TEXT DEFAULT 'unknown',
    last_seen       TEXT,
    UNIQUE(node_id, fabric_id, address)
);

-- Tool instances discovered on fleet nodes
CREATE TABLE IF NOT EXISTS tool_instances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL REFERENCES fleet_nodes(id) ON DELETE CASCADE,
    tool_name       TEXT NOT NULL,
    port            INTEGER NOT NULL,
    proto           TEXT DEFAULT 'https',
    version         TEXT DEFAULT '',
    status          TEXT DEFAULT 'online',
    scanning        INTEGER DEFAULT 0,
    last_seen       TEXT NOT NULL,
    UNIQUE(node_id, tool_name, port)
);

-- Encrypted credentials for SSH / WinRM / API keys
CREATE TABLE IF NOT EXISTS node_credentials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER REFERENCES fleet_nodes(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    cred_type       TEXT NOT NULL DEFAULT 'password',
    username        TEXT DEFAULT '',
    encrypted_value TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- SSH/SOCKS tunnels
CREATE TABLE IF NOT EXISTS tunnels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL REFERENCES fleet_nodes(id) ON DELETE CASCADE,
    fabric_id       INTEGER REFERENCES network_fabrics(id) ON DELETE SET NULL,
    tunnel_type     TEXT NOT NULL DEFAULT 'local',
    local_port      INTEGER NOT NULL,
    remote_host     TEXT NOT NULL,
    remote_port     INTEGER NOT NULL,
    pid             INTEGER,
    status          TEXT DEFAULT 'stopped',
    created_at      TEXT NOT NULL
);

-- Arsenal remote operations log
CREATE TABLE IF NOT EXISTS arsenal_operations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         INTEGER NOT NULL REFERENCES fleet_nodes(id) ON DELETE CASCADE,
    operation       TEXT NOT NULL,
    tool_name       TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    output          TEXT DEFAULT '',
    started_at      TEXT,
    finished_at     TEXT,
    created_at      TEXT NOT NULL
);

-- Append-only audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    username        TEXT NOT NULL DEFAULT 'system',
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    ip              TEXT DEFAULT ''
);

-- Users with RBAC roles
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL DEFAULT 'viewer',
    created_at      TEXT NOT NULL,
    last_login      TEXT
);

-- Key-value settings (DB-backed config)
CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fleet_status      ON fleet_nodes(status);
CREATE INDEX IF NOT EXISTS idx_tool_node         ON tool_instances(node_id);
CREATE INDEX IF NOT EXISTS idx_addr_node         ON node_network_addresses(node_id);
CREATE INDEX IF NOT EXISTS idx_addr_fabric       ON node_network_addresses(fabric_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts          ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_arsenal_node      ON arsenal_operations(node_id);
CREATE INDEX IF NOT EXISTS idx_tunnel_node       ON tunnels(node_id);
"""


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLES)


def _migrate(conn: sqlite3.Connection) -> None:
    for col, tbl, default in [
        ("vm_role", "fleet_nodes", None),
        ("config_enc", "network_fabrics", "''"),
    ]:
        try:
            defstr = f" DEFAULT {default}" if default is not None else ""
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} TEXT{defstr}")
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# Default settings seed
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "discovery_interval_s":     "300",
    "offline_threshold_cycles": "3",
    "ssh_discovery_interval_s": "7200",
    "scan_workers":             "32",
    "scan_timeout_s":           "0.4",
    "max_ssh_sessions":         "10",
    "ssh_idle_timeout_s":       "1800",
    "max_terminal_tabs":        "4",
    "tunnel_reaper_interval_s": "60",
    "github_version_interval_s":"1800",
    "checkin_api_key":          "",
    "broker_port":              "5401",
    "default_palette":          "resume",
    "default_mode":             "light",
}


def _seed_defaults(conn: sqlite3.Connection) -> None:
    now = _now()
    for key, val in _DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, val, now),
        )


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = get_conn().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, _now()),
    )
    conn.commit()


def get_all_settings() -> dict:
    rows = get_conn().execute("SELECT key, value FROM settings ORDER BY key").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(action: str, detail: str = "", username: str = "system", ip: str = "") -> None:
    conn = get_conn()
    conn.execute(
        "INSERT INTO audit_log (ts, username, action, detail, ip) VALUES (?, ?, ?, ?, ?)",
        (_now(), username, action, detail, ip),
    )
    conn.commit()


def get_audit_log(limit: int = 200, offset: int = 0) -> list[dict]:
    rows = get_conn().execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return dict_rows(rows)


# ---------------------------------------------------------------------------
# Fleet node helpers
# ---------------------------------------------------------------------------

def upsert_fleet_node(hostname: str, **kwargs) -> int:
    conn = get_conn()
    now = _now()
    existing = conn.execute(
        "SELECT id FROM fleet_nodes WHERE hostname = ?", (hostname,)
    ).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        if sets:
            vals = list(kwargs.values()) + [now, existing["id"]]
            conn.execute(
                f"UPDATE fleet_nodes SET {sets}, updated_at = ? WHERE id = ?", vals
            )
        conn.commit()
        return existing["id"]
    cols = ["hostname", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [hostname, now, now] + list(kwargs.values())
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    cur = conn.execute(
        f"INSERT INTO fleet_nodes ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def get_fleet_nodes(status: str | None = None) -> list[dict]:
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM fleet_nodes WHERE status = ? ORDER BY hostname", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM fleet_nodes ORDER BY hostname"
        ).fetchall()
    return dict_rows(rows)


def get_fleet_node(node_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM fleet_nodes WHERE id = ?", (node_id,)
    ).fetchone()
    return dict_row(row)


def update_fleet_node(node_id: int, **kwargs) -> bool:
    conn = get_conn()
    if not kwargs:
        return False
    existing = conn.execute(
        "SELECT id FROM fleet_nodes WHERE id = ?", (node_id,)
    ).fetchone()
    if not existing:
        return False
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [_now(), node_id]
    conn.execute(
        f"UPDATE fleet_nodes SET {sets}, updated_at = ? WHERE id = ?", vals
    )
    conn.commit()
    return True


def delete_fleet_node(node_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM fleet_nodes WHERE id = ?", (node_id,))
    conn.commit()
    return True


def search_fleet_nodes(search: str = "", status: str = "",
                       fabric_id: int = 0, role: str = "") -> list[dict]:
    conn = get_conn()
    sql = "SELECT DISTINCT fn.* FROM fleet_nodes fn"
    joins = []
    where = []
    params: list = []

    if fabric_id:
        joins.append(
            "JOIN node_network_addresses na ON na.node_id = fn.id"
        )
        where.append("na.fabric_id = ?")
        params.append(fabric_id)

    if search:
        where.append("(fn.hostname LIKE ? OR fn.display_name LIKE ? OR fn.ip_primary LIKE ?)")
        pat = f"%{search}%"
        params.extend([pat, pat, pat])

    if status:
        where.append("fn.status = ?")
        params.append(status)

    if role:
        where.append("fn.role = ?")
        params.append(role)

    if joins:
        sql += " " + " ".join(joins)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY fn.hostname"

    rows = conn.execute(sql, params).fetchall()
    return dict_rows(rows)


# ---------------------------------------------------------------------------
# Tool instance helpers
# ---------------------------------------------------------------------------

def upsert_tool_instance(node_id: int, tool_name: str, port: int, **kwargs) -> int:
    conn = get_conn()
    now = _now()
    existing = conn.execute(
        "SELECT id FROM tool_instances WHERE node_id = ? AND tool_name = ? AND port = ?",
        (node_id, tool_name, port),
    ).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        if sets:
            vals = list(kwargs.values()) + [now, existing["id"]]
            conn.execute(
                f"UPDATE tool_instances SET {sets}, last_seen = ? WHERE id = ?", vals
            )
        else:
            conn.execute(
                "UPDATE tool_instances SET last_seen = ? WHERE id = ?",
                (now, existing["id"]),
            )
        conn.commit()
        return existing["id"]
    cols = ["node_id", "tool_name", "port", "last_seen"] + list(kwargs.keys())
    vals = [node_id, tool_name, port, now] + list(kwargs.values())
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    cur = conn.execute(
        f"INSERT INTO tool_instances ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def get_tool_instances(node_id: int | None = None) -> list[dict]:
    conn = get_conn()
    if node_id is not None:
        rows = conn.execute(
            "SELECT * FROM tool_instances WHERE node_id = ? ORDER BY tool_name",
            (node_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tool_instances ORDER BY tool_name"
        ).fetchall()
    return dict_rows(rows)


def mark_stale_tools_offline(threshold_cycles: int, scan_interval_s: int) -> int:
    conn = get_conn()
    cutoff_seconds = threshold_cycles * scan_interval_s
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=cutoff_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE tool_instances SET status = 'offline' "
        "WHERE status = 'online' AND last_seen < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount


def mark_stale_nodes_offline() -> int:
    conn = get_conn()
    cur = conn.execute(
        "UPDATE fleet_nodes SET status = 'offline', updated_at = ? "
        "WHERE status = 'online' AND id NOT IN ("
        "  SELECT DISTINCT node_id FROM tool_instances WHERE status = 'online'"
        ")",
        (_now(),),
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Network fabric helpers
# ---------------------------------------------------------------------------

def create_fabric(name: str, fabric_type: str, **kwargs) -> int:
    conn = get_conn()
    now = _now()
    cols = ["name", "fabric_type", "created_at", "updated_at"] + list(kwargs.keys())
    vals = [name, fabric_type, now, now] + list(kwargs.values())
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    cur = conn.execute(
        f"INSERT INTO network_fabrics ({col_names}) VALUES ({placeholders})", vals
    )
    conn.commit()
    return cur.lastrowid


def get_fabrics(enabled_only: bool = False) -> list[dict]:
    conn = get_conn()
    if enabled_only:
        rows = conn.execute(
            "SELECT * FROM network_fabrics WHERE enabled = 1 ORDER BY priority, name"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM network_fabrics ORDER BY priority, name"
        ).fetchall()
    return dict_rows(rows)


def get_fabric(fabric_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM network_fabrics WHERE id = ?", (fabric_id,)
    ).fetchone()
    return dict_row(row)


def update_fabric(fabric_id: int, **kwargs) -> bool:
    conn = get_conn()
    if not kwargs:
        return False
    existing = conn.execute(
        "SELECT id FROM network_fabrics WHERE id = ?", (fabric_id,)
    ).fetchone()
    if not existing:
        return False
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [_now(), fabric_id]
    conn.execute(
        f"UPDATE network_fabrics SET {sets}, updated_at = ? WHERE id = ?", vals
    )
    conn.commit()
    return True


def delete_fabric(fabric_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM node_network_addresses WHERE fabric_id = ?", (fabric_id,))
    conn.execute("DELETE FROM network_fabrics WHERE id = ?", (fabric_id,))
    conn.commit()
    return True


def toggle_fabric(fabric_id: int, enabled: bool) -> bool:
    conn = get_conn()
    conn.execute(
        "UPDATE network_fabrics SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, _now(), fabric_id),
    )
    conn.commit()
    return True


def get_fabric_node_count(fabric_id: int) -> int:
    row = get_conn().execute(
        "SELECT COUNT(DISTINCT node_id) AS c FROM node_network_addresses WHERE fabric_id = ?",
        (fabric_id,),
    ).fetchone()
    return row["c"] if row else 0


def delete_node_addresses_for_fabric(fabric_id: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM node_network_addresses WHERE fabric_id = ?", (fabric_id,)
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Node network address helpers
# ---------------------------------------------------------------------------

def upsert_node_address(node_id: int, fabric_id: int, address: str,
                        lane_status: str = "up") -> int:
    conn = get_conn()
    now = _now()
    existing = conn.execute(
        "SELECT id FROM node_network_addresses "
        "WHERE node_id = ? AND fabric_id = ? AND address = ?",
        (node_id, fabric_id, address),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE node_network_addresses SET lane_status = ?, last_seen = ? WHERE id = ?",
            (lane_status, now, existing["id"]),
        )
        conn.commit()
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO node_network_addresses (node_id, fabric_id, address, lane_status, last_seen) "
        "VALUES (?, ?, ?, ?, ?)",
        (node_id, fabric_id, address, lane_status, now),
    )
    conn.commit()
    return cur.lastrowid


def get_node_addresses(node_id: int) -> list[dict]:
    rows = get_conn().execute(
        "SELECT na.*, nf.name AS fabric_name, nf.fabric_type "
        "FROM node_network_addresses na "
        "JOIN network_fabrics nf ON na.fabric_id = nf.id "
        "WHERE na.node_id = ? ORDER BY nf.priority",
        (node_id,),
    ).fetchall()
    return dict_rows(rows)


def update_node_address_lane(node_id: int, fabric_id: int, lane_status: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE node_network_addresses SET lane_status = ?, last_seen = ? "
        "WHERE node_id = ? AND fabric_id = ?",
        (lane_status, _now(), node_id, fabric_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Credential helpers (encrypted values handled by credential_store)
# ---------------------------------------------------------------------------

def store_credential(node_id: int | None, label: str, cred_type: str,
                     username: str, encrypted_value: str) -> int:
    conn = get_conn()
    now = _now()
    cur = conn.execute(
        "INSERT INTO node_credentials "
        "(node_id, label, cred_type, username, encrypted_value, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (node_id, label, cred_type, username, encrypted_value, now, now),
    )
    conn.commit()
    return cur.lastrowid


def get_credentials(node_id: int | None = None) -> list[dict]:
    conn = get_conn()
    if node_id is not None:
        rows = conn.execute(
            "SELECT id, node_id, label, cred_type, username, created_at, updated_at "
            "FROM node_credentials WHERE node_id = ? ORDER BY label",
            (node_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, node_id, label, cred_type, username, created_at, updated_at "
            "FROM node_credentials ORDER BY label"
        ).fetchall()
    return dict_rows(rows)


def get_credential_raw(cred_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT * FROM node_credentials WHERE id = ?", (cred_id,)
    ).fetchone()
    return dict_row(row)


def delete_credential(cred_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM node_credentials WHERE id = ?", (cred_id,))
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Tunnel helpers
# ---------------------------------------------------------------------------

def create_tunnel(node_id: int, fabric_id: int | None, tunnel_type: str,
                  local_port: int, remote_host: str, remote_port: int,
                  pid: int | None = None) -> int:
    conn = get_conn()
    now = _now()
    cur = conn.execute(
        "INSERT INTO tunnels "
        "(node_id, fabric_id, tunnel_type, local_port, remote_host, remote_port, pid, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (node_id, fabric_id, tunnel_type, local_port, remote_host, remote_port, pid, now),
    )
    conn.commit()
    return cur.lastrowid


def get_tunnels(node_id: int | None = None) -> list[dict]:
    conn = get_conn()
    if node_id is not None:
        rows = conn.execute(
            "SELECT t.*, fn.hostname AS node_hostname, nf.name AS fabric_name "
            "FROM tunnels t "
            "LEFT JOIN fleet_nodes fn ON t.node_id = fn.id "
            "LEFT JOIN network_fabrics nf ON t.fabric_id = nf.id "
            "WHERE t.node_id = ? ORDER BY t.created_at DESC",
            (node_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT t.*, fn.hostname AS node_hostname, nf.name AS fabric_name "
            "FROM tunnels t "
            "LEFT JOIN fleet_nodes fn ON t.node_id = fn.id "
            "LEFT JOIN network_fabrics nf ON t.fabric_id = nf.id "
            "ORDER BY t.created_at DESC"
        ).fetchall()
    return dict_rows(rows)


def get_tunnel(tunnel_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT t.*, fn.hostname AS node_hostname, nf.name AS fabric_name "
        "FROM tunnels t "
        "LEFT JOIN fleet_nodes fn ON t.node_id = fn.id "
        "LEFT JOIN network_fabrics nf ON t.fabric_id = nf.id "
        "WHERE t.id = ?",
        (tunnel_id,),
    ).fetchone()
    return dict_row(row)


def update_tunnel_status(tunnel_id: int, status: str, pid: int | None = None) -> bool:
    conn = get_conn()
    if pid is not None:
        conn.execute(
            "UPDATE tunnels SET status = ?, pid = ? WHERE id = ?",
            (status, pid, tunnel_id),
        )
    else:
        conn.execute(
            "UPDATE tunnels SET status = ? WHERE id = ?",
            (status, tunnel_id),
        )
    conn.commit()
    return True


def delete_tunnel(tunnel_id: int) -> bool:
    conn = get_conn()
    conn.execute("DELETE FROM tunnels WHERE id = ?", (tunnel_id,))
    conn.commit()
    return True


def delete_stale_tunnels() -> int:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM tunnels WHERE status = 'stopped'"
    )
    conn.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Arsenal operation helpers
# ---------------------------------------------------------------------------

def create_arsenal_op(node_id: int, operation: str, tool_name: str = "") -> int:
    conn = get_conn()
    now = _now()
    cur = conn.execute(
        "INSERT INTO arsenal_operations (node_id, operation, tool_name, status, created_at) "
        "VALUES (?, ?, ?, 'pending', ?)",
        (node_id, operation, tool_name, now),
    )
    conn.commit()
    return cur.lastrowid


def get_arsenal_ops(node_id: int | None = None, limit: int = 50) -> list[dict]:
    conn = get_conn()
    if node_id is not None:
        rows = conn.execute(
            "SELECT ao.*, fn.hostname AS node_hostname "
            "FROM arsenal_operations ao "
            "LEFT JOIN fleet_nodes fn ON ao.node_id = fn.id "
            "WHERE ao.node_id = ? ORDER BY ao.id DESC LIMIT ?",
            (node_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ao.*, fn.hostname AS node_hostname "
            "FROM arsenal_operations ao "
            "LEFT JOIN fleet_nodes fn ON ao.node_id = fn.id "
            "ORDER BY ao.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return dict_rows(rows)


def get_arsenal_op(op_id: int) -> dict | None:
    row = get_conn().execute(
        "SELECT ao.*, fn.hostname AS node_hostname "
        "FROM arsenal_operations ao "
        "LEFT JOIN fleet_nodes fn ON ao.node_id = fn.id "
        "WHERE ao.id = ?",
        (op_id,),
    ).fetchone()
    return dict_row(row)


def update_arsenal_op(op_id: int, **kwargs) -> bool:
    conn = get_conn()
    if not kwargs:
        return False
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [op_id]
    conn.execute(f"UPDATE arsenal_operations SET {sets} WHERE id = ?", vals)
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def fleet_stats() -> dict:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS c FROM fleet_nodes").fetchone()["c"]
    online = conn.execute(
        "SELECT COUNT(*) AS c FROM fleet_nodes WHERE status = 'online'"
    ).fetchone()["c"]
    scanning = conn.execute(
        "SELECT COUNT(*) AS c FROM tool_instances WHERE scanning = 1"
    ).fetchone()["c"]
    enrolled = conn.execute(
        "SELECT COUNT(*) AS c FROM fleet_nodes WHERE enrolled = 1"
    ).fetchone()["c"]
    fabrics = conn.execute(
        "SELECT COUNT(*) AS c FROM network_fabrics WHERE enabled = 1"
    ).fetchone()["c"]
    tools = conn.execute("SELECT COUNT(*) AS c FROM tool_instances").fetchone()["c"]
    tunnels = conn.execute(
        "SELECT COUNT(*) AS c FROM tunnels WHERE status = 'active'"
    ).fetchone()["c"]
    return {
        "total": total,
        "online": online,
        "offline": total - online,
        "scanning": scanning,
        "enrolled": enrolled,
        "fabrics": fabrics,
        "tools": tools,
        "tunnels": tunnels,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
