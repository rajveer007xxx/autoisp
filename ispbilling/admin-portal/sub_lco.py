"""
Session 38y — Sub-LCO (franchisee / reseller) module.

Provides:
  • DB schema additions (sub_lcos, sub_lco_commissions; columns added to
    customers + locations via raw SQL ALTER).
  • Auth helper + login branch.
  • Admin CRUD endpoints + UI for managing Sub-LCOs.
  • Sub-LCO portal endpoints (dashboard, customers, locations, commissions).
  • Commission recording hook invoked from /api/payments/create.
  • Strict data isolation via middleware + explicit sub_lco_id filters.

Safe by design: ships as an APIRouter so we don't risk breaking the 14k-line
main.py by AST-editing it. main.py only needs:
    import sub_lco
    sub_lco.ensure_schema(engine)
    app.include_router(sub_lco.admin_router)
    app.include_router(sub_lco.portal_router)
    app.add_middleware(sub_lco.SubLcoRestrictMiddleware)
"""
from __future__ import annotations

import random
import string
from datetime import datetime, date, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from database import get_db  # noqa: E402

templates = Jinja2Templates(directory="templates")

admin_router = APIRouter()
portal_router = APIRouter()


# ---------------------------------------------------------------------------
# Schema bootstrap (idempotent; runs at startup)
# ---------------------------------------------------------------------------
def ensure_schema(engine) -> None:
    """Create sub_lcos / sub_lco_commissions tables + add sub_lco_id columns.
    Idempotent — safe on every boot."""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sub_lcos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id VARCHAR(32) NOT NULL,
                sub_lco_code VARCHAR(32) NOT NULL,
                username VARCHAR(64) NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                name VARCHAR(128) NOT NULL,
                email VARCHAR(128),
                mobile VARCHAR(32),
                address VARCHAR(255),
                commission_percent REAL NOT NULL DEFAULT 0,
                status VARCHAR(16) NOT NULL DEFAULT 'Active',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (company_id, sub_lco_code)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sub_lco_commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id VARCHAR(32) NOT NULL,
                sub_lco_id INTEGER NOT NULL,
                customer_id VARCHAR(64) NOT NULL,
                payment_id INTEGER,
                base_amount REAL NOT NULL DEFAULT 0,
                commission_percent REAL NOT NULL DEFAULT 0,
                commission_amount REAL NOT NULL DEFAULT 0,
                note VARCHAR(255),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sub_lco_comm_company "
            "ON sub_lco_commissions(company_id, sub_lco_id, created_at)"
        ))
        # Add nullable sub_lco_id columns (idempotent via try/except — SQLite
        # has no "ADD COLUMN IF NOT EXISTS").
        for tbl in ("customers", "locations"):
            try:
                conn.execute(text(
                    f"ALTER TABLE {tbl} ADD COLUMN sub_lco_id INTEGER"
                ))
            except Exception:
                pass  # column already exists

        # P1: payout tracking
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sub_lco_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id VARCHAR(32) NOT NULL,
                sub_lco_id INTEGER NOT NULL,
                amount REAL NOT NULL DEFAULT 0,
                reference VARCHAR(128),
                notes VARCHAR(512),
                paid_at DATETIME NOT NULL,
                created_by VARCHAR(64),
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        for col, ddl in (
            ("payout_status", "ALTER TABLE sub_lco_commissions ADD COLUMN payout_status VARCHAR(16) DEFAULT 'Pending'"),
            ("payout_id",     "ALTER TABLE sub_lco_commissions ADD COLUMN payout_id INTEGER"),
            ("settled_at",    "ALTER TABLE sub_lco_commissions ADD COLUMN settled_at DATETIME"),
        ):
            try:
                conn.execute(text(ddl))
            except Exception:
                pass
        # Session 38y.4 — admin assigns a set of existing company locations to
        # each sub-LCO. The sub-LCO can only attach customers to these.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sub_lco_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_lco_id INTEGER NOT NULL,
                location_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (sub_lco_id, location_id)
            )
        """))


# ---------------------------------------------------------------------------
# Auth + session helpers
# ---------------------------------------------------------------------------
def authenticate_sub_lco(db: Session, company_id: str, sub_lco_code: str,
                         password: str) -> Optional[dict]:
    """Return a dict of the sub_lco row on success, else None."""
    # _S58Q_AUTH_FLEX_  accept code | username | email | mobile
    _id = (sub_lco_code or "").strip()
    row = db.execute(text(
        "SELECT id, company_id, sub_lco_code, username, password_hash, "
        "       name, email, mobile, commission_percent, status "
        "FROM sub_lcos WHERE company_id = :c AND ("
        "       sub_lco_code        = :s "
        "    OR LOWER(username)     = LOWER(:s) "
        "    OR LOWER(COALESCE(email,''))  = LOWER(:s) "
        "    OR REGEXP_REPLACE(COALESCE(mobile,''), '[^0-9]', '', 'g') "
        "         = REGEXP_REPLACE(:s, '[^0-9]', '', 'g') "
        "  ) LIMIT 1"
    ), {"c": company_id, "s": _id}).fetchone()
    if not row:
        return None
    if (row[9] or "").lower() != "active":
        return None
    try:
        ok = bcrypt.checkpw(password.encode(), (row[4] or "").encode())
    except Exception:
        ok = False
    if not ok:
        return None
    return {
        "id": row[0], "company_id": row[1], "sub_lco_code": row[2],
        "username": row[3], "name": row[5], "email": row[6],
        "mobile": row[7], "commission_percent": float(row[8] or 0),
        "status": row[9],
    }


def require_sub_lco(request: Request):
    """_S39R5C_ROLE_DENY — gate Sub-LCO routes; deny politely if role mismatch."""
    if "user_id" not in request.session:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/login", status_code=303)
    if (request.session.get("user_type") or "").lower() != "sub_lco":
        # Render shared access-denied page (defined in main.py)
        try:
            from main import _render_access_denied
            return _render_access_denied(request, "sub_lco")
        except Exception:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/login", status_code=303)
    return None


def require_sub_lco_json(request: Request):
    """JSON variant for API endpoints."""
    if "user_id" not in request.session:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    if request.session.get("user_type") != "sub_lco":
        return JSONResponse({"success": False, "message": "Sub-LCO role required"}, status_code=403)
    return None


def _session_sub_lco_id(request: Request) -> Optional[int]:
    sid = request.session.get("sub_lco_db_id")
    try:
        return int(sid) if sid is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------
def get_sub_lco_context(request: Request, db: Session, active_page: str = ""):
    cid = request.session.get("company_id") or ""
    sid = _session_sub_lco_id(request)
    # _S39R5FIX13_ — pull profile_image too so base layout shows it in topbar
    row = db.execute(text(
        "SELECT id, sub_lco_code, username, name, email, mobile, "
        "       commission_percent, status, profile_image "
        "FROM sub_lcos WHERE id=:i"
    ), {"i": sid or 0}).fetchone() if sid else None

    # Company (name + logo) for the branded header.
    from database import Company
    company = db.query(Company).filter(Company.company_id == cid).first()

    # Quick stats for topbar/sidebar badges.
    total_customers = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted')"
    ), {"c": cid, "s": sid or 0}).scalar() or 0

    commission_total = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid or 0}).scalar() or 0

    month_start = date.today().replace(day=1).isoformat()
    commission_mtd = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s AND created_at >= :m"
    ), {"c": cid, "s": sid or 0, "m": month_start}).scalar() or 0

    return {
        # _S39R1_PORTAL_PREFIX_SLCO — keep templates portal-aware.
        "path_prefix": "/sub-lco",
        "request": request,
        "user_type": "sub_lco",
        "user_id": request.session.get("user_id"),
        "user_name": request.session.get("user_name") or (row[3] if row else "Sub-LCO"),
        # Keys expected by base_admin-derived layout (logo, topbar, profile menu)
        "admin_name": (row[3] if row else request.session.get("user_name") or "Sub-LCO"),
        "profile_image": (row[8] if row and len(row) > 8 else None),  # _S39R5FIX13_
        "company_id": cid,
        "company_name": (company.company_name if company else cid),
        "company_logo": (company.logo_path if company and company.logo_path else None),
        "company": company,
        # MFA banner placeholder so base template doesn't error out
        "_s36g_mfa": {"required": False, "enabled": True, "overdue": False,
                       "days_left": None},
        "sub_lco": {
            "id": (row[0] if row else None),
            "code": (row[1] if row else ""),
            "username": (row[2] if row else ""),
            "name": (row[3] if row else ""),
            "email": (row[4] if row else ""),
            "mobile": (row[5] if row else ""),
            "commission_percent": float((row[6] if row else 0) or 0),
            "status": (row[7] if row else ""),
        },
        "stats": {
            "total_customers": int(total_customers),
            "commission_total": float(commission_total),
            "commission_mtd": float(commission_mtd),
        },
        "active_page": active_page,
    }


# ---------------------------------------------------------------------------
# Restriction middleware — sub-LCO can only reach their own namespace.
# ---------------------------------------------------------------------------
class SubLcoRestrictMiddleware(BaseHTTPMiddleware):
    ALLOWED_PREFIXES = (
        "/sub-lco/", "/api/sub-lco/",
        "/static/", "/logout", "/login", "/admin/login",
        "/api/auth/",
    )
    ALLOWED_EXACT = {
        "/api/plans/list",
        "/api/customers/check-username",
    }

    async def dispatch(self, request, call_next):
        try:
            if request.session.get("user_type") == "sub_lco":
                path = request.url.path
                if (not path.startswith(self.ALLOWED_PREFIXES)
                        and path not in self.ALLOWED_EXACT
                        and path != "/"):
                    return JSONResponse(
                        {"success": False,
                         "message": "Access denied for Sub-LCO role."},
                        status_code=403,
                    )
        except Exception:
            pass
        return await call_next(request)


# ---------------------------------------------------------------------------
# Commission hook (called from /api/payments/create)
# ---------------------------------------------------------------------------
def record_commission_for_payment(db: Session, company_id: str, customer,
                                  paying_amount: float, discount: float,
                                  payment_id: int | None = None) -> None:
    """If the customer is owned by a sub-LCO, insert a commission row
    = percent% of (paying_amount). Discounts don't earn commission.
    Fails silently — payment creation must not be blocked by this."""
    try:
        slid = getattr(customer, "sub_lco_id", None)
        if not slid:
            slid = db.execute(text(
                "SELECT sub_lco_id FROM customers WHERE id=:i"
            ), {"i": customer.id}).scalar()
        if not slid:
            return
        row = db.execute(text(
            "SELECT commission_percent FROM sub_lcos WHERE id=:i"
        ), {"i": slid}).fetchone()
        if not row:
            return
        pct = float(row[0] or 0)
        if pct <= 0:
            return
        # __S38T_COMM_PLAN_ONLY__ — commission earns ONLY on the plan
        # portion of the payment. Security-deposit, installation-charges,
        # any discount-credit are FIRST settled by the customer'''s cash
        # before commission applies.
        # 1) sum of cash collected from this customer BEFORE this payment.
        prev_paid = float(db.execute(text(
            "SELECT COALESCE(SUM(amount), 0) FROM payments "
            "WHERE customer_id = :cid AND company_id = :co AND id != :pid"
        ), {"cid": customer.customer_id, "co": company_id,
            "pid": int(payment_id or 0)}).scalar() or 0)
        # __S38U_COMM_TAX_FREE__ — commission earns ONLY on the
        # tax-free plan portion of the payment. Security deposit,
        # installation, ROUTER charges and GST are FIRST settled by
        # the customer's cash before commission applies.
        one_time = (float(getattr(customer, "security_deposit", 0) or 0)
                  + float(getattr(customer, "installation_charges", 0) or 0)
                  + float(getattr(customer, "router_charges", 0) or 0))
        one_time_paid_before = min(prev_paid, one_time)
        one_time_paid_after  = min(prev_paid + float(paying_amount or 0), one_time)
        consumed_for_one_time = max(0.0, one_time_paid_after - one_time_paid_before)
        plan_after_tax = max(0.0, float(paying_amount or 0) - consumed_for_one_time)

        # Strip GST. The ratio is plan-base / plan-with-tax; prefer the
        # customer's own per-month figures (bill_amount = base, monthly_amount
        # = base + tax). Fall back to the plans table when the customer
        # rows are missing values, or default to 1.0 (treat as tax-free).
        mo = float(getattr(customer, "monthly_amount", 0) or 0)
        ba = float(getattr(customer, "bill_amount", 0) or 0)
        if mo > 0 and ba > 0 and ba <= mo + 0.005:
            tax_factor = ba / mo
        else:
            tax_factor = 1.0
            try:
                pid = getattr(customer, "plan_id", None)
                if pid:
                    pr = db.execute(text(
                        "SELECT base_amount, after_tax_amount FROM plans WHERE id=:i"
                    ), {"i": pid}).fetchone()
                    if pr and float(pr[1] or 0) > 0:
                        tax_factor = float(pr[0] or 0) / float(pr[1])
            except Exception:
                pass

        plan_base_only = plan_after_tax * tax_factor
        base = round(plan_base_only, 2)
        if base <= 0:
            return
        amt = round(base * pct / 100.0, 2)
        db.execute(text(
            "INSERT INTO sub_lco_commissions "
            "(company_id, sub_lco_id, customer_id, payment_id, base_amount, "
            " commission_percent, commission_amount, note, created_at) "
            "VALUES (:c, :s, :cid, :pid, :base, :pct, :amt, :note, :now)"
        ), {
            "c": company_id, "s": slid,
            "cid": customer.customer_id, "pid": payment_id,
            "base": base, "pct": pct, "amt": amt,
            "note": "payment collected",
            "now": datetime.utcnow(),
        })
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[sub_lco] commission-hook failed: {e}")


# ===========================================================================
# ADMIN ROUTER — /admin/sub-lcos + /api/admin/sub-lcos/*
# ===========================================================================
def _require_admin_redirect(request: Request):
    if "user_id" not in request.session:
        return RedirectResponse(url="/login", status_code=303)
    if request.session.get("user_type") != "admin":
        return RedirectResponse(url="/login", status_code=303)
    return None


def _require_admin_json(request: Request):
    if "user_id" not in request.session:
        return JSONResponse({"success": False, "message": "Unauthorized"}, status_code=401)
    if request.session.get("user_type") != "admin":
        return JSONResponse({"success": False, "message": "Admin role required"}, status_code=403)
    return None


@admin_router.get("/admin/sub-lcos", response_class=HTMLResponse)
async def admin_sub_lcos_page(request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_redirect(request)
    if gate:
        return gate
    # Re-use admin context from main.py if available
    try:
        from main import get_admin_context
        ctx = get_admin_context(request, db, "sub_lcos")
    except Exception:
        ctx = {"request": request, "active_page": "sub_lcos",
               "user_type": "admin",
               "company_id": request.session.get("company_id"),
               "user_name": request.session.get("user_name")}
    return templates.TemplateResponse("admin_sub_lcos.html", ctx)


@admin_router.get("/api/admin/sub-lcos/list")
async def admin_sub_lcos_list(request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    rows = db.execute(text(
        "SELECT s.id, s.sub_lco_code, s.username, s.name, s.email, s.mobile, "
        "       s.commission_percent, s.status, s.created_at, "
        "  (SELECT COUNT(*) FROM customers c WHERE c.company_id=s.company_id "
        "      AND c.sub_lco_id=s.id AND (c.status IS NULL OR c.status != 'Deleted')) AS cust_count, "
        "  (SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "      WHERE company_id=s.company_id AND sub_lco_id=s.id) AS comm_total "
        "FROM sub_lcos s WHERE s.company_id=:c "
        "ORDER BY s.created_at DESC"
    ), {"c": cid}).fetchall()
    data = []
    for r in rows:
        loc_rows = db.execute(text(
            "SELECT l.id, l.name FROM sub_lco_locations sl "
            "JOIN locations l ON l.id = sl.location_id "
            "WHERE sl.sub_lco_id=:i"
        ), {"i": r[0]}).fetchall()
        data.append({
            "id": r[0], "sub_lco_code": r[1], "username": r[2], "name": r[3],
            "email": r[4] or "", "mobile": r[5] or "",
            "commission_percent": float(r[6] or 0),
            "status": r[7], "created_at": str(r[8] or ""),
            "customers_count": int(r[9] or 0),
            "commission_total": float(r[10] or 0),
            "location_ids": [x[0] for x in loc_rows],
            "location_names": [x[1] for x in loc_rows],
        })
    return {"success": True, "items": data}


@admin_router.get("/api/admin/sub-lcos/{sid}/locations")
async def admin_sub_lcos_locations(sid: int, request: Request,
                                    db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    rows = db.execute(text(
        "SELECT l.id, l.name, l.city, l.state, l.pincode "
        "FROM sub_lco_locations sl JOIN locations l ON l.id = sl.location_id "
        "WHERE sl.sub_lco_id=:i AND l.company_id=:c"
    ), {"i": sid, "c": cid}).fetchall()
    return {"success": True, "items": [{
        "id": r[0], "name": r[1], "city": r[2] or "",
        "state": r[3] or "", "pincode": r[4] or "",
    } for r in rows]}


def _replace_slco_locations(db: Session, cid: str, sid: int, ids: list):
    """Sync sub_lco_locations for a sub-LCO. Silently drops IDs that don't
    belong to the caller's company.
    __S39B__ Also re-syncs employee_locality_assignments for ALL employees
    owned by this Sub-LCO so they automatically inherit the new location set.
    """
    db.execute(text("DELETE FROM sub_lco_locations WHERE sub_lco_id=:s"),
               {"s": sid})
    clean = []
    for x in ids or []:
        try:
            clean.append(int(x))
        except Exception:
            pass
    if clean:
        valid = db.execute(
            text("SELECT id FROM locations WHERE company_id=:c AND id IN :ids")
                .bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)),
            {"c": cid, "ids": clean},
        ).fetchall()
        for row in valid:
            # _S58M_SUBLCO_CREATED_AT_FIX_  explicit created_at
            db.execute(text(
                "INSERT OR IGNORE INTO sub_lco_locations "
                "(sub_lco_id, location_id, created_at) "
                "VALUES (:s, :l, :n)"
            ), {"s": sid, "l": row[0], "n": datetime.utcnow()})
    # __S39B__ resync this Sub-LCO's employees with the new location set
    try:
        from main import _s39emp_resync_all_employees_for_sub_lco as _resync
        _resync(db, cid, int(sid))
    except Exception as _e:
        print(f"[s39b] sub_lco employee resync failed: {_e}")


def _gen_sub_lco_code(db: Session, company_id: str) -> str:
    for _ in range(8):
        code = "SLCO" + "".join(random.choices(string.digits, k=6))
        exists = db.execute(text(
            "SELECT 1 FROM sub_lcos WHERE company_id=:c AND sub_lco_code=:s"
        ), {"c": company_id, "s": code}).fetchone()
        if not exists:
            return code
    raise RuntimeError("Unable to generate unique sub-LCO code")


@admin_router.post("/api/admin/sub-lcos/create")
async def admin_sub_lco_create(request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    name = (data.get("name") or "").strip()
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    email = (data.get("email") or "").strip()
    mobile = (data.get("mobile") or "").strip()
    address = (data.get("address") or "").strip()
    try:
        commission_percent = float(data.get("commission_percent") or 0)
    except Exception:
        commission_percent = 0.0

    if not name or not username or not password:
        return JSONResponse({"success": False,
            "message": "Name, username and password are required."},
            status_code=400)
    if len(password) < 6:
        return JSONResponse({"success": False,
            "message": "Password must be at least 6 characters."},
            status_code=400)
    if commission_percent < 0 or commission_percent > 100:
        return JSONResponse({"success": False,
            "message": "Commission must be between 0 and 100."},
            status_code=400)

    # Uniqueness check on username (scoped to company)
    dup = db.execute(text(
        "SELECT 1 FROM sub_lcos WHERE company_id=:c AND username=:u"
    ), {"c": cid, "u": username}).fetchone()
    if dup:
        return JSONResponse({"success": False,
            "message": "Username already exists for this company."},
            status_code=409)

    code = _gen_sub_lco_code(db, cid)
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = datetime.utcnow()
    res = db.execute(text(
        "INSERT INTO sub_lcos "
        "(company_id, sub_lco_code, username, password_hash, name, email, "
        " mobile, address, commission_percent, status, created_at, updated_at) "
        "VALUES (:c, :code, :u, :p, :n, :e, :m, :a, :pct, 'Active', :now, :now)"
    ), {"c": cid, "code": code, "u": username, "p": pw_hash, "n": name,
        "e": email, "m": mobile, "a": address,
        "pct": commission_percent, "now": now})
    new_sid = res.lastrowid
    # Save location assignments
    loc_ids = data.get("location_ids") or []
    _replace_slco_locations(db, cid, new_sid, loc_ids)
    db.commit()
    return {"success": True, "message": "Sub-LCO created.",
            "sub_lco_code": code, "id": new_sid}


@admin_router.post("/api/admin/sub-lcos/{sid}/update")
async def admin_sub_lco_update(sid: int, request: Request,
                               db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    row = db.execute(text(
        "SELECT id FROM sub_lcos WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not row:
        return JSONResponse({"success": False, "message": "Sub-LCO not found"},
                            status_code=404)
    fields, params = [], {"i": sid, "c": cid, "now": datetime.utcnow()}
    for k in ("name", "email", "mobile", "address", "status"):
        if k in data and data[k] is not None:
            fields.append(f"{k}=:{k}")
            params[k] = (data[k] or "").strip()
    if "commission_percent" in data and data["commission_percent"] is not None:
        try:
            pct = float(data["commission_percent"])
            if pct < 0 or pct > 100:
                return JSONResponse({"success": False,
                    "message": "Commission must be 0–100"},
                    status_code=400)
            fields.append("commission_percent=:commission_percent")
            params["commission_percent"] = pct
        except Exception:
            return JSONResponse({"success": False,
                "message": "Invalid commission_percent"},
                status_code=400)
    if not fields:
        # Still might need to update locations only
        if "location_ids" in data:
            _replace_slco_locations(db, cid, sid, data.get("location_ids") or [])
            db.commit()
            return {"success": True, "message": "Locations updated."}
        return {"success": True, "message": "Nothing to update."}
    fields.append("updated_at=:now")
    db.execute(text(
        f"UPDATE sub_lcos SET {', '.join(fields)} WHERE id=:i AND company_id=:c"
    ), params)
    if "location_ids" in data:
        _replace_slco_locations(db, cid, sid, data.get("location_ids") or [])
    db.commit()
    return {"success": True, "message": "Sub-LCO updated."}


@admin_router.post("/api/admin/sub-lcos/{sid}/reset-password")
async def admin_sub_lco_reset_pw(sid: int, request: Request,
                                 db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    pw = (data.get("password") or "").strip()
    if len(pw) < 6:
        return JSONResponse({"success": False,
            "message": "Password must be at least 6 characters."},
            status_code=400)
    row = db.execute(text(
        "SELECT id FROM sub_lcos WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not row:
        return JSONResponse({"success": False, "message": "Sub-LCO not found"},
                            status_code=404)
    pw_hash = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
    db.execute(text(
        "UPDATE sub_lcos SET password_hash=:p, updated_at=:n "
        "WHERE id=:i AND company_id=:c"
    ), {"p": pw_hash, "n": datetime.utcnow(), "i": sid, "c": cid})
    db.commit()
    return {"success": True, "message": "Password reset."}


@admin_router.post("/api/admin/sub-lcos/{sid}/delete")
async def admin_sub_lco_delete(sid: int, request: Request,
                               db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    # Soft check — don't delete if they still own customers.
    cnt = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted')"
    ), {"c": cid, "s": sid}).scalar() or 0
    if int(cnt) > 0:
        return JSONResponse({"success": False,
            "message": f"Cannot delete: {int(cnt)} customer(s) are assigned. "
                       "Re-assign or delete those customers first."},
            status_code=409)
    # _S39R2_SLCO_SOFT_DELETE — soft-delete so admin can restore
    db.execute(text(
        "UPDATE sub_lcos SET status='Deleted', updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid})
    db.commit()
    return {"success": True, "message": "Sub-LCO deleted (soft)."}



@admin_router.post("/api/admin/sub-lcos/{sid}/restore")
async def admin_sub_lco_restore(sid: int, request: Request,
                                 db: Session = Depends(get_db)):
    """_S39R2_SLCO_SOFT_DELETE — restore a soft-deleted sub-LCO."""
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    res = db.execute(text(
        "UPDATE sub_lcos SET status='Active', updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:i AND company_id=:c AND COALESCE(status,'')='Deleted'"
    ), {"i": sid, "c": cid})
    db.commit()
    if (res.rowcount or 0) == 0:
        return JSONResponse({"success": False,
            "message": "Not found or not in deleted state"}, status_code=404)
    return {"success": True, "message": "Sub-LCO restored."}


@admin_router.get("/admin/deleted-sub-lcos", response_class=HTMLResponse)
async def admin_deleted_sub_lcos_page(request: Request,
                                       db: Session = Depends(get_db)):
    """_S39R2_SLCO_SOFT_DELETE — admin page listing soft-deleted sub-LCOs."""
    from main import require_admin, get_admin_context
    gate = require_admin(request)
    if gate: return gate
    cid = request.session.get("company_id")
    rows = db.execute(text(
        "SELECT id, sub_lco_code, username, name, email, mobile, "
        "       commission_percent, status "
        "FROM sub_lcos WHERE company_id=:c AND status='Deleted' "
        "ORDER BY id DESC"
    ), {"c": cid}).mappings().all()
    rows = [dict(r) for r in rows]
    ctx = get_admin_context(request, db, "deleted_sub_lcos")
    ctx["rows"] = rows
    return templates.TemplateResponse("admin_deleted_sub_lcos.html", ctx)


# ===========================================================================
# SUB-LCO PORTAL ROUTER — /sub-lco/* + /api/sub-lco/*
# ===========================================================================
@portal_router.get("/sub-lco/dashboard", response_class=HTMLResponse)
async def sub_lco_dashboard(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "dashboard")
    cid = ctx["company_id"]
    sid = ctx["sub_lco"]["id"]
    # Extra dashboard stats
    active_cnt = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND LOWER(COALESCE(status,''))='active'"
    ), {"c": cid, "s": sid}).scalar() or 0
    deactive_cnt = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND LOWER(COALESCE(status,'')) IN ('deactive','deactivated','suspended','disabled')"
    ), {"c": cid, "s": sid}).scalar() or 0
    recent = db.execute(text(
        "SELECT customer_id, customer_name, customer_phone, status, end_date "
        "FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted') "
        "ORDER BY id DESC LIMIT 5"
    ), {"c": cid, "s": sid}).fetchall()
    ctx["active_count"] = int(active_cnt)
    ctx["deactive_count"] = int(deactive_cnt)
    ctx["recent_customers"] = [
        {"customer_id": r[0], "customer_name": r[1], "customer_phone": r[2],
         "status": r[3], "end_date": r[4]} for r in recent
    ]
    return templates.TemplateResponse("sub_lco_dashboard.html", ctx)


@portal_router.get("/sub-lco/customers", response_class=HTMLResponse)
async def sub_lco_customers_page(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "customers")
    return templates.TemplateResponse("sub_lco_customers.html", ctx)


@portal_router.get("/api/sub-lco/customers/list")
async def api_sub_lco_customers_list(request: Request,
                                     db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT c.id, c.customer_id, c.customer_name, c.username, c.customer_phone, "
        "       c.customer_email, c.locality, c.status, c.plan_id, c.end_date, "
        "       c.monthly_amount "
        "FROM customers c WHERE c.company_id=:c AND c.sub_lco_id=:s "
        "AND (c.status IS NULL OR c.status != 'Deleted') "
        "ORDER BY c.id DESC"
    ), {"c": cid, "s": sid}).fetchall()
    items = [{
        "id": r[0], "customer_id": r[1], "customer_name": r[2],
        "username": r[3] or "", "customer_phone": r[4] or "",
        "customer_email": r[5] or "", "locality": r[6] or "",
        "status": r[7] or "Active", "plan_id": r[8],
        "end_date": r[9] or "", "monthly_amount": float(r[10] or 0),
    } for r in rows]
    return {"success": True, "items": items}


@portal_router.get("/sub-lco/customers/add", response_class=HTMLResponse)
async def sub_lco_customer_add_page(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "customers")
    # Provide plans + locations lists for the form.
    cid = ctx["company_id"]
    plans = db.execute(text(
        "SELECT id, plan_name, speed, after_tax_amount, service, validity "
        "FROM plans WHERE company_id=:c ORDER BY id DESC"
    ), {"c": cid}).fetchall()
    ctx["plans"] = [{
        "id": p[0], "plan_name": p[1], "speed": p[2] or "",
        "price": float(p[3] or 0), "service": p[4] or "",
        "validity_days": int(p[5] or 30),
    } for p in plans]
    locs = db.execute(text(
        "SELECT id, name, city, state, pincode FROM locations "
        "WHERE company_id=:c AND (status IS NULL OR status='Active') "
        "ORDER BY name"
    ), {"c": cid}).fetchall()
    ctx["locations"] = [{"id": l[0], "name": l[1], "city": l[2] or "",
                         "state": l[3] or "", "pincode": l[4] or ""}
                        for l in locs]
    # _S40zP_dns_  expose tenant's DNS profiles for the form
    try:
        _dnsp = db.execute(text(
            "SELECT id, name FROM dns_profiles WHERE company_id=:c ORDER BY name"
        ), {"c": cid}).fetchall()
        ctx["dns_profiles"] = [{"id": d[0], "name": d[1] or ("Profile #" + str(d[0]))} for d in _dnsp]
    except Exception:
        ctx["dns_profiles"] = []
    return templates.TemplateResponse("sub_lco_customer_add.html", ctx)


@portal_router.post("/api/sub-lco/customers/create")
async def api_sub_lco_customer_create(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    required = ("name", "mobile", "username", "password",
                "service_type", "customer_id")
    for f in required:
        if not (data.get(f) or "").strip():
            return JSONResponse({"success": False,
                "message": f"Missing field: {f}"}, status_code=400)

    # Uniqueness on customer_id AND username within company
    if db.execute(text(
        "SELECT 1 FROM customers WHERE customer_id=:x"
    ), {"x": data["customer_id"]}).fetchone():
        return JSONResponse({"success": False,
            "message": "Customer ID already exists. Regenerate."},
            status_code=409)
    if db.execute(text(
        "SELECT 1 FROM customers WHERE company_id=:c AND username=:u"
    ), {"c": cid, "u": data["username"]}).fetchone():
        return JSONResponse({"success": False,
            "message": "Username already exists in this company."},
            status_code=409)

    def _f(v):
        try:
            return float(str(v).replace("₹", "").replace(",", "").strip())
        except Exception:
            return None

    def _i(v):
        try:
            return int(v)
        except Exception:
            return None

    pw_hash = bcrypt.hashpw(
        data["password"].encode(), bcrypt.gensalt()
    ).decode()

    # End-date calculation (simple: start + period months)
    start = data.get("start_date") or date.today().isoformat()
    period = _i(data.get("period")) or 1
    try:
        sd = datetime.fromisoformat(start).date() if "T" not in start else datetime.fromisoformat(start).date()
    except Exception:
        try:
            sd = datetime.strptime(start, "%Y-%m-%d").date()
        except Exception:
            sd = date.today()
    # naive month-addition
    month = sd.month - 1 + period
    year = sd.year + month // 12
    month = month % 12 + 1
    day = min(sd.day, 28)
    end_d = date(year, month, day).isoformat()

    now = datetime.utcnow()
    db.execute(text("""
        INSERT INTO customers (
            company_id, customer_id, password_hash, registration_type,
            service_type, username, customer_name, customer_email,
            customer_phone, address, locality, city, state, pincode,
            plan_id, dns_profile_id, monthly_amount, auto_renew, customer_type,
            start_date, period, end_date, bill_amount, total_bill_amount,
            payment_mode, received_amount, installation_charges,
            security_deposit, status, sub_lco_id, auth_type,
            created_at, updated_at
        ) VALUES (
            :company_id, :customer_id, :password_hash, :reg,
            :service_type, :username, :name, :email,
            :mobile, :address, :locality, :city, :state, :pincode,
            :plan_id, :dns_profile_id, :monthly_amount, :auto_renew, :customer_type,
            :start_date, :period, :end_date, :bill_amount, :total_bill,
            :payment_mode, :received_amount, :install_charges,
            :sec_deposit, 'Active', :sub_lco_id, :auth_type,
            :now, :now
        )
    """), {
        "company_id": cid,
        "customer_id": data["customer_id"],
        "password_hash": pw_hash,
        "reg": data.get("registration_type", "New Customer"),
        "service_type": data["service_type"],
        "username": data["username"],
        "name": data["name"],
        "email": data.get("email") or None,
        "mobile": data["mobile"],
        "address": data.get("address") or None,
        "locality": data.get("locality") or None,
        "city": data.get("city") or None,
        "state": data.get("state") or None,
        "pincode": data.get("pincode") or None,
        "plan_id": _i(data.get("plan_id")),
        "dns_profile_id": (_i(data.get("dns_profile_id")) or None),
        "monthly_amount": _f(data.get("monthly_amount")),
        "auto_renew": data.get("auto_renew", "Yes"),
        "customer_type": data.get("customer_type", "Postpaid"),
        "start_date": start,
        "period": period,
        "end_date": end_d,
        "bill_amount": _f(data.get("bill_amount")) or _f(data.get("monthly_amount")),
        "total_bill": _f(data.get("total_bill_amount")) or _f(data.get("monthly_amount")),
        "payment_mode": data.get("payment_mode"),
        "received_amount": _f(data.get("received_amount")) or 0,
        "install_charges": _f(data.get("installation_charges")) or 0,
        "sec_deposit": _f(data.get("security_deposit")) or 0,
        "sub_lco_id": sid,
        "auth_type": data.get("auth_type", "pppoe"),
        "now": now,
    })
    db.commit()

    # _S40zM_ Generate first invoice + email welcome receipt (matches admin parity).
    try:
        from services.billing import renew_customer_core
        from datetime import datetime as _dtX
        try:
            sd_dt = _dtX.fromisoformat(start)
        except Exception:
            sd_dt = _dtX.utcnow()
        rres = renew_customer_core(
            company_id=cid,
            customer_id=data["customer_id"],
            start_date=sd_dt,
            period_months=period,
            with_invoice=True,
            db=db,
            source="sub_lco_create",
        )
        if rres.get("success") and rres.get("customer_email") and rres.get("pdf_data"):
            try:
                from main import send_invoice_email
                _eres = await send_invoice_email(
                    rres["invoice_data"],
                    rres["customer_email"],
                    rres["company_data"],
                    rres["pdf_data"],
                    rres.get("customer_type") or "PREPAID",
                )
                if _eres and _eres.get("success"):
                    print(f"[sub_lco] welcome invoice emailed to {rres['customer_email']}")
                else:
                    print(f"[sub_lco] welcome email failed: {_eres}")
            except Exception as _eMail:
                print(f"[sub_lco] welcome email error: {_eMail}")
        elif not rres.get("success"):
            print(f"[sub_lco] initial invoice generation failed: {rres.get('message')}")
    except Exception as _eInv:
        print(f"[sub_lco] post-create invoice/email skipped: {_eInv}")

    return {"success": True, "message": "Customer added.",
            "customer_id": data["customer_id"]}


def _load_owned_customer(db: Session, cid: str, sid: int,
                        customer_id: str):
    """Load a Customer ORM row ONLY if it's owned by the given sub_lco.
    Uses a raw-SQL ownership check first (because sub_lco_id is a raw
    ALTER-added column not in the SQLAlchemy model) and then returns the
    ORM row so callers can use it naturally."""
    from database import Customer
    own = db.execute(text(
        "SELECT id FROM customers "
        "WHERE company_id=:c AND customer_id=:x AND sub_lco_id=:s"
    ), {"c": cid, "x": customer_id, "s": sid}).fetchone()
    if not own:
        return None
    return db.query(Customer).filter(
        Customer.company_id == cid,
        Customer.customer_id == customer_id,
    ).first()


@portal_router.post("/api/sub-lco/customers/{customer_id}/status")
async def api_sub_lco_customer_status(customer_id: str, request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    cust = _load_owned_customer(db, cid, sid, customer_id)
    if not cust:
        return JSONResponse({"success": False,
            "message": "Customer not found in your list."}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    active = data.get("active")
    cust.status = "Active" if active else "Deactive"
    db.commit()
    # Best-effort network enforcement (reuse admin's helper if present)
    try:
        from main import _enforce_user_state
        _enforce_user_state(db, cust)
    except Exception as e:
        print(f"[sub_lco] enforce: {e}")
    return {"success": True, "message": "Status updated.",
            "status": cust.status}


@portal_router.post("/api/sub-lco/customers/{customer_id}/delete")
async def api_sub_lco_customer_delete(customer_id: str, request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    cust = _load_owned_customer(db, cid, sid, customer_id)
    if not cust:
        return JSONResponse({"success": False,
            "message": "Customer not found in your list."}, status_code=404)
    # Soft delete (matches admin flow — /admin/deleted-users restores)
    cust.status = "Deleted"
    db.commit()
    return {"success": True, "message": "Customer moved to deleted list."}


@portal_router.post("/api/sub-lco/customers/{customer_id}/reset-password")
async def api_sub_lco_customer_reset_pw(customer_id: str, request: Request,
                                        db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    cust = _load_owned_customer(db, cid, sid, customer_id)
    if not cust:
        return JSONResponse({"success": False,
            "message": "Customer not found."}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    pw = (data.get("password") or "").strip()
    if len(pw) < 6:
        return JSONResponse({"success": False,
            "message": "Password must be at least 6 characters."},
            status_code=400)
    cust.password_hash = bcrypt.hashpw(pw.encode(),
                                        bcrypt.gensalt()).decode()
    # Also update PPPoE password mirror if present
    if hasattr(cust, "pppoe_password"):
        cust.pppoe_password = pw
    db.commit()
    return {"success": True, "message": "Password reset."}


# ----- Locations (scoped to sub-LCO) -----
@portal_router.get("/sub-lco/locations", response_class=HTMLResponse)
async def sub_lco_locations_page(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "locations")
    return templates.TemplateResponse("sub_lco_locations.html", ctx)


@portal_router.get("/api/sub-lco/locations/list")
async def api_sub_lco_locations_list(request: Request,
                                     db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT id, name, city, state, pincode, status, created_at "
        "FROM locations WHERE company_id=:c AND sub_lco_id=:s "
        "ORDER BY id DESC"
    ), {"c": cid, "s": sid}).fetchall()
    items = [{
        "id": r[0], "name": r[1], "city": r[2] or "",
        "state": r[3] or "", "pincode": r[4] or "",
        "status": r[5] or "Active", "created_at": str(r[6] or ""),
    } for r in rows]
    return {"success": True, "items": items}


@portal_router.post("/api/sub-lco/locations/create")
async def api_sub_lco_location_create(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    try:
        data = dict(await request.form())
    except Exception:
        data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        return JSONResponse({"success": False,
            "message": "Name is required."}, status_code=400)
    now = datetime.utcnow()
    res = db.execute(text(
        "INSERT INTO locations "
        "(company_id, name, city, state, pincode, status, sub_lco_id, "
        " created_at, updated_at) "
        "VALUES (:c, :n, :ci, :st, :pc, :stt, :s, :now, :now)"
    ), {"c": cid, "n": name,
        "ci": (data.get("city") or "").strip(),
        "st": (data.get("state") or "").strip(),
        "pc": (data.get("pincode") or "").strip(),
        "stt": (data.get("status") or "Active").strip(),
        "s": sid, "now": now})
    db.commit()
    return {"success": True, "message": "Location created.",
            "location_id": res.lastrowid}


@portal_router.post("/api/sub-lco/locations/update")
async def api_sub_lco_location_update(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    try:
        data = dict(await request.form())
    except Exception:
        data = await request.json()
    lid = data.get("location_id")
    if not lid:
        return JSONResponse({"success": False,
            "message": "location_id is required."}, status_code=400)
    # ownership check
    own = db.execute(text(
        "SELECT 1 FROM locations WHERE id=:i AND company_id=:c AND sub_lco_id=:s"
    ), {"i": lid, "c": cid, "s": sid}).fetchone()
    if not own:
        return JSONResponse({"success": False,
            "message": "Location not found."}, status_code=404)
    db.execute(text(
        "UPDATE locations SET name=:n, city=:ci, state=:st, pincode=:pc, "
        "       status=:stt, updated_at=:now "
        "WHERE id=:i AND company_id=:c AND sub_lco_id=:s"
    ), {"n": (data.get("name") or "").strip(),
        "ci": (data.get("city") or "").strip(),
        "st": (data.get("state") or "").strip(),
        "pc": (data.get("pincode") or "").strip(),
        "stt": (data.get("status") or "Active").strip(),
        "now": datetime.utcnow(),
        "i": lid, "c": cid, "s": sid})
    db.commit()
    return {"success": True, "message": "Location updated."}


@portal_router.post("/api/sub-lco/locations/delete")
async def api_sub_lco_location_delete(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    try:
        data = dict(await request.form())
    except Exception:
        data = await request.json()
    lid = data.get("location_id")
    if not lid:
        return JSONResponse({"success": False,
            "message": "location_id is required."}, status_code=400)
    db.execute(text(
        "DELETE FROM locations "
        "WHERE id=:i AND company_id=:c AND sub_lco_id=:s"
    ), {"i": lid, "c": cid, "s": sid})
    db.commit()
    return {"success": True, "message": "Location deleted."}


# ----- Commissions ledger -----
@portal_router.get("/sub-lco/commissions", response_class=HTMLResponse)
async def sub_lco_commissions_page(request: Request,
                                   db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "commissions")
    return templates.TemplateResponse("sub_lco_commissions.html", ctx)


@portal_router.get("/api/sub-lco/commissions/list")
async def api_sub_lco_commissions_list(request: Request,
                                       db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT l.id, l.created_at, l.customer_id, c.customer_name, "
        "       l.base_amount, l.commission_percent, l.commission_amount, l.note "
        "FROM sub_lco_commissions l "
        "LEFT JOIN customers c ON c.customer_id = l.customer_id "
        "    AND c.company_id = l.company_id "
        "WHERE l.company_id=:c AND l.sub_lco_id=:s "
        "ORDER BY l.id DESC LIMIT 500"
    ), {"c": cid, "s": sid}).fetchall()
    total = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    items = [{
        "id": r[0], "created_at": str(r[1] or ""),
        "customer_id": r[2], "customer_name": r[3] or "",
        "base_amount": float(r[4] or 0),
        "commission_percent": float(r[5] or 0),
        "commission_amount": float(r[6] or 0),
        "note": r[7] or "",
    } for r in rows]
    return {"success": True, "items": items, "total": float(total)}


# ===========================================================================
# Session 38y.1 — P1 enhancements
#   • Full customer edit for Sub-LCOs
#   • Sub-LCO payment collection
#   • Commission payout tracking (Pending / Settled)
#   • CSV export of commissions
#   • Sub-LCO profile + password change
# ===========================================================================
import csv
import io
from fastapi.responses import StreamingResponse


# ----- Full customer GET + UPDATE (Sub-LCO) -----
_EDITABLE_CUSTOMER_FIELDS = [
    "customer_name", "nickname", "customer_email", "customer_phone", "alt_mobile",
    "service_type", "username", "customer_type", "auto_renew",
    "address", "locality", "city", "state", "pincode",
    "billing_address", "billing_locality", "billing_city", "billing_state", "billing_pincode",
    "gst_invoice_needed", "customer_gst_no", "id_proof", "id_proof_no",
    "installation_date", "caf_no",
    "plan_id", "dns_profile_id", "monthly_amount", "bill_amount", "total_bill_amount",
    "cgst_tax", "sgst_tax", "igst_tax",
    "start_date", "period", "end_date",
    "payment_mode", "security_deposit", "installation_charges", "router_charges",
    "mac_address", "ip_address", "vendor", "modem_no",
    "zone", "node", "distance_from_node",
    "fix_ip_address", "bind_mac_address",
    "auth_type", "static_ip_address", "static_netmask",
    "hotspot_session_timeout", "hotspot_idle_timeout", "allow_devices",
    "do_not_send_sms", "do_not_send_whatsapp", "do_not_send_email",
    "status", "custom_rate_limit",
]

_INT_FIELDS = {"plan_id", "dns_profile_id", "period", "hotspot_session_timeout",
               "hotspot_idle_timeout", "allow_devices"}
_FLOAT_FIELDS = {"monthly_amount", "bill_amount", "total_bill_amount",
                 "cgst_tax", "sgst_tax", "igst_tax",
                 "security_deposit", "installation_charges", "router_charges"}


def _coerce(field, val):
    if val is None or val == "":
        return None if field in _INT_FIELDS or field in _FLOAT_FIELDS else val
    try:
        if field in _INT_FIELDS:
            return int(val)
        if field in _FLOAT_FIELDS:
            return float(str(val).replace("₹", "").replace(",", "").strip())
    except Exception:
        return None
    return val


@portal_router.get("/api/sub-lco/customers/{customer_id}")
async def api_sub_lco_customer_get(customer_id: str, request: Request,
                                    db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    row = db.execute(text(
        "SELECT * FROM customers WHERE company_id=:c AND customer_id=:i AND sub_lco_id=:s"
    ), {"c": cid, "i": customer_id, "s": sid}).mappings().fetchone()
    if not row:
        return JSONResponse({"success": False,
            "message": "Customer not found."}, status_code=404)
    data = {k: v for k, v in row.items()
            if k not in ("password_hash", "caf_pdf")}
    # Serialize datetime → iso
    for k in list(data.keys()):
        if isinstance(data[k], datetime):
            data[k] = data[k].isoformat()
    return {"success": True, "customer": data}


@portal_router.post("/api/sub-lco/customers/{customer_id}/update")
async def api_sub_lco_customer_update(customer_id: str, request: Request,
                                       db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    cust = _load_owned_customer(db, cid, sid, customer_id)
    if not cust:
        return JSONResponse({"success": False,
            "message": "Customer not found."}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    applied = 0
    for f in _EDITABLE_CUSTOMER_FIELDS:
        if f in data:
            v = _coerce(f, data[f])
            try:
                setattr(cust, f, v)
                applied += 1
            except Exception:
                pass
    # Handle optional password change
    new_pw = (data.get("password") or "").strip()
    if new_pw:
        if len(new_pw) < 6:
            return JSONResponse({"success": False,
                "message": "Password must be at least 6 characters."},
                status_code=400)
        cust.password_hash = bcrypt.hashpw(
            new_pw.encode(), bcrypt.gensalt()).decode()
        if hasattr(cust, "pppoe_password"):
            cust.pppoe_password = new_pw
        applied += 1

    cust.updated_at = datetime.utcnow()
    db.commit()
    # Best-effort network enforcement (status may have changed)
    try:
        from main import _enforce_user_state
        _enforce_user_state(db, cust)
    except Exception as e:
        print(f"[sub_lco] post-update enforce: {e}")
    return {"success": True, "message": f"Updated {applied} field(s).",
            "customer_id": customer_id}


@portal_router.get("/sub-lco/customers/{customer_id}/edit", response_class=HTMLResponse)
async def sub_lco_customer_edit_page(customer_id: str, request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "customers")
    cid = ctx["company_id"]
    sid = ctx["sub_lco"]["id"]
    row = db.execute(text(
        "SELECT * FROM customers WHERE company_id=:c AND customer_id=:i AND sub_lco_id=:s"
    ), {"c": cid, "i": customer_id, "s": sid}).mappings().fetchone()
    if not row:
        return RedirectResponse(url="/sub-lco/customers", status_code=303)
    ctx["customer"] = {k: (v if not isinstance(v, datetime) else v.isoformat())
                        for k, v in row.items()
                        if k not in ("password_hash", "caf_pdf")}
    # Plans + locations for selects
    plans = db.execute(text(
        "SELECT id, plan_name, speed, after_tax_amount, service, validity "
        "FROM plans WHERE company_id=:c ORDER BY id DESC"
    ), {"c": cid}).fetchall()
    ctx["plans"] = [{"id": p[0], "plan_name": p[1], "speed": p[2] or "",
                     "price": float(p[3] or 0), "service": p[4] or "",
                     "validity_days": int(p[5] or 30)} for p in plans]
    locs = db.execute(text(
        "SELECT id, name, city, state, pincode FROM locations "
        "WHERE company_id=:c AND (status IS NULL OR status='Active') ORDER BY name"
    ), {"c": cid}).fetchall()
    ctx["locations"] = [{"id": l[0], "name": l[1], "city": l[2] or "",
                         "state": l[3] or "", "pincode": l[4] or ""}
                        for l in locs]
    # _S40zP_dns_  expose tenant's DNS profiles for the form
    try:
        _dnsp = db.execute(text(
            "SELECT id, name FROM dns_profiles WHERE company_id=:c ORDER BY name"
        ), {"c": cid}).fetchall()
        ctx["dns_profiles"] = [{"id": d[0], "name": d[1] or ("Profile #" + str(d[0]))} for d in _dnsp]
    except Exception:
        ctx["dns_profiles"] = []
    return templates.TemplateResponse("sub_lco_customer_edit.html", ctx)


# ----- Sub-LCO payment collection -----
@portal_router.post("/api/sub-lco/customers/{customer_id}/payment")
async def api_sub_lco_collect_payment(customer_id: str, request: Request,
                                       db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    cust = _load_owned_customer(db, cid, sid, customer_id)
    if not cust:
        return JSONResponse({"success": False,
            "message": "Customer not found."}, status_code=404)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())

    def _pf(v):
        try:
            return float(str(v).replace("₹", "").replace(",", "").strip() or 0)
        except Exception:
            return 0.0

    amount = _pf(data.get("amount") or data.get("paying_amount"))
    discount = _pf(data.get("discount") or 0)
    mode = (data.get("payment_mode") or "").strip()
    remarks = (data.get("remarks") or "").strip()
    if amount < 0 or discount < 0 or (amount + discount) <= 0:
        return JSONResponse({"success": False,
            "message": "Amount + discount must be greater than 0."},
            status_code=400)
    if not mode:
        return JSONResponse({"success": False,
            "message": "Payment mode is required."}, status_code=400)

    # Load sub-LCO row so we can set a traceable employee_id string
    slrow = db.execute(text(
        "SELECT sub_lco_code FROM sub_lcos WHERE id=:i"
    ), {"i": sid}).fetchone()
    slco_code = slrow[0] if slrow else str(sid)
    paid_at = datetime.utcnow()

    # Create payment (reuse admin's transaction_no generator)
    from database import Payment, ReceivedTracker
    try:
        from main import generate_transaction_no, compute_customer_balance
    except Exception as _e:
        return JSONResponse({"success": False,
            "message": f"Payment helpers unavailable: {_e}"},
            status_code=500)

    for attempt in range(3):
        try:
            txn = generate_transaction_no(mode, db)
            payment = Payment(
                company_id=cid, customer_id=customer_id,
                employee_id=f"SLCO:{slco_code}",
                amount=amount, discount=discount,
                payment_mode=mode, transaction_no=txn,
                paid_at=paid_at,
                remarks=(remarks or f"Collected by Sub-LCO {slco_code}"),
            )
            db.add(payment)
            db.commit()
            break
        except Exception as _e:
            db.rollback()
            if attempt == 2:
                return JSONResponse({"success": False,
                    "message": f"Failed to record payment: {_e}"},
                    status_code=500)

    # Update received_tracker like admin flow does
    rt = db.query(ReceivedTracker).filter(
        ReceivedTracker.company_id == cid,
        ReceivedTracker.customer_id == customer_id,
    ).first()
    if rt:
        if paid_at >= rt.last_reset_at:
            rt.received_since_reset = (rt.received_since_reset or 0) + (amount + discount)
            rt.updated_at = paid_at
    else:
        rt = ReceivedTracker(company_id=cid, customer_id=customer_id,
                              received_since_reset=(amount + discount),
                              last_reset_at=paid_at, updated_at=paid_at)  # paid_at is UTC ✓  # paid_at is UTC ✓
        db.add(rt)
    db.commit()

    # _S40zM_ Auto-email receipt to customer (best-effort).
    try:
        from main import _send_receipt_email_for_payment
        await _send_receipt_email_for_payment(payment.id, cid, db)
    except Exception as _eRcpt:
        print(f"[sub_lco receipt-email auto] payment#{payment.id}: {_eRcpt}")

    new_balance = compute_customer_balance(customer_id, cid, db)
    # Auto-reactivate if balance cleared
    if new_balance <= 0.01 and (cust.status or "").strip().lower() != "active":
        cust.status = "Active"
        db.commit()
        try:
            from main import _enforce_user_state
            _enforce_user_state(db, cust)
        except Exception:
            pass

    # Record commission
    try:
        record_commission_for_payment(
            db, cid, cust, paying_amount=amount,
            discount=discount, payment_id=payment.id,
        )
    except Exception as e:
        print(f"[sub_lco] commission record after collect failed: {e}")

    return {"success": True, "message": "Payment recorded.",
            "transaction_no": txn, "new_balance": new_balance,
            "status": cust.status}


# ----- CSV export of commissions (sub-LCO) -----
@portal_router.get("/api/sub-lco/commissions/export.csv")
async def api_sub_lco_commissions_export(request: Request,
                                          db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT l.id, l.created_at, l.customer_id, c.customer_name, "
        "       l.base_amount, l.commission_percent, l.commission_amount, "
        "       COALESCE(l.payout_status, 'Pending'), l.settled_at, l.note "
        "FROM sub_lco_commissions l "
        "LEFT JOIN customers c ON c.customer_id=l.customer_id AND c.company_id=l.company_id "
        "WHERE l.company_id=:c AND l.sub_lco_id=:s "
        "ORDER BY l.id DESC"
    ), {"c": cid, "s": sid}).fetchall()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Date", "Customer ID", "Customer Name",
                "Base Amount", "Commission %", "Commission Amount",
                "Status", "Settled At", "Note"])
    for r in rows:
        w.writerow([r[0], str(r[1] or ""), r[2], r[3] or "",
                    f"{float(r[4] or 0):.2f}",
                    f"{float(r[5] or 0):.2f}",
                    f"{float(r[6] or 0):.2f}",
                    r[7] or "Pending", str(r[8] or ""),
                    r[9] or ""])
    buf.seek(0)
    fname = f"commissions_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ----- Sub-LCO profile + password change -----
@portal_router.get("/sub-lco/profile", response_class=HTMLResponse)
async def sub_lco_profile_page(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "profile")
    return templates.TemplateResponse("sub_lco_profile.html", ctx)


@portal_router.post("/api/sub-lco/profile/update")
async def api_sub_lco_profile_update(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    sid = _session_sub_lco_id(request)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    fields, params = [], {"i": sid, "now": datetime.utcnow()}
    for k in ("name", "email", "mobile", "address"):
        if k in data:
            fields.append(f"{k}=:{k}")
            params[k] = (data[k] or "").strip()
    if not fields:
        return {"success": True, "message": "Nothing to update."}
    fields.append("updated_at=:now")
    db.execute(text(
        f"UPDATE sub_lcos SET {', '.join(fields)} WHERE id=:i"
    ), params)
    db.commit()
    # Reflect name change in current session
    if "name" in data:
        request.session["user_name"] = params["name"] or request.session.get("user_name")
    return {"success": True, "message": "Profile updated."}


@portal_router.post("/api/sub-lco/profile/change-password")
async def api_sub_lco_profile_change_pw(request: Request,
                                         db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    sid = _session_sub_lco_id(request)
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    current = (data.get("current_password") or "").strip()
    new_pw = (data.get("new_password") or "").strip()
    if len(new_pw) < 6:
        return JSONResponse({"success": False,
            "message": "New password must be at least 6 characters."},
            status_code=400)
    row = db.execute(text(
        "SELECT password_hash FROM sub_lcos WHERE id=:i"
    ), {"i": sid}).fetchone()
    if not row:
        return JSONResponse({"success": False,
            "message": "Sub-LCO not found."}, status_code=404)
    try:
        ok = bcrypt.checkpw(current.encode(), (row[0] or "").encode())
    except Exception:
        ok = False
    if not ok:
        return JSONResponse({"success": False,
            "message": "Current password is incorrect."}, status_code=400)
    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    db.execute(text(
        "UPDATE sub_lcos SET password_hash=:p, updated_at=:n WHERE id=:i"
    ), {"p": new_hash, "n": datetime.utcnow(), "i": sid})
    db.commit()
    return {"success": True, "message": "Password changed."}


# ----- Commissions list — now exposes payout_status -----
# (The existing /api/sub-lco/commissions/list query only returns the first
#  eight columns; we shadow it here with a richer endpoint that augments the
#  status. Clients should prefer this one for the ledger UI.)
@portal_router.get("/api/sub-lco/commissions/listv2")
async def api_sub_lco_commissions_listv2(request: Request,
                                          db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT l.id, l.created_at, l.customer_id, c.customer_name, "
        "       l.base_amount, l.commission_percent, l.commission_amount, "
        "       l.note, COALESCE(l.payout_status, 'Pending') AS status, "
        "       l.settled_at, l.payout_id "
        "FROM sub_lco_commissions l "
        "LEFT JOIN customers c ON c.customer_id=l.customer_id AND c.company_id=l.company_id "
        "WHERE l.company_id=:c AND l.sub_lco_id=:s "
        "ORDER BY l.id DESC LIMIT 1000"
    ), {"c": cid, "s": sid}).fetchall()
    total = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    paid = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s "
        "AND COALESCE(payout_status,'Pending')='Settled'"
    ), {"c": cid, "s": sid}).scalar() or 0
    pending = float(total or 0) - float(paid or 0)
    return {
        "success": True,
        "items": [{
            "id": r[0], "created_at": str(r[1] or ""),
            "customer_id": r[2], "customer_name": r[3] or "",
            "base_amount": float(r[4] or 0),
            "commission_percent": float(r[5] or 0),
            "commission_amount": float(r[6] or 0),
            "note": r[7] or "",
            "status": r[8] or "Pending",
            "settled_at": str(r[9] or ""),
            "payout_id": r[10],
        } for r in rows],
        "total": float(total or 0),
        "paid": float(paid or 0),
        "pending": pending,
    }


# ----- Admin commission/payout management -----
@admin_router.get("/api/admin/sub-lcos/{sid}/commissions")
async def admin_sub_lco_commissions(sid: int, request: Request,
                                     status: str = "all",
                                     db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    # Ownership check
    own = db.execute(text(
        "SELECT id FROM sub_lcos WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False,
            "message": "Sub-LCO not found."}, status_code=404)

    where = "l.company_id=:c AND l.sub_lco_id=:s"
    params = {"c": cid, "s": sid}
    st = (status or "all").lower()
    if st in ("pending", "settled"):
        where += " AND COALESCE(l.payout_status,'Pending')=:st"
        params["st"] = "Pending" if st == "pending" else "Settled"
    rows = db.execute(text(
        f"SELECT l.id, l.created_at, l.customer_id, c.customer_name, "
        f"       l.base_amount, l.commission_percent, l.commission_amount, "
        f"       COALESCE(l.payout_status,'Pending'), l.payout_id, l.settled_at, l.note "
        f"FROM sub_lco_commissions l "
        f"LEFT JOIN customers c ON c.customer_id=l.customer_id AND c.company_id=l.company_id "
        f"WHERE {where} ORDER BY l.id DESC LIMIT 1000"
    ), params).fetchall()

    total = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    paid = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s "
        "AND COALESCE(payout_status,'Pending')='Settled'"
    ), {"c": cid, "s": sid}).scalar() or 0
    return {
        "success": True,
        "items": [{
            "id": r[0], "created_at": str(r[1] or ""),
            "customer_id": r[2], "customer_name": r[3] or "",
            "base_amount": float(r[4] or 0),
            "commission_percent": float(r[5] or 0),
            "commission_amount": float(r[6] or 0),
            "payout_status": r[7] or "Pending",
            "payout_id": r[8], "settled_at": str(r[9] or ""),
            "note": r[10] or "",
        } for r in rows],
        "total": float(total or 0),
        "paid": float(paid or 0),
        "pending": float(total or 0) - float(paid or 0),
    }


@admin_router.post("/api/admin/sub-lcos/{sid}/payouts")
async def admin_sub_lco_payout_create(sid: int, request: Request,
                                       db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    try:
        data = await request.json()
    except Exception:
        data = dict(await request.form())
    commission_ids = data.get("commission_ids") or []
    reference = (data.get("reference") or "").strip()
    notes = (data.get("notes") or "").strip()
    paid_at_s = (data.get("paid_at") or "").strip()
    # __S38T_MANUAL_PAYOUT__ — operator may override the actual payout
    # amount (less or more than the sum of selected commissions).
    manual_raw = data.get("manual_amount", None)
    try:
        manual_amount = float(manual_raw) if manual_raw not in (None, "", "null") else None
        if manual_amount is not None and manual_amount < 0:
            manual_amount = None
    except Exception:
        manual_amount = None
    try:
        paid_at = datetime.fromisoformat(paid_at_s) if paid_at_s else datetime.utcnow()
    except Exception:
        paid_at = datetime.utcnow()

    if not isinstance(commission_ids, list) or not commission_ids:
        return JSONResponse({"success": False,
            "message": "Select at least one commission."},
            status_code=400)

    # Ownership + pending check
    rows = db.execute(text(
        "SELECT id, commission_amount FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s AND id IN :ids "
        "AND COALESCE(payout_status,'Pending')='Pending'"
    ).bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)),
        {"c": cid, "s": sid, "ids": [int(x) for x in commission_ids]}
    ).fetchall()
    if not rows:
        return JSONResponse({"success": False,
            "message": "No matching pending commissions found."},
            status_code=404)
    selected_total = sum(float(r[1] or 0) for r in rows)
    # Final cash amount paid = manual override if provided, else the sum.
    amount = manual_amount if manual_amount is not None else selected_total
    # Auto-append balance annotation to notes so the receipt is self-explanatory.
    if manual_amount is not None and abs(manual_amount - selected_total) > 0.005:
        diff = manual_amount - selected_total
        if diff < 0:
            adj = f"[Manual: paid Rs.{manual_amount:.2f} of Rs.{selected_total:.2f} — short by Rs.{abs(diff):.2f}]"
        else:
            adj = f"[Manual: paid Rs.{manual_amount:.2f} of Rs.{selected_total:.2f} — advance Rs.{diff:.2f}]"
        notes = (notes + " " + adj).strip() if notes else adj
    pres = db.execute(text(
        "INSERT INTO sub_lco_payouts (company_id, sub_lco_id, amount, "
        " reference, notes, paid_at, created_by, created_at) "
        "VALUES (:c, :s, :a, :ref, :n, :pa, :cb, :now)"
    ), {"c": cid, "s": sid, "a": amount, "ref": reference,
        "n": notes, "pa": paid_at,
        "cb": request.session.get("user_id") or "admin",
        "now": datetime.utcnow()})
    payout_id = pres.lastrowid
    db.execute(text(
        "UPDATE sub_lco_commissions SET payout_status='Settled', "
        " payout_id=:pid, settled_at=:sa "
        "WHERE company_id=:c AND sub_lco_id=:s AND id IN :ids "
        "AND COALESCE(payout_status,'Pending')='Pending'"
    ).bindparams(__import__("sqlalchemy").bindparam("ids", expanding=True)),
        {"c": cid, "s": sid, "pid": payout_id, "sa": paid_at,
         "ids": [int(x) for x in commission_ids]}
    )
    db.commit()
    return {"success": True, "message": f"Marked {len(rows)} commission(s) as settled.",
            "payout_id": payout_id, "amount": amount}


@admin_router.get("/api/admin/sub-lcos/{sid}/payouts")
async def admin_sub_lco_payouts_list(sid: int, request: Request,
                                      db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    rows = db.execute(text(
        "SELECT id, amount, reference, notes, paid_at, created_by, created_at "
        "FROM sub_lco_payouts WHERE company_id=:c AND sub_lco_id=:s "
        "ORDER BY id DESC LIMIT 200"
    ), {"c": cid, "s": sid}).fetchall()
    return {"success": True, "items": [{
        "id": r[0], "amount": float(r[1] or 0),
        "reference": r[2] or "", "notes": r[3] or "",
        "paid_at": str(r[4] or ""),
        "created_by": r[5] or "",
        "created_at": str(r[6] or ""),
    } for r in rows]}


# ===========================================================================
# Session 38y.2 — Menu expansion: placeholder routes + Deleted Users + Plans
# ===========================================================================
def _placeholder(active_page: str, label: str, subtitle: str = ""):
    async def _handler(request: Request, db: Session = Depends(get_db)):
        gate = require_sub_lco(request)
        if gate:
            return gate
        ctx = get_sub_lco_context(request, db, active_page)
        ctx["page_label"] = label
        ctx["page_subtitle"] = subtitle
        return templates.TemplateResponse("sub_lco_placeholder.html", ctx)
    return _handler


# --- Plans (read-only list of company plans) -----------------------------
@portal_router.get("/sub-lco/plans", response_class=HTMLResponse)
async def sub_lco_plans_page(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "plans")
    cid = ctx["company_id"]
    rows = db.execute(text(
        "SELECT id, plan_name, speed, after_tax_amount, service, validity, "
        "       COALESCE(fup_limit_gb, 0), COALESCE(fup_enabled, 0) "
        "FROM plans WHERE company_id=:c ORDER BY id DESC"
    ), {"c": cid}).fetchall()
    ctx["plans"] = [{
        "id": r[0], "plan_name": r[1], "speed": r[2] or "",
        "price": float(r[3] or 0), "service": r[4] or "",
        "validity": int(r[5] or 30),
        "data_limit": (f"{float(r[6])} GB (FUP)" if int(r[7] or 0) and float(r[6] or 0) > 0 else "Unlimited"),
        "fup": float(r[6] or 0),
    } for r in rows]
    return templates.TemplateResponse("sub_lco_plans.html", ctx)


# --- Deleted Users (scoped to this sub-LCO) ------------------------------
@portal_router.get("/sub-lco/deleted-users", response_class=HTMLResponse)
async def sub_lco_deleted_page(request: Request,
                               db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = get_sub_lco_context(request, db, "deleted_users")
    return templates.TemplateResponse("sub_lco_deleted_users.html", ctx)


@portal_router.get("/api/sub-lco/deleted-users/list")
async def api_sub_lco_deleted_list(request: Request,
                                    db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    rows = db.execute(text(
        "SELECT customer_id, customer_name, username, customer_phone, "
        "       locality, updated_at "
        "FROM customers WHERE company_id=:c AND sub_lco_id=:s AND status='Deleted' "
        "ORDER BY id DESC"
    ), {"c": cid, "s": sid}).fetchall()
    return {"success": True, "items": [{
        "customer_id": r[0], "customer_name": r[1], "username": r[2] or "",
        "customer_phone": r[3] or "", "locality": r[4] or "",
        "deleted_at": str(r[5] or ""),
    } for r in rows]}


@portal_router.post("/api/sub-lco/deleted-users/{customer_id}/restore")
async def api_sub_lco_deleted_restore(customer_id: str, request: Request,
                                       db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    own = db.execute(text(
        "SELECT 1 FROM customers WHERE company_id=:c AND customer_id=:x "
        "AND sub_lco_id=:s AND status='Deleted'"
    ), {"c": cid, "x": customer_id, "s": sid}).fetchone()
    if not own:
        return JSONResponse({"success": False,
            "message": "Customer not found."}, status_code=404)
    db.execute(text(
        "UPDATE customers SET status='Active', updated_at=:n "
        "WHERE company_id=:c AND customer_id=:x AND sub_lco_id=:s"
    ), {"n": datetime.utcnow(), "c": cid, "x": customer_id, "s": sid})
    db.commit()
    return {"success": True, "message": "Customer restored."}


# --- Placeholder menu links ---------------------------------------------
portal_router.add_api_route(
    "/sub-lco/customer-distribution",
    _placeholder("customer_distribution", "Customer Distribution",
                 "Map view of customers by locality"),
    methods=["GET"], response_class=HTMLResponse,
)


# __S39EMP__ Sub-LCO Employee Management & Tracking parity ---------------
@portal_router.get("/sub-lco/employees", response_class=HTMLResponse)
async def sub_lco_employees(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_employees.html", ctx)


@portal_router.get("/sub-lco/add-employee", response_class=HTMLResponse)
async def sub_lco_add_employee(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_add_employee.html", ctx)


@portal_router.get("/sub-lco/edit-employee", response_class=HTMLResponse)
async def sub_lco_edit_employee(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "employees")
    return templates.TemplateResponse("admin_edit_employee.html", ctx)


@portal_router.get("/sub-lco/track-employee", response_class=HTMLResponse)
async def sub_lco_track_employee(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "track_employee")
    return templates.TemplateResponse("admin_track_employees_google.html", ctx)



# _S39R2_SLCO_DEL_EMP — sub-LCO can see + restore their own deleted employees.
@portal_router.get("/sub-lco/deleted-employees", response_class=HTMLResponse)
async def sub_lco_deleted_employees(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    # _S39R5FIX12_ — column is employee_name, not full_name
    rows = db.execute(text(
        "SELECT id, employee_code, employee_name AS full_name, email, mobile "
        "  FROM employees "
        " WHERE company_id=:c AND sub_lco_id=:s AND is_deleted=true "
        " ORDER BY id DESC"
    ), {"c": cid, "s": sid or 0}).mappings().all()
    ctx = _slco_admin_context(request, db, "deleted_employees")
    ctx["rows"] = [dict(r) for r in rows]
    return templates.TemplateResponse("admin_deleted_employees.html", ctx)

portal_router.add_api_route(
    "/sub-lco/activity-log",
    _placeholder("activity_log", "Activity Log",
                 "Audit trail of actions under your account"),
    methods=["GET"], response_class=HTMLResponse,
)
portal_router.add_api_route(
    "/sub-lco/billing",
    _placeholder("billing", "Billing",
                 "Invoices, addon bills, and revenue"),
    methods=["GET"], response_class=HTMLResponse,
)


# Session 38y.5 — wired placeholder routes (Reports / Support / Complaints /
# Connection Request) now render the real admin templates inside the sub-lco
# shell. Data auto-scoped via the SQLAlchemy event listeners + /api whitelist.

@portal_router.get("/sub-lco/reports", response_class=HTMLResponse)
async def sub_lco_reports(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "reports")
    return templates.TemplateResponse("admin_reports.html", ctx)


@portal_router.get("/sub-lco/support", response_class=HTMLResponse)
async def sub_lco_support(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "support")
    return templates.TemplateResponse("admin_support.html", ctx)


@portal_router.get("/sub-lco/complaints", response_class=HTMLResponse)
async def sub_lco_complaints(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "complaints")
    return templates.TemplateResponse("admin_complaints.html", ctx)


@portal_router.get("/sub-lco/connection-request", response_class=HTMLResponse)
async def sub_lco_connection_request(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "connection_request")
    return templates.TemplateResponse("admin_connection_request.html", ctx)


# ===========================================================================
# Session 38y.3 — Admin-parity pages for Sub-LCO
#
#   Strategy: don't duplicate the 6000+ lines of admin_users.html /
#   add_customer.html / admin_online_users.html. Instead:
#     1. Convert those templates to dynamic extends
#        ({% extends base_template|default("base_admin.html") %}).
#     2. Render them from /sub-lco/* routes with base_template pointing to
#        base_sub_lco.html.
#     3. Allow the sub-lco role to call the admin APIs those templates hit.
#     4. Auto-filter every ORM SELECT on Customer / Location by sub_lco_id
#        via a before_compile event listener keyed off a contextvar that
#        middleware populates from the session.
#     5. Auto-stamp sub_lco_id on newly-inserted Customer / Location rows
#        via a before_flush listener.
# ===========================================================================
from contextvars import ContextVar
from sqlalchemy import event
from sqlalchemy.orm import Query

_SUB_LCO_CTX: ContextVar[int | None] = ContextVar("sub_lco_scope", default=None)


def attach_scope_events():
    """Attach SQLAlchemy event listeners that transparently scope Customer /
    Location queries to the current sub-LCO and auto-tag new inserts.

    Uses `do_orm_execute` so we inject the filter at execute-time, avoiding
    the `before_compile` "LIMIT/OFFSET already applied" pitfall.
    `sub_lco_id` is a raw-ALTER column (not on the ORM model), so we express
    the filter as text-SQL referring to the table name."""
    from database import Customer, Location, SessionLocal
    from sqlalchemy.sql import text as _text
    from sqlalchemy.orm import Session as OrmSession
    # Optional models — guard for DBs that don't have them
    try:
        from database import Complaint
    except Exception:
        Complaint = None
    try:
        from database import Payment
    except Exception:
        Payment = None
    try:
        from database import Invoice
    except Exception:
        Invoice = None
    try:
        from database import ReceivedTracker
    except Exception:
        ReceivedTracker = None

    @event.listens_for(OrmSession, "do_orm_execute")
    def _on_execute(orm_execute_state):
        sid = _SUB_LCO_CTX.get()
        if sid is None:
            return
        if not orm_execute_state.is_select:
            return
        try:
            ents = orm_execute_state.all_mappers
        except Exception:
            ents = []
        targets = []
        cust_subq = (
            "customer_id IN (SELECT customer_id FROM customers "
            "WHERE sub_lco_id = {sid})"
        )
        for m in ents:
            if m.class_ is Customer:
                targets.append("customers.sub_lco_id = {sid}")
            elif m.class_ is Location:
                targets.append(
                    "locations.id IN (SELECT location_id FROM sub_lco_locations "
                    "WHERE sub_lco_id = {sid})"
                )
            elif Complaint is not None and m.class_ is Complaint:
                targets.append("complaints." + cust_subq)
            elif Payment is not None and m.class_ is Payment:
                targets.append("payments." + cust_subq)
            elif Invoice is not None and m.class_ is Invoice:
                targets.append("invoices." + cust_subq)
            elif ReceivedTracker is not None and m.class_ is ReceivedTracker:
                targets.append("received_tracker." + cust_subq)
        if not targets:
            return
        try:
            stmt = orm_execute_state.statement
            for expr in targets:
                stmt = stmt.where(_text(expr.format(sid=int(sid))))
            orm_execute_state.statement = stmt
        except Exception as e:
            print(f"[sub_lco] scope filter failed: {e}")

    @event.listens_for(Customer, "before_insert", propagate=True)
    def _stamp_customer(mapper, connection, target):
        sid = _SUB_LCO_CTX.get()
        if sid is None:
            return
        target.__dict__["_pending_sub_lco_id"] = int(sid)

    @event.listens_for(Customer, "after_insert", propagate=True)
    def _after_customer_insert(mapper, connection, target):
        sid = target.__dict__.pop("_pending_sub_lco_id", None)
        if sid is None:
            return
        try:
            connection.execute(
                _text("UPDATE customers SET sub_lco_id = :s WHERE id = :i"),
                {"s": int(sid), "i": target.id},
            )
        except Exception as e:
            print(f"[sub_lco] post-insert stamp failed: {e}")

    @event.listens_for(Location, "before_insert", propagate=True)
    def _stamp_location(mapper, connection, target):
        sid = _SUB_LCO_CTX.get()
        if sid is None:
            return
        target.__dict__["_pending_sub_lco_id"] = int(sid)

    @event.listens_for(Location, "after_insert", propagate=True)
    def _after_location_insert(mapper, connection, target):
        sid = target.__dict__.pop("_pending_sub_lco_id", None)
        if sid is None:
            return
        try:
            connection.execute(
                _text("UPDATE locations SET sub_lco_id = :s WHERE id = :i"),
                {"s": int(sid), "i": target.id},
            )
        except Exception as e:
            print(f"[sub_lco] post-insert stamp (loc) failed: {e}")


class SubLcoScopeMiddleware(BaseHTTPMiddleware):
    """Populate the contextvar from the session so query events can scope."""
    async def dispatch(self, request, call_next):
        token = None
        try:
            ut = request.scope.get("session", {}).get("user_type")
            if ut == "sub_lco":
                sid = request.scope.get("session", {}).get("sub_lco_db_id")
                if sid:
                    token = _SUB_LCO_CTX.set(int(sid))
        except Exception:
            pass
        try:
            return await call_next(request)
        finally:
            if token is not None:
                try:
                    _SUB_LCO_CTX.reset(token)
                except Exception:
                    pass


# --- Admin-parity pages -----------------------------------------------------
def _slco_admin_context(request: Request, db: Session, active_page: str):
    """Compose a Jinja context that satisfies BOTH the admin template
    (needs company_id, company_name, company_logo, admin_name, profile_image,
    active_page, etc.) AND the sub-lco shell (needs sub_lco, stats, user_type)."""
    # Admin templates rely on a lot of context-dependent variables that
    # get_admin_context would normally populate. Reuse it when possible.
    try:
        from main import get_admin_context
        # get_admin_context inspects session.user_type; temporarily flip it
        # so it computes as though we're an admin, then revert.
        sess = request.scope.get("session") or {}
        real_ut = sess.get("user_type")
        sess["user_type"] = "admin"
        try:
            ctx = get_admin_context(request, db, active_page)
        finally:
            sess["user_type"] = real_ut
    except Exception:
        ctx = {"request": request, "active_page": active_page}
    # Overlay sub-lco identity for the base_sub_lco.html shell
    slco_ctx = get_sub_lco_context(request, db, active_page)
    ctx.update(slco_ctx)
    ctx["base_template"] = "base_sub_lco.html"
    ctx["path_prefix"] = "/sub-lco"
    return ctx


async def _delegate_to_admin_handler(handler_name: str, request: Request,
                                     db: Session, active_page: str,
                                     template_name: str):
    """Call an admin-side HTML handler with session.user_type temporarily
    flipped to 'admin' (so require_admin passes and get_admin_context runs
    correctly), then rebuild a TemplateResponse that swaps base_template
    and path_prefix for the sub-LCO shell."""
    sess = request.scope.get("session") or {}
    real_ut = sess.get("user_type")
    sid = sess.get("sub_lco_db_id")
    # Ensure auto-scope ctxvar is set for the nested query
    try:
        if sid:
            _SUB_LCO_CTX.set(int(sid))
    except Exception:
        pass
    sess["user_type"] = "admin"
    try:
        import main as _mainmod
        handler = getattr(_mainmod, handler_name)
        response = await handler(request, db)
    finally:
        sess["user_type"] = real_ut
    # Rebuild TemplateResponse with sub-LCO shell overlay
    if hasattr(response, "context"):
        ctx = dict(response.context)
        ctx.update(get_sub_lco_context(request, db, active_page))
        ctx["base_template"] = "base_sub_lco.html"
        ctx["path_prefix"] = "/sub-lco"
        ctx["active_page"] = active_page
        return templates.TemplateResponse(template_name, ctx)
    return response


@portal_router.get("/sub-lco/users", response_class=HTMLResponse)
async def sub_lco_users_admin_parity(request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    return await _delegate_to_admin_handler(
        "admin_users", request, db, "users", "admin_users.html"
    )


@portal_router.get("/sub-lco/add-customer", response_class=HTMLResponse)
async def sub_lco_add_customer_admin_parity(request: Request,
                                             db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "users")
    return templates.TemplateResponse("add_customer.html", ctx)


@portal_router.get("/sub-lco/expenses", response_class=HTMLResponse)
async def sub_lco_expenses(request: Request, db: Session = Depends(get_db)):
    """_S39R5B_SCOPED_PAGES — Sub-LCO sees / adds only their own expenses."""
    gate = require_sub_lco(request)
    if gate:
        return gate
    return await _delegate_to_admin_handler(
        "admin_expenses", request, db, "expenses", "admin_expenses.html"
    )


@portal_router.get("/sub-lco/online-users", response_class=HTMLResponse)
async def sub_lco_online_users_admin_parity(request: Request,
                                              db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    # __S38T_SUBLCO_ONLINE_SYNC__ — same auto-sync the admin
    # /admin/online-users page does, so PPPoE + static-IP customers
    # show up live without the operator pressing "Sync from NAS".
    try:
        from radius_network import (NasDevice, OnlineUser,
                                     _sync_static_ip_online_for_company)
        import routeros_provision as rp
        from datetime import datetime as _dt
        company_id = request.session.get("company_id")
        nas_rows = (db.query(NasDevice)
                      .filter(NasDevice.company_id == company_id,
                              NasDevice.status == "Active").all())
        seen = set()
        # PPPoE auto-sync (same logic as page_online_users in radius_network).
        for nas in nas_rows:
            try:
                with rp.RouterOSClient(nas, dry_run=False) as rc:
                    for row in rc.list_pppoe_active():
                        uname = (row.get("name") or "").strip()
                        if not uname:
                            continue
                        seen.add(uname)
                        ou = (db.query(OnlineUser)
                                .filter(OnlineUser.company_id == company_id,
                                        OnlineUser.username == uname).first())
                        if not ou:
                            ou = OnlineUser(company_id=company_id,
                                             username=uname,
                                             started_at=_dt.utcnow())
                            db.add(ou)
                        ou.ip_address = row.get("address") or ""
                        ou.nas_ip = nas.ip_address
                        ou.framed_protocol = "PPPoE"
                        ou.status = "Online"
                        ou.updated_at = _dt.utcnow()
            except Exception:
                continue
        # Mark not-seen PPPoE as Offline (don'''t touch Static rows here —
        # the helper handles them).
        for ou in (db.query(OnlineUser)
                     .filter(OnlineUser.company_id == company_id,
                             OnlineUser.framed_protocol == "PPPoE",
                             OnlineUser.status == "Online").all()):
            if ou.username not in seen:
                ou.status = "Offline"
        try:
            db.commit()
        except Exception:
            db.rollback()
        # Static-IP refresh.
        try:
            _sync_static_ip_online_for_company(db, company_id, nas_rows=nas_rows)
        except Exception as _e:
            print(f"[warn] sub-lco static-ip sync: {_e}")
    except Exception as _e:
        print(f"[warn] sub-lco online auto-sync: {_e}")
    ctx = _slco_admin_context(request, db, "online_users")
    # __S38T_SUBLCO_ONLINE_FETCH__ — admin_online_users.html iterates the
    # `online_users` ctx variable. The ContextVar listener does not auto-
    # scope OnlineUser (it has no sub_lco_id column), so we explicitly
    # join customers on username and restrict by sub_lco_id.
    try:
        from radius_network import OnlineUser as _OU
        from sqlalchemy import text as _text_ou
        sid = request.session.get("sub_lco_db_id") or 0
        company_id = request.session.get("company_id")
        rows = []
        from datetime import datetime as _dt_ou
        def _parse_dt(v):
            if not v: return None
            if hasattr(v, "isoformat"): return v
            try:
                return _dt_ou.fromisoformat(str(v).replace(" ", "T").split(".")[0])
            except Exception:
                return None
        for r in db.execute(_text_ou("""
                SELECT ou.username, ou.ip_address, ou.nas_ip, ou.session_id,
                       ou.framed_protocol, ou.started_at, ou.uptime_seconds,
                       ou.bytes_in, ou.bytes_out, ou.status, ou.updated_at,
                       ou.mac_address, ou.nas_port_id,
                       COALESCE(c.customer_name, ou.customer_name, '') AS customer_name,
                       c.customer_id AS customer_id,
                       c.id          AS customer_pk,
                       n.id          AS nas_id,
                       COALESCE(n.name, ou.nas_ip, '') AS nas_name
                  FROM online_users ou
                  LEFT JOIN customers c
                       ON c.username = ou.username
                      AND c.company_id = ou.company_id
                  LEFT JOIN nas_devices n
                       ON n.ip_address = ou.nas_ip
                      AND n.company_id = ou.company_id
                 WHERE ou.company_id = :cid
                   AND ou.status = 'Online'
                   AND COALESCE(c.status,'') NOT IN ('Expired','expired','Disabled','disabled','Deactivated','deactivated','Suspended','suspended','Parked','parked','Cancelled','cancelled','Canceled','canceled','Terminated','terminated','Deleted','deleted')
                   AND NOT EXISTS (
                       SELECT 1 FROM ip_pools p
                        WHERE p.company_id = ou.company_id
                          AND LOWER(COALESCE(p.role,'')) = 'parking'
                          AND ou.ip_address LIKE substr(p.network,1,instr(p.network,'/')-1) || '%'
                   )
                   AND c.sub_lco_id = :sid
              ORDER BY ou.framed_protocol, ou.started_at DESC
            """), {"cid": company_id, "sid": int(sid)}).mappings():
            d = dict(r)
            d["started_at"] = _parse_dt(d.get("started_at"))
            d["updated_at"] = _parse_dt(d.get("updated_at"))
            rows.append(d)
        # admin_online_users.html accesses `u.username`, `u.ip_address`, etc.
        # We feed it a list of types.SimpleNamespace so attribute access works.
        import types as _types
        ctx["online_users"] = [_types.SimpleNamespace(**r) for r in rows]
    except Exception as _fe:
        print(f"[warn] sub-lco online fetch: {_fe}")
        ctx.setdefault("online_users", [])
    return templates.TemplateResponse("admin_online_users.html", ctx)


# ___S38T_SUBSCRIBER_DETAIL___ -------------------------------------------
# Sub-LCO subscriber detail page — delegates to admin_subscriber_detail
# with user_type temporarily flipped so the admin handler runs end-to-end.
# ContextVar auto-scoping ensures the customer must belong to this Sub-LCO.
@portal_router.get("/sub-lco/subscribers/{customer_pk}", response_class=HTMLResponse)
async def sub_lco_subscriber_detail(customer_pk: int, request: Request,
                                      db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    sess = request.scope.get("session") or {}
    real_ut = sess.get("user_type")
    sid = sess.get("sub_lco_db_id")
    try:
        if sid:
            _SUB_LCO_CTX.set(int(sid))
    except Exception:
        pass
    sess["user_type"] = "admin"
    try:
        import main as _mm
        response = await _mm.admin_subscriber_detail(customer_pk, request, db)
    finally:
        sess["user_type"] = real_ut
    if hasattr(response, "context"):
        ctx = dict(response.context)
        ctx.update(get_sub_lco_context(request, db, "users"))
        ctx["base_template"] = "base_sub_lco.html"
        ctx["path_prefix"] = "/sub-lco"
        return templates.TemplateResponse(response.template.name, ctx)
    return response


# _S39R5FIX12_ — sub-LCO subscriber USAGE pages (current + previous month)
@portal_router.get("/sub-lco/subscribers/{customer_pk}/usage/current",
                   response_class=HTMLResponse)
async def sub_lco_subscriber_usage_current(customer_pk: int, request: Request,
                                            db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    sess = request.scope.get("session") or {}
    real_ut = sess.get("user_type")
    sid = sess.get("sub_lco_db_id")
    try:
        if sid:
            _SUB_LCO_CTX.set(int(sid))
    except Exception:
        pass
    sess["user_type"] = "admin"
    try:
        import main as _mm
        response = await _mm.admin_subscriber_usage_current(customer_pk, request, db)
    finally:
        sess["user_type"] = real_ut
    if hasattr(response, "context"):
        ctx = dict(response.context)
        ctx.update(get_sub_lco_context(request, db, "users"))
        ctx["base_template"] = "base_sub_lco.html"
        ctx["path_prefix"] = "/sub-lco"
        ctx["viewer_role"] = "sub_lco"
        return templates.TemplateResponse(response.template.name, ctx)
    return response


@portal_router.get("/sub-lco/subscribers/{customer_pk}/usage/previous",
                   response_class=HTMLResponse)
async def sub_lco_subscriber_usage_previous(customer_pk: int, request: Request,
                                             db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    sess = request.scope.get("session") or {}
    real_ut = sess.get("user_type")
    sid = sess.get("sub_lco_db_id")
    try:
        if sid:
            _SUB_LCO_CTX.set(int(sid))
    except Exception:
        pass
    sess["user_type"] = "admin"
    try:
        import main as _mm
        response = await _mm.admin_subscriber_usage_previous(customer_pk, request, db)
    finally:
        sess["user_type"] = real_ut
    if hasattr(response, "context"):
        ctx = dict(response.context)
        ctx.update(get_sub_lco_context(request, db, "users"))
        ctx["base_template"] = "base_sub_lco.html"
        ctx["path_prefix"] = "/sub-lco"
        ctx["viewer_role"] = "sub_lco"
        return templates.TemplateResponse(response.template.name, ctx)
    return response


# ===========================================================================
# Session 38y.4 — Billing & Finance parity (payment history / send invoice /
# sent invoices) + filtered add-customer locality dropdown
# ===========================================================================

@portal_router.get("/sub-lco/payment-history", response_class=HTMLResponse)
async def sub_lco_payment_history(request: Request,
                                    db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "payment_history")
    return templates.TemplateResponse("admin_transactions.html", ctx)


@portal_router.get("/sub-lco/send-invoice", response_class=HTMLResponse)
async def sub_lco_send_invoice(request: Request,
                                db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "send_invoice")
    return templates.TemplateResponse("admin_send_manual_invoice.html", ctx)


@portal_router.get("/sub-lco/sent-invoices", response_class=HTMLResponse)
async def sub_lco_sent_invoices(request: Request,
                                  db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    ctx = _slco_admin_context(request, db, "sent_invoices")
    return templates.TemplateResponse("admin_send_invoices.html", ctx)


# Locality dropdown helper — add-customer form reads this to populate the
# Locality <select>. Reuses /api/locations/list which is already auto-scoped
# by the SQLAlchemy event listener (Location filter now uses sub_lco_locations).

# === BEGIN payout receipt routes (auto) ===
from fastapi.responses import HTMLResponse as _HTMLResponseRcpt

def _fmt_ist_dt(val):
    """Convert a stored UTC-naive datetime to IST DD-MM-YYYY hh:mm AM/PM."""
    from datetime import datetime, timedelta, timezone
    if not val:
        return ""
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val.replace("Z", "").replace(" ", "T"))
        except Exception:
            return val[:16]
    if val.tzinfo is None:
        val = val.replace(tzinfo=timezone.utc)
    ist = val.astimezone(timezone(timedelta(hours=5, minutes=30)))
    return ist.strftime("%d-%m-%Y %I:%M %p")


def _render_payout_receipt(db, cid, sid, pid):
    """Render a printable HTML receipt for one payout. Returns (html, status)."""
    payout = db.execute(text(
        "SELECT id, amount, reference, notes, paid_at, created_by, created_at "
        "FROM sub_lco_payouts WHERE id=:p AND company_id=:c AND sub_lco_id=:s"
    ), {"p": pid, "c": cid, "s": sid}).fetchone()
    if not payout:
        return ("<h3 style='color:#b91c1c;text-align:center;margin-top:60px'>Receipt not found.</h3>", 404)
    slco = db.execute(text(
        "SELECT sub_lco_code, name, username, email, mobile, address, commission_percent "
        "FROM sub_lcos WHERE id=:s AND company_id=:c"
    ), {"s": sid, "c": cid}).fetchone()
    items = db.execute(text(
        "SELECT l.id, l.created_at, l.customer_id, c.customer_name, "
        "       l.base_amount, l.commission_percent, l.commission_amount, l.note "
        "FROM sub_lco_commissions l "
        "LEFT JOIN customers c ON c.customer_id=l.customer_id AND c.company_id=l.company_id "
        "WHERE l.payout_id=:p AND l.company_id=:c AND l.sub_lco_id=:s "
        "ORDER BY l.id"
    ), {"p": pid, "c": cid, "s": sid}).fetchall()
    company_row = db.execute(text(
        "SELECT company_name, gst_number, company_email, company_phone, company_email, company_address "
        "FROM companies WHERE company_id=:c LIMIT 1"
    ), {"c": cid}).fetchone()
    if company_row:
        co_name, co_gst, co_contact, co_mobile, co_email, co_addr = company_row
    else:
        co_name = co_gst = co_contact = co_mobile = co_email = co_addr = ""

    rows_html = []
    sum_comm = 0.0
    sum_base = 0.0
    for r in items:
        sum_comm += float(r[6] or 0); sum_base += float(r[4] or 0)
        rows_html.append(
            f"<tr><td>{_fmt_ist_dt(r[1])}</td>"
            f"<td><code>{r[2] or ''}</code></td>"
            f"<td>{(r[3] or '')}</td>"
            f"<td style='text-align:right'>&#8377; {float(r[4] or 0):,.2f}</td>"
            f"<td style='text-align:right'>{float(r[5] or 0):.2f}%</td>"
            f"<td style='text-align:right'><b>&#8377; {float(r[6] or 0):,.2f}</b></td>"
            f"<td>{(r[7] or '')}</td></tr>"
        )
    if not rows_html:
        rows_html.append("<tr><td colspan='7' style='text-align:center;color:#64748b'>No commissions linked to this payout.</td></tr>")

    diff = float(payout[1] or 0) - sum_comm
    diff_html = ""
    # Only render the short/advance message when the operator EXPLICITLY
    # used the manual-amount override (notes carry the `[Manual:` marker).
    # For routine payouts, the diff is just stale data and should be
    # suppressed — see __S38U_RECEIPT_DIFF__.
    notes_str = (payout[3] or "")
    if abs(diff) > 0.005 and "[Manual:" in notes_str:
        if diff < 0:
            diff_html = f"<div style='color:#b45309'>Short paid by &#8377; {abs(diff):,.2f}</div>"
        else:
            diff_html = f"<div style='color:#15803d'>Advance paid &#8377; {diff:,.2f}</div>"

    html = f"""<!doctype html>
<html><head><meta charset='utf-8'>
<title>Payout Receipt #{payout[0]} — {(slco[0] if slco else '')}</title>
<style>
  *{{box-sizing:border-box}}
  body{{font-family: 'Segoe UI', Arial, sans-serif; color:#0f172a; margin:0; padding:32px; background:#f8fafc}}
  .receipt{{max-width:860px; margin:0 auto; background:#fff; border:1px solid #e2e8f0; padding:36px; border-radius:8px}}
  .head{{display:flex; justify-content:space-between; align-items:flex-start; padding-bottom:18px; border-bottom:2px solid #1e40af}}
  .head h1{{margin:0; color:#1e40af; font-size:22px}}
  .head .sub{{font-size:12px; color:#475569; margin-top:4px; line-height:1.5}}
  .meta{{display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:22px 0}}
  .meta .card{{background:#f1f5f9; padding:14px 16px; border-radius:6px; font-size:13px}}
  .meta .card b{{display:block; font-size:11px; color:#64748b; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:6px}}
  table.lines{{width:100%; border-collapse:collapse; margin-top:8px; font-size:12.5px}}
  table.lines th{{background:#1e293b; color:#fff; padding:8px; text-align:left}}
  table.lines td{{padding:8px; border-bottom:1px solid #e2e8f0}}
  .totals{{margin-top:18px; display:flex; justify-content:flex-end}}
  .totals table{{font-size:13px; min-width:300px}}
  .totals td{{padding:6px 12px}}
  .totals .grand td{{background:#1e40af; color:#fff; font-weight:700; font-size:15px}}
  .footer{{margin-top:30px; font-size:11px; color:#64748b; text-align:center; border-top:1px dashed #cbd5e1; padding-top:16px}}
  .actions{{text-align:center; margin:18px 0 4px; }}
  .actions button{{background:#1e40af; color:#fff; border:0; padding:9px 22px; border-radius:6px; cursor:pointer; font-size:13px; margin:0 4px}}
  @media print {{ body{{background:#fff;padding:0}} .receipt{{border:0;padding:14px}} .actions{{display:none}} }}
</style>
</head><body>
<div class='actions'>
  <button onclick='window.print()' data-testid='print-receipt-btn'>Print / Save as PDF</button>
  <button onclick='window.close()' style='background:#475569'>Close</button>
</div>
<div class='receipt' data-testid='payout-receipt'>
  <div class='head'>
    <div>
      <h1>{co_name or 'Commission Payout Receipt'}</h1>
      <div class='sub'>{(co_addr or '')}<br>
        {('GSTIN: ' + co_gst) if co_gst else ''} {('• ' + co_mobile) if co_mobile else ''} {('• ' + co_email) if co_email else ''}
      </div>
    </div>
    <div style='text-align:right'>
      <div style='font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:1px'>Payout Receipt</div>
      <div style='font-size:18px;font-weight:700;color:#0f172a'>#PO-{payout[0]:06d}</div>
      <div style='font-size:12px;color:#475569;margin-top:4px'>Issued: {_fmt_ist_dt(payout[6])}</div>
    </div>
  </div>

  <div class='meta'>
    <div class='card'>
      <b>Paid To (Sub-LCO)</b>
      {(slco[1] if slco else '')}<br>
      <code>{(slco[0] if slco else '')}</code> · {(slco[2] if slco else '')}<br>
      {(slco[4] or '') if slco else ''} {('• ' + slco[3]) if slco and slco[3] else ''}<br>
      {(slco[5] or '') if slco else ''}
    </div>
    <div class='card'>
      <b>Payout Details</b>
      Amount Paid: <b>&#8377; {float(payout[1] or 0):,.2f}</b><br>
      Paid On: {_fmt_ist_dt(payout[4])}<br>
      Reference: {payout[2] or '—'}<br>
      Created By: {payout[5] or '—'}
      {('<br>Notes: ' + payout[3]) if payout[3] else ''}
    </div>
  </div>

  <h3 style='margin:18px 0 8px;font-size:15px;color:#1e40af'>Settled Commissions</h3>
  <table class='lines'>
    <thead><tr><th>Date</th><th>Customer ID</th><th>Customer</th><th style='text-align:right'>Base</th><th style='text-align:right'>%</th><th style='text-align:right'>Commission</th><th>Note</th></tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
  </table>

  <div class='totals'>
    <table>
      <tr><td>Total Base Revenue</td><td style='text-align:right'>&#8377; {sum_base:,.2f}</td></tr>
      <tr><td>Sum of Commissions</td><td style='text-align:right'>&#8377; {sum_comm:,.2f}</td></tr>
      <tr class='grand'><td>Amount Paid</td><td style='text-align:right'>&#8377; {float(payout[1] or 0):,.2f}</td></tr>
    </table>
  </div>
  {diff_html}

  <div class='footer'>
    This is a system-generated receipt. {(co_name or 'Operator')} confirms commission settlement to the above Sub-LCO partner.
  </div>
</div>
</body></html>"""
    return (html, 200)


@portal_router.get("/api/sub-lco/payouts/{pid}/receipt", response_class=_HTMLResponseRcpt)
async def slco_payout_receipt(pid: int, request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    html, status = _render_payout_receipt(db, cid, sid, pid)
    return _HTMLResponseRcpt(html, status_code=status)


@admin_router.get("/api/admin/sub-lcos/{sid}/payouts/{pid}/receipt", response_class=_HTMLResponseRcpt)
async def admin_payout_receipt(sid: int, pid: int, request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_redirect(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    html, status = _render_payout_receipt(db, cid, sid, pid)
    return _HTMLResponseRcpt(html, status_code=status)
# === END payout receipt routes (auto) ===

# === BEGIN sub-lco detail-profile routes (auto) ===
from collections import OrderedDict as _ODict_DP

@admin_router.get("/admin/sub-lcos/{sid}/details", response_class=HTMLResponse)
async def admin_sub_lco_details_page(sid: int, request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_redirect(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    row = db.execute(text(
        "SELECT id, sub_lco_code, name, username, email, mobile, address, "
        "       commission_percent, status, created_at "
        "FROM sub_lcos WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not row:
        return HTMLResponse(
            "<h3 style='text-align:center;margin-top:80px'>Sub-LCO not found.</h3>",
            status_code=404)
    try:
        from main import get_admin_context
        ctx = get_admin_context(request, db, "sub_lcos")
    except Exception:
        ctx = {"request": request, "active_page": "sub_lcos",
               "user_type": "admin",
               "company_id": cid,
               "user_name": request.session.get("user_name")}
    ctx["sub_lco"] = {
        "id": row[0], "code": row[1], "name": row[2], "username": row[3],
        "email": row[4] or "", "mobile": row[5] or "", "address": row[6] or "",
        "commission_percent": float(row[7] or 0),
        "status": row[8], "created_at": str(row[9] or ""),
    }
    return templates.TemplateResponse("admin_sub_lco_details.html", ctx)


@admin_router.get("/api/admin/sub-lcos/{sid}/overview")
async def admin_sub_lco_overview(sid: int, request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    own = db.execute(text(
        "SELECT id, sub_lco_code, name FROM sub_lcos WHERE id=:i AND company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False, "message": "Sub-LCO not found."}, status_code=404)

    total_users = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted')"
    ), {"c": cid, "s": sid}).scalar() or 0
    active_users = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted') "
        "AND COALESCE(end_date,'') >= :today"
    ), {"c": cid, "s": sid, "today": datetime.utcnow().strftime("%Y-%m-%d")}).scalar() or 0
    deleted_users = db.execute(text(
        "SELECT COUNT(*) FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND status='Deleted'"
    ), {"c": cid, "s": sid}).scalar() or 0
    # Plan-only revenue (mirrors commission base — excludes deposit /
    # installation / router_charges / GST). Source of truth is
    # sub_lco_commissions.base_amount (already computed correctly).
    total_revenue = db.execute(text(
        "SELECT COALESCE(SUM(base_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    total_commission = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    paid_commission = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s AND COALESCE(payout_status,'Pending')='Settled'"
    ), {"c": cid, "s": sid}).scalar() or 0
    pending_commission = float(total_commission or 0) - float(paid_commission or 0)

    # Monthly breakdown (last 12 months) — revenue, commission, new users
    # Plan-only revenue per month (from sub_lco_commissions.base_amount).
    rev_rows = db.execute(text(
        "SELECT strftime('%Y-%m', created_at) AS mo, "
        "       COALESCE(SUM(base_amount),0) AS amt, COUNT(*) AS cnt "
        "FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s "
        "GROUP BY mo ORDER BY mo DESC LIMIT 12"
    ), {"c": cid, "s": sid}).fetchall()
    com_rows = db.execute(text(
        "SELECT strftime('%Y-%m', created_at) AS mo, "
        "       COALESCE(SUM(commission_amount),0) AS amt "
        "FROM sub_lco_commissions WHERE company_id=:c AND sub_lco_id=:s "
        "GROUP BY mo ORDER BY mo DESC LIMIT 12"
    ), {"c": cid, "s": sid}).fetchall()
    new_user_rows = db.execute(text(
        "SELECT strftime('%Y-%m', COALESCE(created_at, start_date)) AS mo, COUNT(*) AS cnt "
        "FROM customers WHERE company_id=:c AND sub_lco_id=:s "
        "AND (status IS NULL OR status != 'Deleted') "
        "GROUP BY mo ORDER BY mo DESC LIMIT 12"
    ), {"c": cid, "s": sid}).fetchall()

    monthly = _ODict_DP()
    for mo, amt, cnt in rev_rows:
        if not mo: continue
        monthly.setdefault(mo, {"revenue": 0.0, "payments": 0, "commission": 0.0, "new_users": 0})
        monthly[mo]["revenue"] = float(amt or 0); monthly[mo]["payments"] = int(cnt or 0)
    for mo, amt in com_rows:
        if not mo: continue
        monthly.setdefault(mo, {"revenue": 0.0, "payments": 0, "commission": 0.0, "new_users": 0})
        monthly[mo]["commission"] = float(amt or 0)
    for mo, cnt in new_user_rows:
        if not mo: continue
        monthly.setdefault(mo, {"revenue": 0.0, "payments": 0, "commission": 0.0, "new_users": 0})
        monthly[mo]["new_users"] = int(cnt or 0)
    monthly_list = sorted(
        [{"month": m, **v} for m, v in monthly.items()],
        key=lambda x: x["month"], reverse=True,
    )

    return {
        "success": True,
        "sub_lco": {"id": own[0], "code": own[1], "name": own[2]},
        "stats": {
            "total_users": int(total_users),
            "active_users": int(active_users),
            "deleted_users": int(deleted_users),
            "total_revenue": float(total_revenue or 0),
            "total_commission": float(total_commission or 0),
            "paid_commission": float(paid_commission or 0),
            "pending_commission": pending_commission,
        },
        "monthly": monthly_list,
    }


@admin_router.get("/api/admin/sub-lcos/{sid}/invoices")
async def admin_sub_lco_invoices(sid: int, request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    own = db.execute(text("SELECT id FROM sub_lcos WHERE id=:i AND company_id=:c"),
                     {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False, "message": "Sub-LCO not found."}, status_code=404)
    rows = db.execute(text(
        "SELECT i.id, i.invoice_no, i.issue_date, i.due_date, i.start_date, i.end_date, "
        "       i.plan_name, i.total_amount, COALESCE(i.status,'Pending'), "
        "       i.customer_id, c.customer_name "
        "FROM invoices i "
        "JOIN customers c ON c.customer_id=i.customer_id AND c.company_id=i.company_id "
        "WHERE i.company_id=:co AND c.sub_lco_id=:s "
        "ORDER BY i.id DESC LIMIT 1000"
    ), {"co": cid, "s": sid}).fetchall()
    return {"success": True, "items": [{
        "id": r[0], "invoice_no": r[1] or "", "issue_date": r[2] or "",
        "due_date": r[3] or "", "start_date": r[4] or "", "end_date": r[5] or "",
        "plan_name": r[6] or "", "total_amount": float(r[7] or 0),
        "status": r[8] or "Pending", "customer_id": r[9] or "",
        "customer_name": r[10] or "",
    } for r in rows]}


@admin_router.get("/api/admin/sub-lcos/{sid}/customer-payments")
async def admin_sub_lco_customer_payments(sid: int, request: Request, db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    own = db.execute(text("SELECT id FROM sub_lcos WHERE id=:i AND company_id=:c"),
                     {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False, "message": "Sub-LCO not found."}, status_code=404)
    rows = db.execute(text(
        "SELECT p.id, p.paid_at, p.customer_id, c.customer_name, p.amount, p.discount, "
        "       p.payment_mode, p.transaction_no, p.remarks, p.employee_id, p.created_at, "
        "       e.employee_name, s.name AS slco_name, a.admin_name "
        "FROM payments p "
        "JOIN customers c ON c.customer_id=p.customer_id AND c.company_id=p.company_id "
        "LEFT JOIN employees e ON e.employee_code=p.employee_id AND e.company_id=p.company_id "
        "LEFT JOIN sub_lcos s ON s.sub_lco_code=p.employee_id AND s.company_id=p.company_id "
        "LEFT JOIN admins a ON a.admin_id=p.employee_id AND a.company_id=p.company_id "
        "WHERE p.company_id=:co AND c.sub_lco_id=:s "
        "ORDER BY p.id DESC LIMIT 1000"
    ), {"co": cid, "s": sid}).fetchall()

    items = []
    for r in rows:
        actor_id   = (r[9]  or "").strip()
        emp_name   = (r[11] or "").strip()
        slco_name  = (r[12] or "").strip()
        admin_name = (r[13] or "").strip()
        # Resolution order: Sub-LCO -> Employee -> Admin -> bare code.
        if slco_name:
            added_by = f"Sub-LCO · {slco_name} ({actor_id})"
        elif emp_name:
            added_by = f"Employee · {emp_name} ({actor_id})"
        elif admin_name:
            added_by = f"Admin · {admin_name} ({actor_id})"
        elif actor_id:
            # Unknown code — at least prefix it so the operator sees something.
            prefix = ("Sub-LCO" if actor_id.upper().startswith("SLCO")
                      else "Admin" if actor_id.upper().startswith("ADMIN")
                      else "User")
            added_by = f"{prefix} · {actor_id}"
        else:
            added_by = "System"
        items.append({
            "id": r[0], "paid_at": str(r[1] or ""),
            "customer_id": r[2] or "", "customer_name": r[3] or "",
            "amount": float(r[4] or 0), "discount": float(r[5] or 0),
            "payment_mode": r[6] or "", "transaction_no": r[7] or "",
            "remarks": r[8] or "", "added_by": added_by,
            "created_at": str(r[10] or ""),
        })
    return {"success": True, "items": items}


@admin_router.post("/api/admin/sub-lcos/{sid}/commissions/{cmid}/email")
async def admin_sub_lco_commission_email(sid: int, cmid: int, request: Request,
                                          db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    own = db.execute(text(
        "SELECT s.email, s.name, s.sub_lco_code FROM sub_lcos s "
        "WHERE s.id=:i AND s.company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False, "message": "Sub-LCO not found."}, status_code=404)
    if not (own[0] or "").strip():
        return JSONResponse({"success": False,
            "message": "No email on file for this Sub-LCO."}, status_code=400)
    com = db.execute(text(
        "SELECT id, customer_id, base_amount, commission_percent, commission_amount, "
        "       COALESCE(payout_status,'Pending'), payout_id, settled_at, created_at "
        "FROM sub_lco_commissions WHERE id=:i AND company_id=:c AND sub_lco_id=:s"
    ), {"i": cmid, "c": cid, "s": sid}).fetchone()
    if not com:
        return JSONResponse({"success": False, "message": "Commission entry not found."},
                            status_code=404)

    # Build link if settled
    link = ""
    if com[6]:
        host = str(request.base_url).rstrip("/")
        link = f"{host}/api/admin/sub-lcos/{sid}/payouts/{com[6]}/receipt"
    subject = f"Commission #{com[0]} — {own[2]}"
    body = (
        f"Hi {own[1]},\n\n"
        f"Commission Entry: #{com[0]}\n"
        f"Customer: {com[1]}\n"
        f"Base Amount: Rs.{float(com[2] or 0):,.2f}\n"
        f"Rate: {float(com[3] or 0):.2f}%\n"
        f"Commission: Rs.{float(com[4] or 0):,.2f}\n"
        f"Status: {com[5]}\n"
        f"{('Settled On: ' + str(com[7])) if com[7] else ''}\n"
        f"{('Receipt: ' + link) if link else ''}\n\n"
        f"Regards,\nBilling Team"
    )
    try:
        import smtplib, os
        from email.mime.text import MIMEText
        host = os.getenv("SMTP_HOST", "smtp.hostinger.com")
        port = int(os.getenv("SMTP_PORT", "465"))
        user = os.getenv("SMTP_USER", "billing@ispbilling.in")
        pw   = os.getenv("SMTP_PASSWORD", "Login@121212")
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = f"ISP Billing <{user}>"
        msg["To"] = own[0]
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            srv = smtplib.SMTP(host, port, timeout=15)
            srv.starttls()
        srv.login(user, pw); srv.send_message(msg); srv.quit()
        return {"success": True, "message": f"Email sent to {own[0]}"}
    except Exception as e:
        return JSONResponse({"success": False,
            "message": f"Email failed: {e}"}, status_code=500)


@admin_router.post("/api/admin/sub-lcos/{sid}/commissions/{cmid}/whatsapp")
async def admin_sub_lco_commission_whatsapp(sid: int, cmid: int, request: Request,
                                             db: Session = Depends(get_db)):
    gate = _require_admin_json(request)
    if gate:
        return gate
    # __s56l_wa_gate__ — respect SuperAdmin per-company WhatsApp flag
    cid = request.session.get("company_id")
    try:
        from database import Company as _CoGate
        _g = db.query(_CoGate).filter(_CoGate.company_id == cid).first()
        if not _g or not getattr(_g, "enable_whatsapp_api", 0):
            return JSONResponse({"success": False,
                "feature_disabled": True,
                "message": "WhatsApp feature is disabled for your account. "
                           "Ask the Superadmin to enable it."}, status_code=403)
    except Exception:
        pass
    own = db.execute(text(
        "SELECT s.mobile, s.name, s.sub_lco_code FROM sub_lcos s "
        "WHERE s.id=:i AND s.company_id=:c"
    ), {"i": sid, "c": cid}).fetchone()
    if not own:
        return JSONResponse({"success": False, "message": "Sub-LCO not found."},
                            status_code=404)
    if not (own[0] or "").strip():
        return JSONResponse({"success": False,
            "message": "No mobile on file for this Sub-LCO."}, status_code=400)
    com = db.execute(text(
        "SELECT id, customer_id, base_amount, commission_percent, commission_amount, "
        "       COALESCE(payout_status,'Pending'), payout_id, settled_at "
        "FROM sub_lco_commissions WHERE id=:i AND company_id=:c AND sub_lco_id=:s"
    ), {"i": cmid, "c": cid, "s": sid}).fetchone()
    if not com:
        return JSONResponse({"success": False, "message": "Commission entry not found."},
                            status_code=404)

    link = ""
    if com[6]:
        host = str(request.base_url).rstrip("/")
        link = f"{host}/api/admin/sub-lcos/{sid}/payouts/{com[6]}/receipt"
    body = (
        f"Hi {own[1]},\n\n"
        f"Commission #{com[0]} — Customer {com[1]}\n"
        f"Base: Rs.{float(com[2] or 0):,.2f}  •  {float(com[3] or 0):.2f}%\n"
        f"Commission: Rs.{float(com[4] or 0):,.2f}\n"
        f"Status: {com[5]}\n"
        f"{('Settled On: ' + str(com[7])) if com[7] else ''}\n"
        f"{link}"
    ).strip()

    try:
        # __MSG91_SUBLCO_COMMISSION__
        from msg91_whatsapp import send_sublco_commission_payout, normalise_phone
        to_ = normalise_phone(own[0])
        if not to_:
            return JSONResponse({"success": False,
                "message": "Sub-LCO mobile is not a valid phone number."},
                status_code=400)
        # company address lookup
        _cname = "AUTO ISP BILLING"; _caddr = ""
        try:
            from database import Company as _Co
            _crow = db.query(_Co).filter(_Co.company_id == company_id).first()
            if _crow:
                _cname = _crow.company_name or _cname
                _caddr = _crow.company_address or ""
        except Exception: pass
        res = send_sublco_commission_payout(
            phone=to_,
            sublco_name=str(own[1] or "Sub-LCO"),
            customer_name=str(com[1] or "Customer"),
            base_amount=f"{float(com[2] or 0):.2f}",
            commission_pct=f"{float(com[3] or 0):.2f}",
            commission_amount=f"{float(com[4] or 0):.2f}",
            status=str(com[5] or "Pending"),
            receipt_url=link,
            company_name=_cname,
            company_address=_caddr,
            company_id=company_id,
        )
        if isinstance(res, dict) and res.get("success"):
            return {"success": True, "message": f"WhatsApp sent to {own[0]}",
                    "sid": res.get("sid")}
        return JSONResponse({"success": False,
            "message": f"WhatsApp send failed: {(res or {}).get('error') or res}"},
            status_code=500)
    except Exception as e:
        return JSONResponse({"success": False,
            "message": f"WhatsApp failed: {e}"}, status_code=500)
# === END sub-lco detail-profile routes (auto) ===

# === BEGIN sub-lco dashboard stats (auto) ===
def _slco_emp_ids(db, cid, sid):
    """__S43ZG__ employee PKs whose sub_lco_id = sid (so SLCO sees their adds too)."""
    try:
        rows = db.execute(text(
            "SELECT id FROM employees WHERE company_id=:c AND sub_lco_id=:s"
        ), {"c": cid, "s": int(sid)}).fetchall()
        return [int(r[0]) for r in rows if r[0] is not None]
    except Exception:
        return []


def _slco_scope_sql_args(db, cid, sid, alias="c"):
    """__S43ZG__ SQL fragment: customers under this sub-LCO OR added by its
    employees. Returns ("<sql>", {bindname: value, ...}).
    """
    emp_ids = _slco_emp_ids(db, cid, sid)
    sub_lco_pk = int(sid)
    if not emp_ids:
        return f"({alias}.sub_lco_id = :__s_sid)", {"__s_sid": sub_lco_pk}
    placeholders = ",".join(f":__s_eid_{i}" for i in range(len(emp_ids)))
    sql = (f"({alias}.sub_lco_id = :__s_sid "
           f"  OR {alias}.created_by_employee_id IN ({placeholders}))")
    args = {"__s_sid": sub_lco_pk}
    for i, e in enumerate(emp_ids):
        args[f"__s_eid_{i}"] = e
    return sql, args


@portal_router.get("/api/sub-lco/dashboard/stats")
async def api_sub_lco_dashboard_stats(request: Request, db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    from datetime import datetime as _dt_d
    mo_start = _dt_d.utcnow().strftime("%Y-%m-01")

    # __S43ZG__ scope = own + own-employees' adds
    scope_sql, scope_args = _slco_scope_sql_args(db, cid, sid, alias="c")

    def _scoped(extra_sql, **xargs):
        sql = (f"SELECT COUNT(*) FROM customers c WHERE c.company_id=:c AND {scope_sql} "
               f"AND (c.status IS NULL OR c.status != 'Deleted') {extra_sql}")
        args = {"c": cid, **scope_args, **xargs}
        return int(db.execute(text(sql), args).scalar() or 0)

    total_customers = _scoped("")
    active_count = _scoped("AND LOWER(COALESCE(c.status,''))='active'")
    deactive_count = _scoped(
        "AND LOWER(COALESCE(c.status,'')) IN ('deactive','deactivated','suspended','disabled')")
    # __S43ZG__ NEW tiles
    from datetime import datetime as _dt_zg
    today_str_zg = _dt_zg.utcnow().strftime("%Y-%m-%d")
    expired_count = _scoped("AND c.end_date IS NOT NULL AND c.end_date <> '' AND c.end_date < :td "
                            "AND LOWER(COALESCE(c.status,'')) != 'deleted'",
                            td=today_str_zg)
    # Currently online: usernames in online_users with status='Online'
    online_now = int(db.execute(text(
        f"SELECT COUNT(*) FROM customers c "
        f" JOIN online_users ou ON ou.username=c.username AND ou.company_id=c.company_id "
        f"WHERE c.company_id=:c AND {scope_sql} AND ou.status='Online' "
        f"  AND LOWER(COALESCE(c.status,'')) NOT IN ("
        f"    'expired','disabled','deactivated','deactive','suspended',"
        f"    'parked','cancelled','canceled','terminated','deleted') "
        f"  AND NOT EXISTS (SELECT 1 FROM ip_pools p "
        f"                  WHERE p.company_id=ou.company_id "
        f"                    AND LOWER(COALESCE(p.role,''))='parking' "
        f"                    AND ou.ip_address LIKE substr(p.network,1,instr(p.network,'/')-1) || '%')"
    ), {"c": cid, **scope_args}).scalar() or 0)
    # Total dues: sum of (invoiced - paid) per scoped customer
    # Use ledger from invoices+payments aggregates
    total_dues = float(db.execute(text(
        f"SELECT COALESCE(SUM(amt),0) FROM ("
        f"  SELECT (COALESCE(inv.iv_total,0) - COALESCE(py.pd_total,0)) AS amt"
        f"    FROM customers c "
        f"    LEFT JOIN (SELECT customer_id, SUM(total_amount) AS iv_total FROM invoices "
        f"               WHERE company_id=:c GROUP BY customer_id) inv "
        f"      ON inv.customer_id=c.customer_id "
        f"    LEFT JOIN (SELECT customer_id, SUM(amount) AS pd_total FROM payments "
        f"               WHERE company_id=:c GROUP BY customer_id) py "
        f"      ON py.customer_id=c.customer_id "
        f"   WHERE c.company_id=:c AND {scope_sql} "
        f"     AND (c.status IS NULL OR c.status != 'Deleted')"
        f") d WHERE amt > 0"
    ), {"c": cid, **scope_args}).scalar() or 0)

    revenue_total = db.execute(text(
        "SELECT COALESCE(SUM(base_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    revenue_mtd = db.execute(text(
        "SELECT COALESCE(SUM(base_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s AND created_at >= :m"
    ), {"c": cid, "s": sid, "m": mo_start}).scalar() or 0
    commission_total = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s"
    ), {"c": cid, "s": sid}).scalar() or 0
    commission_mtd = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s AND created_at >= :m"
    ), {"c": cid, "s": sid, "m": mo_start}).scalar() or 0
    commission_paid = db.execute(text(
        "SELECT COALESCE(SUM(commission_amount),0) FROM sub_lco_commissions "
        "WHERE company_id=:c AND sub_lco_id=:s "
        "AND COALESCE(payout_status,'Pending')='Settled'"
    ), {"c": cid, "s": sid}).scalar() or 0
    commission_pending = float(commission_total or 0) - float(commission_paid or 0)

    recent_payments = db.execute(text(
        "SELECT p.paid_at, p.customer_id, c.customer_name, p.amount, p.payment_mode "
        "FROM payments p "
        "JOIN customers c ON c.customer_id=p.customer_id AND c.company_id=p.company_id "
        f"WHERE p.company_id=:co AND {scope_sql} "
        "ORDER BY p.id DESC LIMIT 8"
    ), {"co": cid, **scope_args}).fetchall()

    return {
        "success": True,
        "stats": {
            "total_customers": int(total_customers),
            "active_count": int(active_count),
            "deactive_count": int(deactive_count),
            "revenue_total": float(revenue_total or 0),
            "revenue_mtd": float(revenue_mtd or 0),
            "commission_total": float(commission_total or 0),
            "commission_mtd": float(commission_mtd or 0),
            "commission_pending": float(commission_pending or 0),
            # __S43ZG__ new tiles
            "online_now": int(online_now),
            "total_dues": float(total_dues),
            "expired_count": int(expired_count),
            # __S43ZH__ critical alerts + open complaints
            "critical_alerts": int(db.execute(text(
                "SELECT COUNT(*) FROM olt_alerts "
                " WHERE company_id=:c AND COALESCE(acked,0)=0 "
                "   AND LOWER(COALESCE(level,'')) IN ('critical','severe','high')"
            ), {"c": cid}).scalar() or 0),
            "open_complaints": int(db.execute(text(
                f"SELECT COUNT(*) FROM complaints cp "
                f" JOIN customers c ON c.customer_id=cp.customer_id AND c.company_id=cp.company_id "
                f" WHERE cp.company_id=:c "
                f"   AND COALESCE(cp.status,'') NOT IN ('Resolved','resolved','Closed','closed') "
                f"   AND {scope_sql}"
            ), {"c": cid, **scope_args}).scalar() or 0),
        },
        "recent_payments": [{
            "paid_at": str(r[0] or ""), "customer_id": r[1] or "",
            "customer_name": r[2] or "", "amount": float(r[3] or 0),
            "payment_mode": r[4] or "",
        } for r in recent_payments],
    }
# === END sub-lco dashboard stats (auto) ===



# _S39R2_SLCO_PHOTO — sub-LCO profile photo upload.
@portal_router.post("/api/sub-lco/profile/upload-photo")
async def api_sub_lco_upload_photo(request: Request,
                                     photo: UploadFile = File(...),
                                     db: Session = Depends(get_db)):
    gate = require_sub_lco_json(request)
    if gate:
        return gate
    cid = request.session.get("company_id")
    sid = _session_sub_lco_id(request)
    if not sid:
        return JSONResponse({"success": False,
            "message": "no sub-lco session"}, status_code=401)
    # Validate file type
    fname = (photo.filename or "").lower()
    ext = ""
    for e in (".jpg", ".jpeg", ".png", ".webp"):
        if fname.endswith(e):
            ext = e
            break
    if not ext:
        return JSONResponse({"success": False,
            "message": "Only JPG/PNG/WEBP allowed"}, status_code=400)
    import os, uuid
    upload_dir = "/opt/ispbilling/admin-portal/static/sub_lco_photos"
    os.makedirs(upload_dir, exist_ok=True)
    safe = f"slco_{cid}_{sid}_{uuid.uuid4().hex[:8]}{ext}"
    full = os.path.join(upload_dir, safe)
    try:
        contents = await photo.read()
        if len(contents) > 4 * 1024 * 1024:
            return JSONResponse({"success": False,
                "message": "File too large (max 4 MB)"}, status_code=400)
        with open(full, "wb") as f:
            f.write(contents)
    except Exception as e:
        return JSONResponse({"success": False,
            "message": f"Upload failed: {e}"}, status_code=500)
    rel_path = f"/static/sub_lco_photos/{safe}"
    db.execute(text(
        "UPDATE sub_lcos SET profile_image=:p, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=:i AND company_id=:c"
    ), {"p": rel_path, "i": sid, "c": cid})
    db.commit()
    return {"success": True, "profile_image": rel_path}

