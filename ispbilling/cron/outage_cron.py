#!/usr/bin/env python3
"""[PHASE-3] Outage detector — routed through db_compat → PG.

If >=3 ONUs on the same OLT/PON port go offline within 10 min, auto-create
an outage_event + complaint.
"""
import sys
import json
from datetime import datetime

if "/opt/ispbilling" not in sys.path:
    sys.path.insert(0, "/opt/ispbilling")
import db_compat  # noqa: E402

THRESHOLD = 3
WINDOW_MIN = 10

con = db_compat.get_raw_conn(timeout=10.0)
cur = con.cursor()

# Snapshot offline ONUs. NOTE: GROUP_CONCAT(serial, ',') in PG is string_agg —
# db_compat's translator does NOT rewrite group_concat, so use string_agg form.
# We construct two variants: SQLite (legacy) and PG. Pick whichever runs.
def fetch_offline_groups():
    try:
        cur.execute(
            f"""
            SELECT olt_id, pon_port_index AS pon_port, COUNT(*) AS n,
                   STRING_AGG(serial, ',') AS ids,
                   (SELECT company_id FROM olts WHERE olts.id = onus.olt_id) AS company_id
              FROM onus
             WHERE LOWER(COALESCE(status,'')) = 'offline'
               AND COALESCE(last_seen,'') >= (NOW() - INTERVAL '{WINDOW_MIN} minutes')::text
             GROUP BY olt_id, pon_port_index
            HAVING COUNT(*) >= {THRESHOLD}
            """
        )
        return cur.fetchall()
    except Exception as e:
        print(f"[outage_cron] PG variant failed → {e}; trying SQLite variant")
        try:
            cur.execute(
                """
                SELECT olt_id, pon_port_index AS pon_port, COUNT(*) AS n,
                       GROUP_CONCAT(serial, ',') AS ids,
                       (SELECT company_id FROM olts WHERE olts.id = onus.olt_id) AS company_id
                  FROM onus
                 WHERE LOWER(COALESCE(status,'')) = 'offline'
                   AND COALESCE(last_seen,'') >= datetime('now', ?)
                 GROUP BY olt_id, pon_port_index
                HAVING n >= ?
                """,
                (f"-{WINDOW_MIN} minutes", THRESHOLD),
            )
            return cur.fetchall()
        except Exception as e2:
            print(f"[outage_cron] schema not ready: {e2}")
            return []


rows = fetch_offline_groups()
opened = 0
for r in rows:
    # tuple order matches SELECT: olt_id, pon_port, n, ids, company_id
    olt_id, pon_port, n, ids, company_id = r[0], r[1], r[2], r[3], r[4]
    company_id = company_id or ""
    if not company_id:
        continue
    cur.execute(
        "SELECT id FROM outage_events WHERE company_id=? AND olt_id=? "
        "AND pon_port_index=? AND status='open'",
        (company_id, olt_id, pon_port),
    )
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE outage_events SET onu_count=?, affected_ids=? WHERE id=?",
            (n, json.dumps((ids or "").split(",")), existing[0]),
        )
        continue
    cur.execute(
        "INSERT INTO outage_events "
        "(company_id, olt_id, pon_port_index, onu_count, affected_ids, status) "
        "VALUES (?, ?, ?, ?, ?, 'open')",
        (company_id, olt_id, pon_port, n, json.dumps((ids or "").split(","))),
    )
    # Get inserted id — db_compat's _PGConnWrap surfaces lastrowid via RETURNING
    try:
        oid = cur.lastrowid
    except Exception:
        oid = None
    if oid is None:
        cur.execute("SELECT MAX(id) FROM outage_events WHERE company_id=?", (company_id,))
        oid = cur.fetchone()[0]
    ticket_no = f"OUT{datetime.now().strftime('%Y%m%d%H%M%S')}-{olt_id}-{pon_port}"
    cur.execute(
        "INSERT INTO complaints "
        "(company_id, customer_id, ticket_no, complaint_type, priority, "
        "subject, description, status, kind, source, target_role, sla_minutes) "
        "VALUES (?, NULL, ?, 'Outage', 'critical', ?, ?, 'Open', "
        "'Auto-Outage', 'system', 'admin', 60)",
        (
            company_id, ticket_no,
            f"PON Outage detected — OLT {olt_id} / PON {pon_port}",
            f"{n} ONUs went offline within last {WINDOW_MIN} min on OLT-{olt_id}, "
            f"PON {pon_port}. Affected: {ids}",
        ),
    )
    try:
        cid = cur.lastrowid
    except Exception:
        cid = None
    if cid is None:
        cur.execute("SELECT MAX(id) FROM complaints WHERE ticket_no=?", (ticket_no,))
        cid = cur.fetchone()[0]
    cur.execute("UPDATE outage_events SET complaint_id=? WHERE id=?", (cid, oid))
    opened += 1

try:
    con.commit()
except Exception:
    pass
con.close()
print(f"[outage_cron] {datetime.now().isoformat()} new={opened} active={len(rows)}")
