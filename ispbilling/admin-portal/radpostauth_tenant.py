"""S42J — Tenant-isolated PG queries for FreeRADIUS shared tables.

Post-Phase-Final rewrite: FreeRADIUS tables (radpostauth, radacct) now live in
the central PostgreSQL `autoispbilling` DB. We DON'T touch SQLite any more.
The function signatures stay backward-compatible so existing callers work.
"""
import os
from typing import Dict, List, Optional
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Backfill helpers (idempotent, no-op when nothing to backfill)
# ---------------------------------------------------------------------------
def _backfill_pg(db, table: str, limit: int = 5000) -> Dict[str, int]:
    """Backfill company_id on a RADIUS shared table by joining on customers."""
    sql = text(f"""
        WITH cte AS (
            SELECT ra.{ "radacctid" if table == "radacct" else "id" } AS pk,
                   c.company_id AS cid
              FROM {table} ra
              JOIN customers c ON ra.username = c.username
             WHERE ra.company_id IS NULL OR ra.company_id = ''
             LIMIT :lim
        )
        UPDATE {table} SET company_id = cte.cid
          FROM cte WHERE {table}.{ "radacctid" if table == "radacct" else "id" } = cte.pk
    """)
    n = db.execute(sql, {"lim": int(limit)}).rowcount or 0
    try: db.commit()
    except Exception: pass
    return {"scanned": n, "resolved": n, "orphaned": 0}


def backfill_company_id(db, radacct_path: Optional[str] = None,
                        limit: int = 5000) -> Dict[str, int]:
    return _backfill_pg(db, "radpostauth", limit)


def backfill_radacct_company_id(db, radacct_path: Optional[str] = None,
                                limit: int = 5000) -> Dict[str, int]:
    return _backfill_pg(db, "radacct", limit)


def backfill_all(db, radacct_path: Optional[str] = None,
                 limit_each: int = 5000) -> Dict[str, Dict[str, int]]:
    return {
        "radpostauth": _backfill_pg(db, "radpostauth", limit_each),
        "radacct": _backfill_pg(db, "radacct", limit_each),
    }


def ensure_company_id_column(con):
    """Back-compat alias — schema is now PG-managed, this is a no-op."""
    return False


def ensure_all_company_id_columns(con):
    """No-op (PG schema is canonical)."""
    return {"radpostauth": False, "radacct": False}


# ---------------------------------------------------------------------------
# Tenant-scoped readers
# ---------------------------------------------------------------------------
def fetch_tenant_radpostauth(db, company_id: str,
                             radacct_path: Optional[str] = None,
                             limit: int = 500) -> List[Dict]:
    if not company_id:
        return []
    try:
        _backfill_pg(db, "radpostauth", limit=2000)
    except Exception:
        pass

    sql = text("""
        SELECT id, username, authdate, reply, nasipaddress,
               COALESCE(calledstationid, '') AS calledstationid,
               COALESCE(callingstationid, '') AS callingstationid,
               COALESCE(reason, '') AS reason
          FROM radpostauth
         WHERE company_id = :cid
         ORDER BY authdate DESC
         LIMIT :lim
    """)
    rows = db.execute(sql, {"cid": str(company_id), "lim": int(limit)}).mappings().all()
    out: List[Dict] = []
    for r in rows:
        out.append({
            "id":               r.get("id"),
            "username":         r.get("username") or "",
            "authdate":         r.get("authdate"),
            "reply":            r.get("reply") or "",
            "nasipaddress":     str(r.get("nasipaddress") or "") if r.get("nasipaddress") else "",
            "callingstationid": r.get("callingstationid") or "",
            "calledstationid":  r.get("calledstationid") or "",
            "reason":           r.get("reason") or "",
        })
    return out


def fetch_tenant_radacct(db, company_id: str,
                         radacct_path: Optional[str] = None,
                         limit: int = 500) -> List[Dict]:
    """List sessions for a tenant from PG radacct."""
    if not company_id:
        return []
    try:
        _backfill_pg(db, "radacct", limit=2000)
    except Exception:
        pass
    sql = text("""
        SELECT radacctid, acctsessionid, username, nasipaddress::text AS nasip,
               framedipaddress::text AS framedip, callingstationid, calledstationid,
               acctstarttime, acctstoptime, acctsessiontime,
               acctinputoctets, acctoutputoctets
          FROM radacct
         WHERE company_id = :cid
         ORDER BY acctstarttime DESC NULLS LAST
         LIMIT :lim
    """)
    rows = db.execute(sql, {"cid": str(company_id), "lim": int(limit)}).mappings().all()
    return [dict(r) for r in rows]
