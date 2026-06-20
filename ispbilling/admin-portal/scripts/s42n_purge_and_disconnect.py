#!/usr/bin/env python3
"""S42N — One-shot: purge all session history + disconnect every active
PPPoE session across every Active NAS for every tenant.

Effect:
  1. Backs up `radacct.db` to `/var/lib/freeradius/radacct.db.purge-<ts>.bak`.
  2. DELETE FROM radacct (entire history).
  3. UPDATE online_users SET status='Offline', bytes_in=0, bytes_out=0,
     uptime_seconds=0 (clears cache).
  4. For every Active NAS in `nas_devices`, opens a RouterOS API
     connection and removes every `/ppp/active` entry.
  5. Sends CoA-Disconnect to FreeRADIUS for every Active customer
     for safety (RADIUS state convergence).

After this, customers' CPEs will dial in fresh PPPoE sessions within a
few seconds. FreeRADIUS writes new radacct rows; online_users repopulates
from the BG sync.

Idempotent and safe to re-run.
"""
import os
import sys
import time
import shutil
import sqlite3

sys.path.insert(0, "/opt/ispbilling/admin-portal")
os.chdir("/opt/ispbilling/admin-portal")

from database import SessionLocal  # noqa: E402
from radius_network import NasDevice  # noqa: E402
import routeros_provision as rp  # noqa: E402

RADACCT = os.getenv("RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")


def step_backup() -> str:
    ts = int(time.time())
    backup = f"{RADACCT}.purge-{ts}.bak"
    shutil.copy2(RADACCT, backup)
    print(f"[step 1/5] Backup written: {backup}")
    return backup


def step_purge_radacct() -> dict:
    con = sqlite3.connect(RADACCT, timeout=10)
    cur = con.cursor()
    before = cur.execute("SELECT COUNT(*) FROM radacct").fetchone()[0]
    cur.execute("DELETE FROM radacct")
    con.commit()
    after = cur.execute("SELECT COUNT(*) FROM radacct").fetchone()[0]
    cur.execute("VACUUM")
    con.close()
    print(f"[step 2/5] radacct purged: {before} → {after}")
    return {"before": before, "after": after}


def step_purge_online_users(db) -> int:
    res = db.execute(__import__("sqlalchemy").text(
        "UPDATE online_users "
        "   SET status='Offline', bytes_in=0, bytes_out=0, "
        "       uptime_seconds=0 "
        " WHERE status IN ('Online','WalledGarden')"))
    db.commit()
    n = res.rowcount or 0
    print(f"[step 3/5] online_users marked Offline: {n} rows")
    return n


def step_disconnect_pppoe(db) -> dict:
    nas_rows = db.query(NasDevice).filter(
        NasDevice.status == "Active").all()
    print(f"[step 4/5] Disconnecting on {len(nas_rows)} active NAS")
    per_nas = {}
    total = 0
    for nas in nas_rows:
        ip = nas.ip_address or "(no-ip)"
        nm = nas.name or ip
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                actives = rc._api.path("ppp/active")
                rows = list(actives)
                removed = 0
                for a in rows:
                    try:
                        actives.remove(a[".id"])
                        removed += 1
                    except TypeError:
                        actives("remove", **{".id": a[".id"]})
                        removed += 1
                    except Exception as _ie:
                        # Per-session failure shouldn't abort the whole NAS.
                        print(f"    [{nm}] remove err: {_ie}")
                per_nas[nm] = removed
                total += removed
                print(f"    [{nm} / {ip}] removed: {removed}")
        except Exception as e:
            per_nas[nm] = f"ERROR: {e}"
            print(f"    [{nm} / {ip}] connect/loop failed: {e}")
    return {"total": total, "per_nas": per_nas}


def step_coa_disconnect(db) -> int:
    """Send CoA-Disconnect for every customer with a non-empty username,
    on every active NAS — so the RADIUS session table matches reality."""
    try:
        import freeradius_manager as fm
    except Exception as e:
        print(f"[step 5/5] freeradius_manager import failed: {e}")
        return 0
    from database import Customer
    nas_rows = db.query(NasDevice).filter(
        NasDevice.status == "Active").all()
    cust_rows = db.query(Customer).filter(
        Customer.username.isnot(None), Customer.username != "").all()
    sent = 0
    for cust in cust_rows:
        for nas in nas_rows:
            try:
                fm.send_coa_disconnect(
                    nas_ip=nas.ip_address,
                    username=cust.username,
                    secret=(nas.secret or "testing123"))
                sent += 1
            except Exception:
                # CoA failures are non-fatal — PPP /active/remove was the main action.
                pass
    print(f"[step 5/5] CoA-Disconnect packets sent: {sent}")
    return sent


def main():
    if not os.path.exists(RADACCT):
        print(f"FATAL: {RADACCT} not found", file=sys.stderr)
        return 1
    started = time.time()
    backup = step_backup()
    purge = step_purge_radacct()
    db = SessionLocal()
    try:
        ou = step_purge_online_users(db)
        disc = step_disconnect_pppoe(db)
        coa = step_coa_disconnect(db)
    finally:
        db.close()
    print()
    print("=" * 60)
    print(f"DONE in {time.time() - started:.1f}s")
    print(f"  backup            : {backup}")
    print(f"  radacct purged    : {purge['before']} rows → 0")
    print(f"  online_users reset: {ou} rows")
    print(f"  PPPoE disconnects : {disc['total']} sessions across "
          f"{len(disc['per_nas'])} NAS")
    print(f"  CoA-Disconnects   : {coa}")
    print("=" * 60)
    print("Customers will reconnect over the next 30-120 seconds.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
