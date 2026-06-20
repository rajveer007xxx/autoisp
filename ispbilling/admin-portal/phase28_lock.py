"""
phase28_lock.py — Sub-LCO Lock feature (admin-portal)

WHAT IT DOES
  Admins can hide selected Sub-LCOs (and all customers added by them or by their
  employees) from THEIR OWN admin portal view. Sub-LCOs and their customers
  continue to function normally (login, RADIUS, billing, payments).

  Locking requires a password, set once per admin company on first use.
  Unlocking restores the customers in the admin's view INCLUDING any data
  generated during the lock period.

PUBLIC HELPERS (used by other admin endpoints)
  - hidden_customer_ids(company_id, db) -> set[str]
  - hidden_usernames(company_id, db) -> set[str]
  - is_admin_view(request) -> bool
  - apply_customer_filter(query, Customer, company_id, db, request) -> SQLAlchemy query
  - is_lock_active(company_id) -> bool   # any sub-lco currently locked
"""
from __future__ import annotations

import os
import re
import sqlite3
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional, Set

from fastapi import Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

DB_PATH = os.environ.get("ISPBILLING_DB",
                         "/var/lib/autoispbilling/autoispbilling.db")


# ─────────────────────────────────── DDL ──────────────────────────────────
def _ensure_tables():
    con = _compat_conn(timeout=15)
    try:
        cur = con.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS lock_secrets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id    TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            algo          TEXT DEFAULT 'sha256',
            set_at        DATETIME DEFAULT (CURRENT_TIMESTAMP),
            set_by        TEXT
        );
        CREATE TABLE IF NOT EXISTS sub_lco_locks (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id    TEXT NOT NULL,
            sub_lco_id    INTEGER NOT NULL,
            locked_at     DATETIME DEFAULT (CURRENT_TIMESTAMP),
            locked_by     TEXT,
            UNIQUE(company_id, sub_lco_id)
        );
        CREATE INDEX IF NOT EXISTS idx_lock_lookup
            ON sub_lco_locks(company_id, sub_lco_id);
        """)
        con.commit()
    finally:
        con.close()


def _conn():
    c = _compat_conn(timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _hash_pw(pw: str) -> str:
    """SHA-256 with per-installation salt env var (fallback constant)."""
    salt = os.environ.get("ISP_LOCK_SALT", "isp-billing-lock-salt-2026")
    return hashlib.sha256((salt + "|" + (pw or "")).encode("utf-8")).hexdigest()


# ─────────────────────────────────── core ─────────────────────────────────
def is_lock_password_set(cid: str) -> bool:
    if not cid: return False
    with _conn() as c:
        r = c.execute("SELECT 1 FROM lock_secrets WHERE company_id=?", (cid,)).fetchone()
    return bool(r)


def set_lock_password(cid: str, pw: str, actor: str = "") -> bool:
    if not cid or not pw or len(pw) < 4:
        return False
    h = _hash_pw(pw)
    with _conn() as c:
        c.execute("INSERT INTO lock_secrets(company_id, password_hash, set_by) "
                  "VALUES (?,?,?) "
                  "ON CONFLICT(company_id) DO UPDATE SET "
                  "password_hash=excluded.password_hash, "
                  "set_at=CURRENT_TIMESTAMP, set_by=excluded.set_by",
                  (cid, h, actor))
        c.commit()
    return True


def verify_lock_password(cid: str, pw: str) -> bool:
    if not cid or not pw: return False
    h = _hash_pw(pw)
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM lock_secrets WHERE company_id=? AND password_hash=?",
            (cid, h)).fetchone()
    return bool(r)


def is_sub_lco_locked(cid: str, slco_id: int) -> bool:
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM sub_lco_locks WHERE company_id=? AND sub_lco_id=?",
            (cid, int(slco_id))).fetchone()
    return bool(r)


def lock_sub_lco(cid: str, slco_id: int, actor: str = "") -> bool:
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO sub_lco_locks(company_id,sub_lco_id,locked_by) "
                  "VALUES (?,?,?)", (cid, int(slco_id), actor))
        c.commit()
    return True


def unlock_sub_lco(cid: str, slco_id: int) -> bool:
    with _conn() as c:
        c.execute("DELETE FROM sub_lco_locks WHERE company_id=? AND sub_lco_id=?",
                  (cid, int(slco_id)))
        c.commit()
    return True


def locked_sub_lco_ids(cid: str) -> Set[int]:
    with _conn() as c:
        rows = c.execute(
            "SELECT sub_lco_id FROM sub_lco_locks WHERE company_id=?",
            (cid,)).fetchall()
    return {int(r["sub_lco_id"]) for r in rows}


def is_lock_active(cid: str) -> bool:
    return len(locked_sub_lco_ids(cid)) > 0


# ─────────────────── Universal admin-side filter helpers ──────────────────
_HIDDEN_CACHE: dict = {}   # cid -> (cached_at, set[str])
_CACHE_TTL = 30            # seconds


def _resolve_hidden_customer_ids(cid: str, db: Session) -> Set[str]:
    """Return customer_ids for ALL customers under any locked sub-lco
       (directly or via that sub-lco's employees)."""
    slco_ids = locked_sub_lco_ids(cid)
    if not slco_ids:
        return set()
    try:
        from database import Customer, Employee
    except Exception:
        return set()

    # Direct: customer.sub_lco_id ∈ locked
    direct = {c.customer_id for c in db.query(Customer.customer_id).filter(
        Customer.company_id == cid,
        Customer.sub_lco_id.in_(slco_ids)).all()}

    # Via employees: customer.created_by_employee_id ∈ employees-of-locked-sub-lcos
    emp_ids = {e.id for e in db.query(Employee.id).filter(
        Employee.company_id == cid,
        Employee.sub_lco_id.in_(slco_ids)).all()}
    via_emp = set()
    if emp_ids:
        via_emp = {c.customer_id for c in db.query(Customer.customer_id).filter(
            Customer.company_id == cid,
            Customer.created_by_employee_id.in_(emp_ids)).all()}

    return direct | via_emp


def hidden_customer_ids(cid: str, db: Session) -> Set[str]:
    """Cached wrapper. Keys off (cid, sorted-locked-set)."""
    if not cid: return set()
    now_ts = datetime.now().timestamp()
    cache_key = (cid, tuple(sorted(locked_sub_lco_ids(cid))))
    cached = _HIDDEN_CACHE.get(cache_key)
    if cached and (now_ts - cached[0]) < _CACHE_TTL:
        return cached[1]
    s = _resolve_hidden_customer_ids(cid, db)
    _HIDDEN_CACHE[cache_key] = (now_ts, s)
    # Trim any stale entries belonging to this cid
    for k in list(_HIDDEN_CACHE.keys()):
        if k[0] == cid and k != cache_key:
            _HIDDEN_CACHE.pop(k, None)
    return s


def hidden_usernames(cid: str, db: Session) -> Set[str]:
    """For radacct/IPDR/URL filtering by username (PPPoE login)."""
    cust_ids = hidden_customer_ids(cid, db)
    if not cust_ids:
        return set()
    try:
        from database import Customer
    except Exception:
        return set()
    rows = db.query(Customer.username).filter(
        Customer.company_id == cid,
        Customer.customer_id.in_(cust_ids)).all()
    return {r.username for r in rows if r.username}


def is_admin_view(request: Request) -> bool:
    """True only when the requestor is the tenant admin (not sub_lco / employee /
       superadmin / customer / internal-token)."""
    try:
        utype = (request.session.get("user_type") or "").lower()
    except Exception:
        return False
    # 'admin' role = tenant admin. Apply hide filter only here.
    return utype == "admin"


def hidden_customer_ids_for_request(request: Request, db: Session) -> Set[str]:
    """Returns hidden ids ONLY when the requestor should be filtered (admin),
       else returns empty set so non-admin requests see everything."""
    if not is_admin_view(request):
        return set()
    cid = (request.session.get("company_id") or "")
    return hidden_customer_ids(cid, db)


# ─────────────────────────── Route mounting ───────────────────────────────
def mount(app, templates, get_db, require_admin):
    _ensure_tables()

    @app.get("/api/admin/lock/state")
    async def api_lock_state(request: Request, db: Session = Depends(get_db)):
        gate = require_admin(request)
        if gate: return gate
        cid = request.session.get("company_id") or ""
        return {
            "success": True,
            "password_set": is_lock_password_set(cid),
            "locked_ids": sorted(locked_sub_lco_ids(cid)),
        }

    @app.post("/api/admin/lock/set-password")
    async def api_lock_set_password(request: Request, db: Session = Depends(get_db)):
        gate = require_admin(request)
        if gate: return gate
        cid = request.session.get("company_id") or ""
        actor = request.session.get("user_id") or ""
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        pw = (data.get("password") or "").strip()
        confirm = (data.get("confirm") or "").strip()
        if len(pw) < 4 or pw != confirm:
            return JSONResponse({"success": False,
                "message": "Password must be at least 4 chars and match confirm field"},
                status_code=400)
        # Disallow overwriting if already set; require old password instead.
        if is_lock_password_set(cid):
            old = (data.get("old_password") or "").strip()
            if not verify_lock_password(cid, old):
                return JSONResponse({"success": False,
                    "message": "Existing password incorrect"}, status_code=403)
        ok = set_lock_password(cid, pw, actor)
        return {"success": ok, "message": "Password saved" if ok else "Failed"}

    @app.post("/api/admin/lock/sub-lco/{slco_id}")
    async def api_lock_sub_lco(slco_id: int, request: Request,
                                  db: Session = Depends(get_db)):
        gate = require_admin(request)
        if gate: return gate
        cid = request.session.get("company_id") or ""
        actor = request.session.get("user_id") or ""
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        pw = (data.get("password") or "").strip()
        if not is_lock_password_set(cid):
            return JSONResponse({"success": False, "code": "PW_NOT_SET",
                "message": "Set the lock password first"}, status_code=400)
        if not verify_lock_password(cid, pw):
            return JSONResponse({"success": False, "code": "PW_BAD",
                "message": "Incorrect lock password"}, status_code=403)
        # Verify the sub-lco belongs to this tenant
        with _conn() as c:
            row = c.execute(
                "SELECT id FROM sub_lcos WHERE id=? AND company_id=?",
                (slco_id, cid)).fetchone()
        if not row:
            return JSONResponse({"success": False,
                "message": "Sub-LCO not found"}, status_code=404)
        action = "lock" if not is_sub_lco_locked(cid, slco_id) else "unlock"
        if action == "lock":
            lock_sub_lco(cid, slco_id, actor)
        else:
            unlock_sub_lco(cid, slco_id)
        # Bust cache for this cid
        for k in list(_HIDDEN_CACHE.keys()):
            if k[0] == cid: _HIDDEN_CACHE.pop(k, None)
        return {"success": True, "action": action,
                "locked": action == "lock"}

    print("[phase28_lock] registered: lock secrets + sub-lco lock toggles")
