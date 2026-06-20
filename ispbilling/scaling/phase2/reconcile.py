"""
Phase 2 daily reconciliation — compares SQLite row counts + content hashes
to Postgres shadow. Fails loudly if divergence detected.

Schedule via systemd timer:
  isp-pg-reconcile.timer  → 04:00 IST daily
"""
import hashlib
import json
import os
import sqlite3
import sys
from sqlalchemy import create_engine, text

DB_SQLITE = "/var/lib/autoispbilling/autoispbilling.db"
DB_PG = os.environ["DUAL_WRITE_PG_URL"]
pg = create_engine(DB_PG, pool_pre_ping=True)

# Critical tables — must match exactly. Add more as you stabilise.
TABLES = ["customers", "companies", "admins", "invoices", "transactions",
          "plans", "nas_devices", "olts", "employees", "sub_lcos"]


def row_hash_sqlite(table):
    with sqlite3.connect(DB_SQLITE, timeout=30) as c:
        c.execute("PRAGMA busy_timeout=5000")
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        # Hash the concatenation of all row PKs and (optional) updated_at
        rows = c.execute(f"SELECT id, COALESCE(updated_at, '') FROM {table} "
                         f"ORDER BY id").fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{r[0]}|{r[1]}\n".encode())
    return n, h.hexdigest()


def row_hash_pg(table):
    with pg.connect() as c:
        n = c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        rows = c.execute(text(
            f"SELECT id, COALESCE(updated_at::text, '') FROM {table} ORDER BY id"
        )).all()
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{r[0]}|{r[1]}\n".encode())
    return n, h.hexdigest()


def main():
    report = {}
    bad = 0
    for t in TABLES:
        s_n, s_h = row_hash_sqlite(t)
        p_n, p_h = row_hash_pg(t)
        match = (s_n == p_n) and (s_h == p_h)
        report[t] = {"sqlite_n": s_n, "pg_n": p_n,
                     "sqlite_hash": s_h[:16], "pg_hash": p_h[:16],
                     "ok": match}
        print(f"  {t:20s}  sqlite={s_n:>7d}  pg={p_n:>7d}  "
              f"{'✓' if match else '✗ DRIFT'}")
        if not match:
            bad += 1

    out = "/var/log/autoispbilling/pg_reconcile.json"
    open(out, "w").write(json.dumps(report, indent=2))
    print(f"\n{'PASS' if bad == 0 else f'FAIL — {bad} drift'}")
    print(f"Report: {out}")
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
