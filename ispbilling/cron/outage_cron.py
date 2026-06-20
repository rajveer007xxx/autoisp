#!/usr/bin/env python3
"""Outage detector — runs every 5 min via systemd timer.
If >=3 ONUs on the same OLT/PON port go offline within 10 min,
auto-create an outage_event + complaint."""
import sqlite3, json
from datetime import datetime, timedelta

DB = "/var/lib/autoispbilling/autoispbilling.db"
THRESHOLD = 3      # min offline count to trigger
WINDOW_MIN = 10    # rolling window in minutes

eng = sqlite3.connect(DB)
eng.row_factory = sqlite3.Row

# Snapshot offline ONUs (assumes onus table with status, last_seen, olt_id, pon_port_index)
try:
    rows = eng.execute("""
        SELECT olt_id, pon_port_index AS pon_port, COUNT(*) AS n,
               GROUP_CONCAT(serial, ',') AS ids,
               (SELECT company_id FROM olts WHERE olts.id = onus.olt_id) AS company_id
          FROM onus
         WHERE LOWER(COALESCE(status,'')) = 'offline'
           AND COALESCE(last_seen,'') >= datetime('now', ?)
         GROUP BY olt_id, pon_port_index
        HAVING n >= ?
    """, (f'-{WINDOW_MIN} minutes', THRESHOLD)).fetchall()
except sqlite3.OperationalError as e:
    print(f"[outage_cron] schema not ready: {e}")
    rows = []

opened = 0
for r in rows:
    company_id = r["company_id"] or ""
    if not company_id:
        continue
    # Already-open event for this PON port?
    existing = eng.execute("""SELECT id FROM outage_events
                                WHERE company_id=? AND olt_id=? AND pon_port_index=?
                                  AND status='open'""",
                           (company_id, r["olt_id"], r["pon_port_index"])).fetchone()
    if existing:
        # update count & affected list only
        eng.execute("""UPDATE outage_events
                          SET onu_count=?, affected_ids=?
                        WHERE id=?""",
                    (r["n"], json.dumps((r["ids"] or "").split(",")), existing["id"]))
        continue
    # New outage — create event + complaint
    eng.execute("""INSERT INTO outage_events
        (company_id, olt_id, pon_port_index, onu_count, affected_ids, status)
        VALUES (?, ?, ?, ?, ?, 'open')""",
        (company_id, r["olt_id"], r["pon_port_index"], r["n"],
         json.dumps((r["ids"] or "").split(","))))
    oid = eng.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Auto-complaint
    ticket_no = f"OUT{datetime.now().strftime('%Y%m%d%H%M%S')}-{r['olt_id']}-{r['pon_port_index']}"
    eng.execute("""INSERT INTO complaints
        (company_id, customer_id, ticket_no, complaint_type, priority,
         subject, description, status, kind, source, target_role, sla_minutes)
        VALUES (?, NULL, ?, 'Outage', 'critical', ?, ?, 'Open',
                'Auto-Outage', 'system', 'admin', 60)""",
        (company_id, ticket_no,
         f"PON Outage detected — OLT {r['olt_id']} / PON {r['pon_port_index']}",
         f"{r['n']} ONUs went offline within last {WINDOW_MIN} min on OLT-{r['olt_id']}, PON {r['pon_port_index']}. Affected: {r['ids']}"))
    cid = eng.execute("SELECT last_insert_rowid()").fetchone()[0]
    eng.execute("UPDATE outage_events SET complaint_id=? WHERE id=?", (cid, oid))
    opened += 1

eng.commit(); eng.close()
print(f"[outage_cron] {datetime.now().isoformat()} new={opened} active={len(rows)}")
