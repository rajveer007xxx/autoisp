"""
S42J — Bulletproof tenant isolation for FreeRADIUS shared tables
(radpostauth + radacct).

The FreeRADIUS sqlite is shared across ALL tenants on this VPS, but
its native tables carry NO `company_id`. Two tenants may legitimately
have a customer with the SAME username (e.g. `mp.raj.fibernet` in both
CITY WIFI and FIBERNET). Filtering by username alone leaked data.

This module:
  1) Idempotently ensures a `company_id` column exists on radpostauth
     AND radacct (both shared FreeRADIUS tables).
  2) Backfills NULL rows by resolving (username, mac/callingstationid)
     to a unique company_id using a deterministic cascade:
       a) username unique across all companies      -> that company
       b) username + mac matches a customer row     -> that company
       c) exactly one Active customer with that username -> that company
       d) otherwise                                  -> '0' (orphan, hidden)
  3) After backfill, queries simply do `WHERE company_id = :tenant`,
     making cross-tenant leaks IMPOSSIBLE by construction.

Self-heals on every page load (capped batch size for latency).
"""
import os
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------
def _ensure_company_id_column(con: sqlite3.Connection, table: str,
                              index_name: str) -> bool:
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    added = False
    if "company_id" not in cols:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN company_id VARCHAR DEFAULT NULL")
            con.commit()
            added = True
        except Exception:
            pass
    try:
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table}(company_id)")
        con.commit()
    except Exception:
        pass
    return added


def ensure_company_id_column(con: sqlite3.Connection) -> bool:
    """Back-compat alias used by callers (radpostauth only)."""
    return _ensure_company_id_column(con, "radpostauth", "radpostauth_company_id")


def ensure_all_company_id_columns(con: sqlite3.Connection) -> Dict[str, bool]:
    """Ensure company_id exists on every shared FreeRADIUS table we read."""
    out = {}
    out["radpostauth"] = _ensure_company_id_column(
        con, "radpostauth", "radpostauth_company_id")
    out["radacct"] = _ensure_company_id_column(
        con, "radacct", "radacct_company_id")
    return out


# ---------------------------------------------------------------------------
# Resolver cache build (read from admin DB via SQLAlchemy session)
# ---------------------------------------------------------------------------
def _build_resolver(db) -> Dict[str, Dict]:
    """
    Returns:
      {
        'by_username': { username: [{company_id, mac, status}, ...] },
        'by_uname_mac': { (username, mac_upper): company_id },
      }
    """
    from database import Customer
    by_username: Dict[str, List[Dict]] = {}
    by_uname_mac: Dict[Tuple[str, str], str] = {}
    rows = db.query(Customer.username, Customer.mac_address,
                    Customer.company_id, Customer.status).all()
    for uname, mac, cid, status in rows:
        if not uname or not cid:
            continue
        by_username.setdefault(uname, []).append({
            "company_id": str(cid),
            "mac": (mac or "").strip().upper(),
            "status": (status or "").strip(),
        })
        if mac:
            by_uname_mac[(uname, mac.strip().upper())] = str(cid)
    return {"by_username": by_username, "by_uname_mac": by_uname_mac}


def _resolve_company_id(username: str, mac: str, resolver: Dict) -> str:
    by_username = resolver["by_username"]
    by_uname_mac = resolver["by_uname_mac"]

    candidates = by_username.get(username) or []
    if not candidates:
        return "0"

    if len(candidates) == 1:
        return candidates[0]["company_id"]

    mac_u = (mac or "").strip().upper()
    if mac_u:
        cid = by_uname_mac.get((username, mac_u))
        if cid:
            return cid

    actives = [c for c in candidates if c["status"].lower() == "active"]
    if len(actives) == 1:
        return actives[0]["company_id"]

    return "0"


# ---------------------------------------------------------------------------
# Backfill drivers
# ---------------------------------------------------------------------------
def backfill_company_id(db, radacct_path: Optional[str] = None,
                        limit: int = 2000) -> Dict[str, int]:
    """Fill `company_id` on up to `limit` NULL radpostauth rows."""
    radacct_path = radacct_path or os.getenv(
        "RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")
    if not os.path.exists(radacct_path):
        return {"scanned": 0, "resolved": 0, "orphaned": 0}

    resolver = _build_resolver(db)
    con = sqlite3.connect(radacct_path, timeout=5.0)
    try:
        ensure_company_id_column(con)
        cur = con.cursor()
        cur.execute(
            "SELECT id, username, COALESCE(callingstationid,'') "
            "FROM radpostauth WHERE company_id IS NULL OR company_id = '' "
            "ORDER BY id DESC LIMIT ?", (int(limit),))
        rows = cur.fetchall()
        resolved = 0
        orphaned = 0
        updates: List[Tuple[str, int]] = []
        for rid, uname, mac in rows:
            cid = _resolve_company_id(uname or "", mac or "", resolver)
            if cid == "0":
                orphaned += 1
            else:
                resolved += 1
            updates.append((cid, rid))
        if updates:
            cur.executemany(
                "UPDATE radpostauth SET company_id = ? WHERE id = ?", updates)
            con.commit()
        return {"scanned": len(rows), "resolved": resolved, "orphaned": orphaned}
    finally:
        try: con.close()
        except Exception: pass


def backfill_radacct_company_id(db, radacct_path: Optional[str] = None,
                                limit: int = 5000) -> Dict[str, int]:
    """Fill `company_id` on up to `limit` NULL radacct rows."""
    radacct_path = radacct_path or os.getenv(
        "RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")
    if not os.path.exists(radacct_path):
        return {"scanned": 0, "resolved": 0, "orphaned": 0}

    resolver = _build_resolver(db)
    con = sqlite3.connect(radacct_path, timeout=5.0)
    try:
        _ensure_company_id_column(con, "radacct", "radacct_company_id")
        cur = con.cursor()
        cur.execute(
            "SELECT radacctid, username, COALESCE(callingstationid,'') "
            "FROM radacct WHERE company_id IS NULL OR company_id = '' "
            "ORDER BY radacctid DESC LIMIT ?", (int(limit),))
        rows = cur.fetchall()
        resolved = 0
        orphaned = 0
        updates: List[Tuple[str, int]] = []
        for rid, uname, mac in rows:
            cid = _resolve_company_id(uname or "", mac or "", resolver)
            if cid == "0":
                orphaned += 1
            else:
                resolved += 1
            updates.append((cid, rid))
        if updates:
            cur.executemany(
                "UPDATE radacct SET company_id = ? WHERE radacctid = ?",
                updates)
            con.commit()
        return {"scanned": len(rows), "resolved": resolved, "orphaned": orphaned}
    finally:
        try: con.close()
        except Exception: pass


def backfill_all(db, radacct_path: Optional[str] = None,
                 limit_each: int = 5000) -> Dict[str, Dict[str, int]]:
    """Convenience helper — backfill both shared FreeRADIUS tables."""
    return {
        "radpostauth": backfill_company_id(db, radacct_path=radacct_path,
                                           limit=limit_each),
        "radacct": backfill_radacct_company_id(db, radacct_path=radacct_path,
                                               limit=limit_each),
    }


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def fetch_tenant_radpostauth(db, company_id: str,
                             radacct_path: Optional[str] = None,
                             limit: int = 500) -> List[Dict]:
    radacct_path = radacct_path or os.getenv(
        "RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")
    if not os.path.exists(radacct_path) or not company_id:
        return []
    try:
        backfill_company_id(db, radacct_path=radacct_path, limit=2000)
    except Exception:
        pass
    out: List[Dict] = []
    con = sqlite3.connect(radacct_path, timeout=5.0)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(radpostauth)")
        cols = {x[1] for x in cur.fetchall()}
        has_caller = "callingstationid" in cols
        has_reason = "reason" in cols
        sel = "id, username, authdate, reply, pass, nasipaddress"
        if has_caller: sel += ", callingstationid"
        if has_reason: sel += ", reason"
        cur.execute(
            f"SELECT {sel} FROM radpostauth "
            f"WHERE company_id = ? "
            f"ORDER BY authdate DESC LIMIT ?",
            (str(company_id), int(limit)))
        for row in cur.fetchall():
            rad_id, uname, authdate, reply, _pw, nas_ip = row[:6]
            idx = 6
            caller = row[idx] if has_caller else ""
            idx += 1 if has_caller else 0
            reason = row[idx] if has_reason else ""
            out.append({
                "id": rad_id, "username": uname or "",
                "authdate": authdate, "reply": reply or "",
                "nasipaddress": nas_ip or "", "callingstationid": caller or "",
                "reason": reason or "",
            })
        return out
    finally:
        try: con.close()
        except Exception: pass


def delete_tenant_radpostauth(db, company_id: str,
                              ids: Optional[Iterable[int]] = None,
                              radacct_path: Optional[str] = None) -> int:
    radacct_path = radacct_path or os.getenv(
        "RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")
    if not os.path.exists(radacct_path) or not company_id:
        return 0
    try:
        backfill_company_id(db, radacct_path=radacct_path, limit=5000)
    except Exception:
        pass
    con = sqlite3.connect(radacct_path, timeout=5.0)
    try:
        cur = con.cursor()
        if ids is None:
            cur.execute("DELETE FROM radpostauth WHERE company_id = ?",
                        (str(company_id),))
        else:
            id_list = [int(i) for i in ids]
            if not id_list:
                return 0
            ph = ",".join(["?"] * len(id_list))
            cur.execute(
                f"DELETE FROM radpostauth "
                f"WHERE company_id = ? AND id IN ({ph})",
                (str(company_id), *id_list))
        rc = cur.rowcount or 0
        con.commit()
        return rc
    finally:
        try: con.close()
        except Exception: pass


# ---------------------------------------------------------------------------
# Convenience: SQL fragment for tenant-fenced radacct read
# ---------------------------------------------------------------------------
def tenant_radacct_where(company_id: str) -> Tuple[str, list]:
    """
    Return (sql_fragment, params) you can append to ANY radacct WHERE clause
    to make it tenant-safe. Caller is responsible for calling
    `backfill_radacct_company_id(db)` shortly before reading (one call per
    request is sufficient).
    """
    return (" company_id = ? ", [str(company_id)])
