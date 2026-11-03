"""Nexus v2.5 — Retention reaper.

Background thread that enforces data retention policies:
  - remote_sessions: 1 year (365 days)
  - service_ports:   3 years (1095 days)
  - audit_log:       3 months (90 days)
  - node_history:    3 years (1095 days)
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

from models import db as mdb

log = logging.getLogger("nexus.retention_reaper")


class RetentionReaper:
    def __init__(self, socketio=None):
        self._sio = socketio
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._interval = 86400  # default: once per day

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        try:
            self._interval = int(mdb.get_setting("retention_reaper_interval_s", "86400"))
        except (ValueError, RuntimeError):
            pass
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="retention-reaper",
        )
        self._thread.start()
        log.info("Retention reaper started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("Retention reaper stopped")

    def _loop(self) -> None:
        time.sleep(30)
        self._reap()
        while not self._stop.wait(self._interval):
            self._reap()

    def _reap(self) -> None:
        try:
            session_days = int(mdb.get_setting("retention_sessions_days", "365"))
        except (ValueError, RuntimeError):
            session_days = 365
        try:
            port_days = int(mdb.get_setting("retention_ports_days", "1095"))
        except (ValueError, RuntimeError):
            port_days = 1095
        try:
            audit_days = int(mdb.get_setting("retention_audit_days", "90"))
        except (ValueError, RuntimeError):
            audit_days = 90
        try:
            history_days = int(mdb.get_setting("retention_node_history_days", "1095"))
        except (ValueError, RuntimeError):
            history_days = 1095

        now = datetime.now(timezone.utc)
        conn = mdb.get_conn()

        total = 0
        for table, ts_col, days, label in [
            ("remote_sessions", "timestamp_start", session_days, "sessions"),
            ("service_ports", "first_seen", port_days, "ports"),
            ("audit_log", "ts", audit_days, "audit_log"),
            ("node_history", "timestamp", history_days, "node_history"),
        ]:
            cutoff = (now - timedelta(days=days)).isoformat()
            cur = conn.execute(
                f"DELETE FROM {table} WHERE {ts_col} < ?", (cutoff,)
            )
            count = cur.rowcount
            total += count
            if count > 0:
                log.info("Reaped %d %s entries older than %d days", count, label, days)

        if total > 0:
            conn.commit()
            mdb.audit(
                "retention_reap",
                f"Purged {total} expired records",
                severity="info",
                event_type="maintenance",
            )
        else:
            log.debug("Retention reap: nothing to purge")

    def reap_now(self) -> dict:
        self._reap()
        return {"ok": True}
