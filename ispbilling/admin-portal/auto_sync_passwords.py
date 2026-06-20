"""Auto-sync detected PPPoE passwords from radpostauth -> customers DB.

Runs every 5 min via systemd timer. For every company, finds any
customer whose:
  - pppoe_password in DB is empty/null, OR differs from the password
    the CPE actually sent within the last 7 days (radpostauth.pass)
  - and the latest auth was Access-Accept (i.e. the CPE's password
    works — we trust it)

…and updates customers.pppoe_password silently. After updating, the
regular FreeRADIUS sync timer picks it up and writes isp-users; we
don't restart FR here to avoid disrupting traffic. RADIUS continues
to authenticate against radpostauth-captured passwords directly via
this DB column on the next isp-users rebuild.

This solves the chronic 'password keeps going empty in DB' issue —
once a CPE sends a valid PAP password and gets Access-Accept, we
permanently lock it into the billing DB so it survives:
  - radius restarts
  - isp-users regeneration
  - manual NAS rebuilds"""
import os, sys, sqlite3, time
from datetime import datetime

# Boot DB / models
sys.path.insert(0, "/opt/ispbilling/admin-portal")
os.chdir("/opt/ispbilling/admin-portal")

from database import SessionLocal, Customer  # type: ignore
from sqlalchemy import text as _sql

RADACCT = os.environ.get("RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")
MAX_AGE_H = 168  # 7 days

def _latest_passwords_for(uname_cid_pairs):
    """Return {(username, company_id): (password, authdate)} for latest
    Access-Accept capture within MAX_AGE_H hours.

    S42J — keyed by (username, company_id) so customers in different
    tenants who share a username CANNOT inherit each other's secrets.
    Relies on the `company_id` column on radpostauth (added by
    radpostauth_tenant.ensure_company_id_column).
    """
    out = {}
    if not uname_cid_pairs: return out
    usernames = sorted({u for (u, _c) in uname_cid_pairs if u})
    try:
        con = sqlite3.connect(f"file:{RADACCT}?mode=ro", uri=True, timeout=5)
        cur = con.cursor()
        # Chunk to stay under SQLite IN-list limit
        for i in range(0, len(usernames), 400):
            batch = usernames[i:i+400]
            placeholders = ",".join("?" * len(batch))
            q = (f"SELECT username, company_id, pass, authdate FROM radpostauth "
                 f"WHERE username IN ({placeholders}) "
                 f"  AND IFNULL(pass,'') <> '' "
                 f"  AND reply = 'Access-Accept' "
                 f"  AND company_id IS NOT NULL "
                 f"  AND company_id <> '' "
                 f"  AND company_id <> '0' "
                 f"  AND authdate >= datetime('now', ?) "
                 f"ORDER BY authdate DESC")
            for un, cid, pw, ad in cur.execute(q, list(batch) + [f"-{MAX_AGE_H} hours"]):
                key = (un, str(cid))
                if key not in out:
                    out[key] = (pw, ad)
        con.close()
    except Exception as e:
        print(f"[ERR] radpostauth read failed: {e}", file=sys.stderr)
    return out


def main():
    db = SessionLocal()
    started = time.time()
    try:
        # S42J — ensure the company_id column exists and recent rows are
        # backfilled BEFORE we read radpostauth. Otherwise the strict
        # `company_id IS NOT NULL` filter would drop fresh captures.
        try:
            from radpostauth_tenant import (ensure_company_id_column,
                                            backfill_company_id)
            con_bf = sqlite3.connect(RADACCT, timeout=5)
            try:
                ensure_company_id_column(con_bf)
            finally:
                con_bf.close()
            backfill_company_id(db, radacct_path=RADACCT, limit=20000)
        except Exception as _e:
            print(f"[WARN] s42j backfill skipped: {_e}", file=sys.stderr)

        # All Active PPPoE customers across ALL companies (per-tenant
        # would be wasted overhead; FreeRADIUS is shared on this box).
        cust_rows = (db.query(Customer)
                       .filter(Customer.username.isnot(None))
                       .filter(Customer.username != "")
                       .all())
        # S42J — keyed by (username, company_id) so two tenants sharing
        # a username never receive each other's captured password.
        pairs = sorted({((c.username or "").strip(), str(c.company_id or ""))
                        for c in cust_rows
                        if (c.username or "").strip() and (c.company_id or "")})
        captures = _latest_passwords_for(pairs)

        updates = 0
        empties_filled = 0
        diffs_locked = 0
        for c in cust_rows:
            un = (c.username or "").strip()
            cid = str(c.company_id or "")
            if not un or not cid:
                continue
            cap = captures.get((un, cid))
            if not cap:
                continue
            new_pw, when = cap
            old_pw = (c.pppoe_password or "")
            if not old_pw:
                c.pppoe_password = new_pw
                empties_filled += 1
                updates += 1
            elif old_pw != new_pw:
                # CPE has a NEWER different working password — trust
                # the live capture (CPE was reset / customer changed
                # the wifi password / staff updated CPE only).
                c.pppoe_password = new_pw
                diffs_locked += 1
                updates += 1

        if updates:
            db.commit()
        dur = time.time() - started
        print(f"[auto-sync-passwords] examined={len(cust_rows)} "
              f"captures={len(captures)} filled-empty={empties_filled} "
              f"updated-diff={diffs_locked} commits={updates} "
              f"took={dur:.2f}s")
    finally:
        db.close()


if __name__ == "__main__":
    main()
