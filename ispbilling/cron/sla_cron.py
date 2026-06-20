#!/usr/bin/env python3
"""[PHASE-3] Standalone SLA escalation cron — routed through db_compat → PG."""
import sys
from datetime import datetime, timedelta, timezone

if "/opt/ispbilling" not in sys.path:
    sys.path.insert(0, "/opt/ispbilling")
import db_compat  # noqa: E402

SLA_DEFAULTS = {"low": 24 * 60, "medium": 8 * 60, "high": 4 * 60, "critical": 60}

con = db_compat.get_raw_conn(timeout=10.0)
cur = con.cursor()
cur.execute(
    """
    SELECT id, ticket_no, priority, status, sla_minutes, escalation_level, created_at
      FROM complaints
     WHERE status NOT IN ('Resolved','Closed','closed','resolved')
    """
)
rows = cur.fetchall()

now = datetime.now(timezone.utc)
breached = 0
escalated = 0
for r in rows:
    # tuple indices match SELECT order
    _id, _ticket, prio, _st, sla_min, esc_lvl, created_at = r
    try:
        if isinstance(created_at, datetime):
            created = created_at
        else:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        # Make tz-aware (assume UTC if naive — matches DB default)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except Exception:
        continue
    sla = int(sla_min or SLA_DEFAULTS.get((prio or "medium").lower(), 480))
    overdue_min = int((now - (created + timedelta(minutes=sla))).total_seconds() // 60)
    if overdue_min <= 0:
        continue
    breached += 1
    target = min(3, 1 + overdue_min // 60)
    if target > (esc_lvl or 0):
        cur.execute(
            "UPDATE complaints SET escalation_level=?, escalated_at=datetime('now'), "
            "sla_breached=1 WHERE id=?",
            (target, _id),
        )
        escalated += 1
    else:
        cur.execute("UPDATE complaints SET sla_breached=1 WHERE id=?", (_id,))

try:
    con.commit()
except Exception:
    pass
con.close()
print(f"[sla_cron] breached={breached} escalated={escalated} ts={now.isoformat()}")
