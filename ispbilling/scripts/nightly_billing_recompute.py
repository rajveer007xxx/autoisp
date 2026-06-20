#!/usr/bin/env python3
"""_v4727_  Nightly recompute of cached billing columns from the live ledger.

Run by systemd timer at 02:30 IST. Reads invoices + payments per customer and
overwrites customers.received_amount and customers.bill_amount with the live
totals so the dashboard, list cards, and ledger never drift."""
import sqlite3
from datetime import datetime

DB = "/var/lib/autoispbilling/autoispbilling.db"

def main():
    started = datetime.now()
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode = WAL")
    cur = con.cursor()
    cur.execute("""
        SELECT customer_id, company_id FROM customers
    """)
    rows = cur.fetchall()
    fixed = 0
    for cust_id, cid in rows:
        try:
            inv = cur.execute(
                "SELECT COALESCE(SUM(total_amount),0) FROM invoices "
                " WHERE company_id=? AND customer_id=?",
                (cid, cust_id)).fetchone()[0] or 0
        except Exception: inv = 0
        try:
            pay = cur.execute(
                "SELECT COALESCE(SUM(amount),0) FROM payments "
                " WHERE company_id=? AND customer_id=?",
                (cid, cust_id)).fetchone()[0] or 0
        except Exception: pay = 0
        try:
            cur.execute(
                "UPDATE customers SET "
                "  bill_amount = ?, "
                "  received_amount = ?, "
                "  updated_at = ? "
                "WHERE company_id=? AND customer_id=?",
                (inv, pay, datetime.now().isoformat(timespec='seconds'),
                 cid, cust_id))
            fixed += 1
        except Exception: pass
    con.commit(); con.close()
    elapsed = (datetime.now() - started).total_seconds()
    print(f"[nightly-recompute] {fixed}/{len(rows)} rows refreshed in {elapsed:.2f}s")

if __name__ == "__main__":
    main()
