"""Factory-Reset Recovery helper.

When a successful WiFi / WAN / TR069 push happens, we persist the
pushed payload to onu_config_snapshots. After a factory reset the
newly-arrived ONU's serial is matched, and the latest snapshot of
each kind is re-applied automatically. This is the SmartOLT
"factory-reset-and-survive" behaviour."""

from __future__ import annotations
from typing import Any, Dict, Optional
import json


def record_snapshot(company_id: str, onu_id: int, serial: Optional[str],
                    kind: str, payload: Dict[str, Any],
                    pushed_by: Optional[str] = None) -> bool:
    """Persist a config snapshot. Never raises."""
    try:
        from database import engine
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            conn.execute(_t(
                "INSERT INTO onu_config_snapshots "
                "(company_id, onu_id, serial, kind, payload, pushed_by) "
                "VALUES (:cid, :oid, :sn, :k, CAST(:p AS JSONB), :u)"
            ), {"cid": company_id, "oid": onu_id, "sn": serial or None,
                "k": kind, "p": json.dumps(payload), "u": pushed_by})
        return True
    except Exception:
        return False


def latest_snapshots(company_id: str,
                     onu_id: Optional[int] = None,
                     serial: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Return {kind: payload_dict} for the latest snapshot of each kind."""
    if not (onu_id or serial):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from database import engine
        from sqlalchemy import text as _t
        where = ["company_id=:cid"]
        params = {"cid": company_id}
        if onu_id:
            where.append("onu_id=:oid"); params["oid"] = onu_id
        if serial:
            where.append("serial=:sn");  params["sn"] = serial
        sql = (
            "SELECT DISTINCT ON (kind) kind, payload "
            "FROM onu_config_snapshots WHERE "
            + " AND ".join(where) +
            " ORDER BY kind, id DESC"
        )
        with engine.begin() as conn:
            for kind, payload in conn.execute(_t(sql), params).fetchall():
                if isinstance(payload, str):
                    try: payload = json.loads(payload)
                    except Exception: continue
                out[kind] = payload
    except Exception:
        pass
    return out
