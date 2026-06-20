"""
_S40zκ_  Lightweight login-tracking module
─────────────────────────────────────────────────────────────────────
Provides two small SQLite tables shared by every portal:

  • login_events       — one row per successful login (admin/sub_lco/employee)
  • session_activity   — one row per actor with last_seen_at heartbeat

The superadmin dashboard reads these to display live + today + 30-day
unique-visitor counts per role.

All writes are best-effort and silenced on error so they can never
break a request.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from database import engine

log = logging.getLogger(__name__)

# Roles tracked in the dashboard widgets
TRACKED_ROLES = ("admin", "sub_lco", "employee")
# A user is considered "live" if their last heartbeat is within this window.
LIVE_WINDOW_SECONDS = 5 * 60       # 5 minutes
# Throttle heartbeat writes per actor so we don't hammer SQLite on every req.
HEARTBEAT_THROTTLE_SEC = 60

_LAST_HB_CACHE: dict = {}


def _ensure_schema() -> None:
    """Create the two tables idempotently. Safe to call repeatedly."""
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS login_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_type  TEXT NOT NULL,
                    actor_id    TEXT NOT NULL,
                    company_id  TEXT,
                    actor_name  TEXT,
                    ip_address  TEXT,
                    user_agent  TEXT,
                    login_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lev_login_at "
                "ON login_events(login_at)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_lev_role_at "
                "ON login_events(actor_type, login_at)")
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS session_activity (
                    actor_type    TEXT NOT NULL,
                    actor_id      TEXT NOT NULL,
                    company_id    TEXT,
                    last_seen_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (actor_type, actor_id)
                )
            """)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_sact_seen "
                "ON session_activity(last_seen_at)")
    except Exception as e:
        log.warning("login-tracking schema init failed: %s", e)


_ensure_schema()


def record_login(actor_type: str, actor_id: str,
                 company_id: Optional[str] = None,
                 actor_name: Optional[str] = None,
                 ip_address: Optional[str] = None,
                 user_agent: Optional[str] = None) -> None:
    """Insert one row into login_events and bump session_activity."""
    if not actor_type or not actor_id:
        return
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO login_events "
                "  (actor_type, actor_id, company_id, actor_name, "
                "   ip_address, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (actor_type, str(actor_id), company_id, actor_name,
                 ip_address, (user_agent or "")[:300]),
            )
            conn.exec_driver_sql(
                "INSERT INTO session_activity "
                "  (actor_type, actor_id, company_id, last_seen_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(actor_type, actor_id) DO UPDATE SET "
                "  last_seen_at = CURRENT_TIMESTAMP, "
                "  company_id   = excluded.company_id",
                (actor_type, str(actor_id), company_id),
            )
    except Exception as e:
        log.debug("record_login failed: %s", e)


def heartbeat(actor_type: str, actor_id: str,
              company_id: Optional[str] = None) -> None:
    """Throttled UPSERT into session_activity. Called on each request."""
    if not actor_type or not actor_id:
        return
    if actor_type not in TRACKED_ROLES:
        return
    key = (actor_type, str(actor_id))
    now = datetime.now(timezone.utc).timestamp()
    last = _LAST_HB_CACHE.get(key, 0.0)
    if now - last < HEARTBEAT_THROTTLE_SEC:
        return
    _LAST_HB_CACHE[key] = now
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO session_activity "
                "  (actor_type, actor_id, company_id, last_seen_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(actor_type, actor_id) DO UPDATE SET "
                "  last_seen_at = CURRENT_TIMESTAMP",
                (actor_type, str(actor_id), company_id),
            )
    except Exception as e:
        log.debug("heartbeat failed: %s", e)


def get_dashboard_stats() -> dict:
    """
    Return a dict shaped like:
      {
        "live":  {"admin": N, "sub_lco": N, "employee": N, "total": N},
        "today": {"admin": N, "sub_lco": N, "employee": N, "total": N},
        "month": {"admin": N, "sub_lco": N, "employee": N, "total": N},
      }
    "today" / "month" are unique actor counts via login_events.
    """
    out = {bucket: {role: 0 for role in TRACKED_ROLES + ("total",)}
           for bucket in ("live", "today", "month")}
    try:
        with engine.begin() as conn:
            # Live: last 5 minutes
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(seconds=LIVE_WINDOW_SECONDS))
            rows = conn.exec_driver_sql(
                "SELECT actor_type, COUNT(*) FROM session_activity "
                "WHERE last_seen_at >= ? "
                "GROUP BY actor_type",
                (cutoff.strftime("%Y-%m-%d %H:%M:%S"),)
            ).fetchall()
            for r in rows:
                if r[0] in TRACKED_ROLES:
                    out["live"][r[0]] = int(r[1])
                    out["live"]["total"] += int(r[1])

            # Today: distinct actors logged in since 00:00 (system tz = IST)
            rows = conn.exec_driver_sql(
                "SELECT actor_type, COUNT(DISTINCT actor_id) "
                "FROM login_events "
                "WHERE date(login_at) = date('now') "
                "GROUP BY actor_type"
            ).fetchall()
            for r in rows:
                if r[0] in TRACKED_ROLES:
                    out["today"][r[0]] = int(r[1])
                    out["today"]["total"] += int(r[1])

            # Last 30 days: distinct actors
            rows = conn.exec_driver_sql(
                "SELECT actor_type, COUNT(DISTINCT actor_id) "
                "FROM login_events "
                "WHERE login_at >= datetime('now', '-30 days') "
                "GROUP BY actor_type"
            ).fetchall()
            for r in rows:
                if r[0] in TRACKED_ROLES:
                    out["month"][r[0]] = int(r[1])
                    out["month"]["total"] += int(r[1])
    except Exception as e:
        log.warning("get_dashboard_stats failed: %s", e)
    return out
