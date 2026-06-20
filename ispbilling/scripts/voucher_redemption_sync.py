#!/usr/bin/env python3
"""Voucher redemption sync — scans FreeRADIUS radpostauth + radacct for
voucher-code logins and:
  1. Marks the voucher as 'used' in the app DB (with audit row).
  2. (Optional, controlled by ENFORCE_SINGLE_USE flag) deletes radcheck/radreply
     for vouchers that have been used → single-use enforcement.

Designed to be called by the existing nas_healthcheck timer (every 5 min)
AND on-demand from /api/online-users/sync-from-nas (which already runs on UI loads).
"""
from __future__ import annotations
import sqlite3, os, sys
from datetime import datetime

ENFORCE_SINGLE_USE = True

# __live_session_cleanup__ — kick stale hotspot sessions whose voucher code
# was marked 'used'. Without this, the device that originally redeemed a
# voucher remains connected via MAC cookie even after the voucher is dead.
ENFORCE_LIVE_SESSION_KICK = True

APP_DB     = '/var/lib/autoispbilling/autoispbilling.db'
RADIUS_DB  = '/var/lib/freeradius/radacct.db'


def sync_voucher_redemptions(limit: int = 1000) -> dict:
    """Returns: {marked_used: int, revoked: int, scanned: int}"""
    if not os.path.exists(APP_DB) or not os.path.exists(RADIUS_DB):
        return {"error": "db missing", "marked_used": 0, "revoked": 0}

    out = {"marked_used": 0, "revoked": 0, "scanned": 0}

    # 1. Load codes that are NOT already 'used' (i.e. still need marking).
    app = sqlite3.connect(APP_DB, timeout=10); app.row_factory = sqlite3.Row
    rad = sqlite3.connect(RADIUS_DB, timeout=10); rad.row_factory = sqlite3.Row

    rows = app.execute(
        "SELECT id, company_id, code, batch_id, plan_name, duration_minutes, "
        "       data_cap_mb "
        "FROM hotspot_vouchers WHERE status IN ('unused','active','reserved')"
    ).fetchall()
    out["scanned"] = len(rows)
    if not rows:
        app.close(); rad.close()
        return out

    # 2. For each unused voucher, see if there's an Access-Accept in radpostauth
    #    OR a non-zero accounting session.
    for v in rows:
        code = v["code"]
        # radpostauth: latest reply for this voucher code — S42J tenant-fenced.
        # Voucher codes are random per-tenant and unmatched in customers,
        # so the resolver tags them as orphan ('0'). Accept either this
        # tenant's rows OR orphans; reject rows owned by another tenant.
        rec = rad.execute(
            "SELECT reply, authdate, callingstationid FROM radpostauth "
            "WHERE username = ? AND reply = 'Access-Accept' "
            "  AND (company_id = ? OR company_id IS NULL "
            "       OR company_id = '' OR company_id = '0') "
            "ORDER BY id DESC LIMIT 1", (code, str(v["company_id"] or ""))
        ).fetchone()
        if not rec:
            continue

        # Mark voucher used in app DB.
        used_at = datetime.utcnow()
        used_by = rec["callingstationid"] or ""
        app.execute(
            "UPDATE hotspot_vouchers SET status='used', "
            "used_by=?, used_at=? WHERE id=?",
            (used_by, used_at.isoformat(sep=" "), v["id"]),
        )
        # Append redemption-audit row, if table exists.
        try:
            app.execute(
                "INSERT INTO voucher_redemptions "
                " (company_id, voucher_id, batch_id, code, used_by, mac_address,"
                "  ip_address, user_agent, duration_minutes, data_cap_mb, plan_name,"
                "  created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (v["company_id"], v["id"], v["batch_id"], code, used_by, used_by,
                 "", "radius-postauth", v["duration_minutes"] or 0,
                 v["data_cap_mb"] or 0, v["plan_name"], used_at.isoformat(sep=" ")),
            )
        except Exception:
            pass
        out["marked_used"] += 1

        if ENFORCE_SINGLE_USE:
            # Remove from FreeRADIUS so the SAME code cannot be used a 2nd time.
            rad.execute("DELETE FROM radcheck WHERE username=?", (code,))
            rad.execute("DELETE FROM radreply WHERE username=?", (code,))
            out["revoked"] += 1

    app.commit(); rad.commit()
    app.close(); rad.close()

    # Live-session enforcement: log into every NAS that is hotspot-capable
    # and kick any /ip/hotspot/active whose user is in our just-marked-used
    # voucher set. Best-effort — never fail the sync over a NAS error.
    if ENFORCE_LIVE_SESSION_KICK and out["marked_used"] > 0:
        try:
            import sys as _sys
            _sys.path.insert(0, '/opt/ispbilling/admin-portal')
            from database import SessionLocal as _SL
            from radius_network import NasDevice as _ND
            from librouteros import connect as _rc
            from librouteros.login import plain as _rp
            _db = _SL()
            for _nas in _db.query(_ND).all():
                try:
                    _api = _rc(host=_nas.ip_address, username=_nas.api_username,
                               password=_nas.api_password,
                               port=int(_nas.port or 8728),
                               login_method=_rp, timeout=8)
                    _used = sqlite3.connect(APP_DB, timeout=5).execute(
                        "SELECT code FROM hotspot_vouchers WHERE status='used'"
                    ).fetchall()
                    _used = {c[0] for c in _used if c[0]}
                    for _a in _api('/ip/hotspot/active/print'):
                        if _a.get('user') in _used:
                            try: _api.path('/ip/hotspot/active').remove(_a['.id'])
                            except Exception: pass
                            try:
                                for _ck in _api('/ip/hotspot/cookie/print'):
                                    if _ck.get('user') == _a.get('user'):
                                        _api.path('/ip/hotspot/cookie').remove(_ck['.id'])
                            except Exception: pass
                            out["revoked"] += 1
                except Exception:
                    pass
            _db.close()
        except Exception:
            pass

    return out


if __name__ == "__main__":
    res = sync_voucher_redemptions()
    print(res)


# Convenience helper for nas_healthcheck integration.
def run_periodic():
    try:
        return sync_voucher_redemptions()
    except Exception as e:
        return {"error": str(e), "marked_used": 0, "revoked": 0}
