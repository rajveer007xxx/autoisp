#!/usr/bin/env python3
"""s55u — FreeRADIUS radreply sync worker for per-user VLAN authentication.

Writes the RFC-2868 tunnel attributes into `/var/lib/freeradius/radacct.db`
for every customer with `vlan_enabled=1` and a non-NULL `vlan_id`.
Removes them for customers where the toggle is now off (or the VLAN was
released back to the pool). Idempotent and safe to run on a 30 s timer.

Per RFC, three reply attributes are needed:
    Tunnel-Type           := VLAN          (#13)
    Tunnel-Medium-Type    := IEEE-802      (#6)
    Tunnel-Private-Group-Id := <vlan_id>
MikroTik PPPoE Server with use-radius=yes will reject the session if the
incoming Q-tag does not match Tunnel-Private-Group-Id.

This worker NEVER touches existing non-tunnel attributes for any user, so
it cannot break PPPoE / Hotspot / Static-IP for users with vlan_enabled=0.
"""
from __future__ import annotations
import sys as _sys; _sys.path.insert(0, '/opt/ispbilling/admin-portal'); from db_compat import get_raw_conn as _compat_conn  # __s56Z2_compat__
import os
import sqlite3
import time
import sys
import logging
from datetime import datetime

ADMIN_DB = "/var/lib/autoispbilling/autoispbilling.db"
RAD_DB   = "/var/lib/freeradius/radacct.db"
TUNNEL_ATTRS = ("Tunnel-Type", "Tunnel-Medium-Type", "Tunnel-Private-Group-Id")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vlan-radius-sync] %(message)s",
)
log = logging.getLogger(__name__)

# __PHASE_RADACCT_PG__  FreeRADIUS now writes its tables (radacct, radpostauth,
# radcheck, radreply, ...) into the central PostgreSQL `autoispbilling` DB.
# We expose a tiny adapter so the legacy `sqlite3.connect(RADACCT)` calls
# below keep working without touching any of their SQL.
def _radacct_pg_connect(*_a, **_kw):
    import sys as _sys_rd
    if "/opt/ispbilling" not in _sys_rd.path:
        _sys_rd.path.insert(0, "/opt/ispbilling")
    import db_compat as _db_compat_rd
    return _db_compat_rd.get_raw_conn(timeout=10.0)



def open_admin() -> sqlite3.Connection:
    """Open admin DB read-only via URI (WAL safe)."""
    return _compat_conn(timeout=10)


def open_rad() -> sqlite3.Connection:
    c = _radacct_pg_connect()
    c.execute("PRAGMA journal_mode=WAL")
    return c


def sync_once() -> dict:
    """One pass. Returns counters for logging."""
    if not os.path.exists(ADMIN_DB):
        log.error("admin DB missing: %s", ADMIN_DB)
        return {"error": "admin-db-missing"}
    if not os.path.exists(RAD_DB):
        log.error("radius DB missing: %s", RAD_DB)
        return {"error": "rad-db-missing"}

    added = updated = removed = 0

    # Step 1 — fetch desired state from admin DB.
    # We only act on PPPoE customers (auth_type='pppoe'), since the BSNL gate
    # is meaningful only for PPPoE dial-in. Hotspot/Static-IP are skipped.
    desired: dict[str, int] = {}  # username -> vlan_id
    with open_admin() as a:
        cur = a.cursor()
        rows = cur.execute(
            "SELECT username, vlan_id FROM customers "
            "WHERE vlan_enabled = 1 "
            "AND vlan_id IS NOT NULL "
            "AND IFNULL(auth_type, 'pppoe') = 'pppoe' "
            "AND username IS NOT NULL AND username != ''"
        ).fetchall()
        for username, vlan_id in rows:
            if not username:
                continue
            desired[str(username).strip()] = int(vlan_id)

    if not desired:
        # Still need a tear-down pass for any stale tunnel rows.
        log.debug("no vlan-enabled customers; will sweep stale rows")

    # Step 2 — fetch current managed rows from radreply.
    with open_rad() as r:
        cur = r.cursor()
        existing_rows = cur.execute(
            "SELECT username, attribute, value FROM radreply "
            "WHERE attribute IN (?, ?, ?)",
            TUNNEL_ATTRS,
        ).fetchall()

        # Build current map: username -> {attribute: value}
        current: dict[str, dict[str, str]] = {}
        for username, attribute, value in existing_rows:
            current.setdefault(username, {})[attribute] = value

        # Step 3 — for each desired user, ensure the 3 attrs are correct.
        for username, vlan_id in desired.items():
            expected = {
                "Tunnel-Type":             "VLAN",
                "Tunnel-Medium-Type":      "IEEE-802",
                "Tunnel-Private-Group-Id": str(vlan_id),
            }
            cur_attrs = current.get(username, {})
            # Decide whether to act.
            need_rewrite = False
            for k, v in expected.items():
                if cur_attrs.get(k) != v:
                    need_rewrite = True
                    break
            if need_rewrite:
                # Atomically replace: delete then insert (single tx).
                cur.execute(
                    "DELETE FROM radreply WHERE username = ? AND attribute IN (?, ?, ?)",
                    (username, *TUNNEL_ATTRS),
                )
                for attr, val in expected.items():
                    cur.execute(
                        "INSERT INTO radreply (username, attribute, op, value) "
                        "VALUES (?, ?, ':=', ?)",
                        (username, attr, val),
                    )
                if cur_attrs:
                    updated += 1
                else:
                    added += 1

        # Step 4 — remove tunnel rows for users no longer in `desired`.
        stale_users = set(current.keys()) - set(desired.keys())
        for username in stale_users:
            cur.execute(
                "DELETE FROM radreply WHERE username = ? AND attribute IN (?, ?, ?)",
                (username, *TUNNEL_ATTRS),
            )
            removed += 1

        r.commit()

    log.info(
        "sync ok: %d added, %d updated, %d removed (desired=%d)",
        added, updated, removed, len(desired)
    )
    return {"added": added, "updated": updated, "removed": removed,
            "desired": len(desired)}


def main_loop(interval: int = 30):
    log.info("starting vlan-radius-sync (interval=%ds)", interval)
    while True:
        try:
            sync_once()
        except sqlite3.OperationalError as e:
            log.warning("sqlite op-error (retrying): %s", e)
        except Exception:
            log.exception("unhandled error")
        time.sleep(interval)


if __name__ == "__main__":
    if "--once" in sys.argv:
        r = sync_once()
        print(r)
        sys.exit(0 if "error" not in r else 1)
    interval = 30
    for a in sys.argv[1:]:
        if a.startswith("--interval="):
            try:
                interval = max(5, int(a.split("=", 1)[1]))
            except ValueError:
                pass
    main_loop(interval)
