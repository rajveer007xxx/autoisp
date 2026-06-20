#!/usr/bin/env python3
"""Standalone SLA escalation cron — no FastAPI import needed."""
import sqlite3
from datetime import datetime, timedelta
DB = "/var/lib/autoispbilling/autoispbilling.db"
SLA_DEFAULTS = {"low": 24*60, "medium": 8*60, "high": 4*60, "critical": 60}
eng = sqlite3.connect(DB)
eng.row_factory = sqlite3.Row
rows = eng.execute("""
    SELECT id, ticket_no, priority, status, sla_minutes, escalation_level, created_at
      FROM complaints
     WHERE status NOT IN ('Resolved','Closed','closed','resolved')
""").fetchall()
now = datetime.now()
breached = 0; escalated = 0
for r in rows:
    try:
        created = datetime.fromisoformat(str(r["created_at"]).replace("Z",""))
    except Exception:
        continue
    sla = int(r["sla_minutes"] or SLA_DEFAULTS.get((r["priority"] or "medium").lower(), 480))
    overdue_min = int((now - (created + timedelta(minutes=sla))).total_seconds() // 60)
    if overdue_min <= 0:
        continue
    breached += 1
    target = min(3, 1 + overdue_min // 60)
    if target > (r["escalation_level"] or 0):
        eng.execute("""UPDATE complaints
                          SET escalation_level=?, escalated_at=datetime('now'),
                              sla_breached=1
                        WHERE id=?""", (target, r["id"]))
        escalated += 1
    else:
        eng.execute("UPDATE complaints SET sla_breached=1 WHERE id=?", (r["id"],))
eng.commit(); eng.close()
print(f"[sla_cron] breached={breached} escalated={escalated} ts={now.isoformat()}")
