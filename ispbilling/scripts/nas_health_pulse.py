#!/usr/bin/env python3
"""
ISP Billing — NAS Health Pulse  [PHASE-3 migrated to db_compat / PostgreSQL]
============================================================================
Lightweight 60-second TCP-port reachability pulse for every row in
`nas_devices`. Writes the result to `last_status` + `last_status_msg`.

PHASE-3 NOTE (2026-06-21): previously this script wrote *only* to the legacy
SQLite file via raw sqlite3.connect(), which silently bypassed the PG
DATABASE_URL the rest of the app uses. Now routes through db_compat so
all writes land in PostgreSQL (with PRAGMA / placeholder translation).
"""
from __future__ import annotations
import os
import sys
import socket
import time
from datetime import datetime, timezone

# Ensure /opt/ispbilling is importable so we can use db_compat.
if "/opt/ispbilling" not in sys.path:
    sys.path.insert(0, "/opt/ispbilling")

import db_compat  # noqa: E402

TIMEOUT = float(os.environ.get("NAS_PULSE_TIMEOUT", "3.0"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _tcp_check(host: str, port: int, timeout: float = TIMEOUT):
    """Return (ok, elapsed_ms, err_msg)."""
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as _:
            return True, int((time.monotonic() - t0) * 1000), ""
    except (socket.timeout, TimeoutError):
        return False, int((time.monotonic() - t0) * 1000), "timeout"
    except OSError as e:
        return False, int((time.monotonic() - t0) * 1000), str(e)


def main():
    c = db_compat.get_raw_conn(timeout=10.0)
    cur = c.cursor()
    try:
        cur.execute(
            "SELECT id, company_id, name, ip_address, "
            "       COALESCE(port, 8728) AS port, "
            "       COALESCE(use_tls, 0) AS use_tls, "
            "       last_status "
            "  FROM nas_devices "
            " WHERE COALESCE(status, 'Active') NOT IN ('Disabled','disabled','Archived')"
        )
        rows = cur.fetchall()
    except Exception as e:
        print(f"[nas-pulse] table read failed: {e}", file=sys.stderr)
        return 0

    if not rows:
        print("[nas-pulse] no NAS devices to check")
        return 0

    # db_compat returns plain tuples on PG; use column-index access (matches
    # the SELECT order above). We keep column names below in comments.
    # 0=id  1=company_id  2=name  3=ip_address  4=port  5=use_tls  6=last_status
    up_n = down_n = changed_n = 0
    ts = _now_iso()
    for r in rows:
        ip = (r[3] or "").strip()
        if not ip:
            continue
        port = int(r[4] or 8728)
        if port == 0:
            port = 8729 if int(r[5] or 0) else 8728

        ok, ms, err = _tcp_check(ip, port)
        new_status = "up" if ok else "down"
        msg = (f"tcp:{port} OK in {ms}ms" if ok
               else f"tcp:{port} FAIL ({err}) after {ms}ms")
        prev = (r[6] or "").lower()
        if prev != new_status:
            changed_n += 1
            print(f"[nas-pulse] NAS#{r[0]} {r[2]} ({ip}:{port}) "
                  f"{prev or 'unknown'} → {new_status}  | {msg}")
        try:
            cur.execute(
                "UPDATE nas_devices "
                "   SET last_status=?, last_status_msg=?, updated_at=? "
                " WHERE id=?",
                (new_status, msg, ts, r[0]),
            )
        except Exception as e:
            print(f"[nas-pulse] update failed for NAS#{r[0]}: {e}",
                  file=sys.stderr)
        if ok:
            up_n += 1
        else:
            down_n += 1
    try:
        c.commit()
    except Exception:
        pass
    try:
        c.close()
    except Exception:
        pass
    print(f"[nas-pulse] done  total={up_n + down_n}  up={up_n}  down={down_n}  "
          f"changed={changed_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
