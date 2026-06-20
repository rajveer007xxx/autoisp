#!/usr/bin/env python3
"""
ISP Billing — NAS Health Pulse
==============================
Lightweight 60-second TCP-port reachability pulse for every row in
`nas_devices`. Writes the result to `last_status` + `last_status_msg`
so downstream code (e.g. the mobile API's real-time online_now filter)
can exclude sessions whose NAS is currently down.

Why TCP-only (and not ICMP / RouterOS API)?
  • Many ISPs block ICMP at the edge, so ping is unreliable.
  • Opening a real RouterOS API session every 60 s for every NAS is
    expensive — the existing 5-min `isp-nas-healthcheck` already does
    that for deep auto-heal.
  • A 3-second TCP-connect on the API port is cheap and accurate enough
    to know "this box is responding to TCP".

Schema fields used:
  • nas_devices.id / company_id / name / ip_address
  • nas_devices.port             (default 8728 RouterOS-API)
  • nas_devices.use_tls          (if 1, port defaults to 8729)
  • nas_devices.last_status      ('up' | 'down' | 'unknown')
  • nas_devices.last_status_msg  ('tcp:8728 OK in 42ms' | error text)

Exit code is always 0 (best-effort daemon-style script).
"""
from __future__ import annotations
import os
import sys
import socket
import sqlite3
import time
from datetime import datetime, timezone

DB_PATH = os.environ.get("ISP_DB_PATH", "/var/lib/autoispbilling/autoispbilling.db")
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
    if not os.path.exists(DB_PATH):
        print(f"[nas-pulse] DB not found at {DB_PATH}; skipping run", file=sys.stderr)
        return 0
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "SELECT id, company_id, name, ip_address, "
            "       COALESCE(port, 8728) AS port, "
            "       COALESCE(use_tls, 0) AS use_tls, "
            "       last_status "
            "  FROM nas_devices "
            " WHERE COALESCE(status, 'Active') NOT IN ('Disabled','disabled','Archived')"
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"[nas-pulse] table read failed: {e}", file=sys.stderr)
        return 0

    if not rows:
        print("[nas-pulse] no NAS devices to check")
        return 0

    up_n = down_n = changed_n = 0
    ts = _now_iso()
    for r in rows:
        ip = (r["ip_address"] or "").strip()
        if not ip:
            continue
        # If TLS, RouterOS API-SSL = 8729 by default.
        port = int(r["port"] or 8728)
        if port == 0:
            port = 8729 if int(r["use_tls"] or 0) else 8728

        ok, ms, err = _tcp_check(ip, port)
        new_status = "up" if ok else "down"
        msg = (f"tcp:{port} OK in {ms}ms" if ok
               else f"tcp:{port} FAIL ({err}) after {ms}ms")
        prev = (r["last_status"] or "").lower()
        if prev != new_status:
            changed_n += 1
            print(f"[nas-pulse] NAS#{r['id']} {r['name']} ({ip}:{port}) "
                  f"{prev or 'unknown'} → {new_status}  | {msg}")
        try:
            c.execute(
                "UPDATE nas_devices "
                "   SET last_status=?, last_status_msg=?, updated_at=? "
                " WHERE id=?",
                (new_status, msg, ts, r["id"]),
            )
        except sqlite3.OperationalError as e:
            print(f"[nas-pulse] update failed for NAS#{r['id']}: {e}",
                  file=sys.stderr)
        if ok:
            up_n += 1
        else:
            down_n += 1
    c.commit()
    c.close()
    print(f"[nas-pulse] done  total={up_n + down_n}  up={up_n}  down={down_n}  "
          f"changed={changed_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
