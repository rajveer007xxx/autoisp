"""
Expense tracking module (Round 3 — Session 39 finance rebuild).

Owns:
  • Idempotent SQLite `expenses` table
  • CRUD APIs at /api/admin/expenses
  • Summary API at /api/admin/expenses/summary
  • Revenue summary at /api/admin/revenue/summary
  • Revenue list at /api/admin/revenue/list
  • Combined dashboard at /api/admin/finance/dashboard

Tenant isolation: every query is scoped by request.session["company_id"].
"""

from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Optional, List

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db, engine, Payment, Invoice, Customer

router = APIRouter()

# ---------- Schema ----------

EXPENSE_CATEGORIES = [
    "Salary", "Material", "Transport", "Petrol",
    "Equipment", "Maintenance", "Rent", "Utilities",
    "Internet/Bandwidth", "Marketing", "Office Supplies",
    "Tax/Govt Fees", "Other",
]

PAYMENT_MODES = ["Cash", "Bank Transfer", "Cheque", "UPI", "Card", "Other"]


def _ensure_table() -> None:
    """Create the expenses table once at import time."""
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id    TEXT NOT NULL,
                expense_date  TEXT NOT NULL,
                category      TEXT NOT NULL,
                sub_category  TEXT,
                amount        REAL NOT NULL DEFAULT 0,
                payment_mode  TEXT,
                vendor        TEXT,
                paid_to       TEXT,
                description   TEXT,
                attachment    TEXT,
                created_by    TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at    TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_expenses_company_date "
            "ON expenses(company_id, expense_date)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_expenses_company_category "
            "ON expenses(company_id, category)"
        )


_ensure_table()


# ---------- Auth helper ----------

def _require_admin(request: Request):
    """Backwards-compat alias: returns (company_id, actor) and rejects scoped roles."""
    sc = _require_scope(request)
    if sc["role"] in ("sub_lco", "employee"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return sc["company_id"], sc["actor"]


def _require_scope(request: Request):
    """_S39R5B_SCOPED — accept admin / sub_lco / employee / superadmin.
    Returns dict {company_id, role, actor, sub_lco_id, employee_id}."""
    sess = request.session
    company_id = sess.get("company_id")
    if not company_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    ut = (sess.get("user_type") or "").lower()
    if ut not in ("admin", "superadmin", "sub_lco", "sublco", "employee"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    actor = sess.get("user_name") or sess.get("user_id") or ut or "admin"
    sub_lco_id = sess.get("sub_lco_db_id") if ut.startswith("sub_lco") or ut == "sublco" else None
    employee_id = sess.get("user_id") if ut == "employee" else None
    return {
        "company_id": company_id,
        "role": "sub_lco" if ut == "sublco" else ut,
        "actor": str(actor),
        "sub_lco_id": int(sub_lco_id) if sub_lco_id else None,
        "employee_id": str(employee_id) if employee_id else None,
    }


def _scope_where(scope: dict, alias: str = ""):
    """Compose a SQL WHERE fragment + params dict for tenant + role isolation."""
    a = (alias + ".") if alias else ""
    where = [f"{a}company_id=:cid"]
    params = {"cid": scope["company_id"]}
    if scope["role"] == "sub_lco":
        where.append(f"{a}sub_lco_id=:sid")
        params["sid"] = scope["sub_lco_id"] or -1
    elif scope["role"] == "employee":
        where.append(f"{a}employee_id=:eid")
        params["eid"] = scope["employee_id"] or "__none__"
    return " AND ".join(where), params


# ---------- Models ----------

class ExpenseIn(BaseModel):
    expense_date: str
    category: str
    sub_category: Optional[str] = ""
    amount: float
    payment_mode: Optional[str] = ""
    vendor: Optional[str] = ""
    paid_to: Optional[str] = ""
    description: Optional[str] = ""
    attachment: Optional[str] = ""


class ExpenseOut(ExpenseIn):
    id: int
    created_by: Optional[str] = ""
    created_at: Optional[str] = ""


# ---------- Helpers ----------

def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "expense_date": row[1] or "",
        "category": row[2] or "",
        "sub_category": row[3] or "",
        "amount": float(row[4] or 0),
        "payment_mode": row[5] or "",
        "vendor": row[6] or "",
        "paid_to": row[7] or "",
        "description": row[8] or "",
        "attachment": row[9] or "",
        "created_by": row[10] or "",
        "created_at": str(row[11]) if row[11] else "",
    }


def _parse_period(period: str, dfrom: Optional[str], dto: Optional[str]):
    today = date.today()
    if period == "today":
        return today.isoformat(), today.isoformat()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if period == "month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if period == "year":
        return today.replace(month=1, day=1).isoformat(), today.isoformat()
    if period == "30d":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if period == "custom":
        return (dfrom or "1970-01-01"), (dto or today.isoformat())
    return "1970-01-01", today.isoformat()


# ---------- CRUD: Expenses ----------

@router.get("/api/admin/expenses/categories")
async def list_categories(request: Request):
    _require_admin(request)
    return {"categories": EXPENSE_CATEGORIES, "payment_modes": PAYMENT_MODES}


@router.get("/api/admin/expenses")
async def list_expenses(
    request: Request,
    period: str = "month",
    dfrom: Optional[str] = None,
    dto: Optional[str] = None,
    category: Optional[str] = None,
    payment_mode: Optional[str] = None,
    q: Optional[str] = None,
):
    scope = _require_scope(request)
    start, end = _parse_period(period, dfrom, dto)
    where, params = _scope_where(scope)
    params.update({"start": start, "end": end})
    sql = (
        "SELECT id, expense_date, category, sub_category, amount, "
        "payment_mode, vendor, paid_to, description, attachment, "
        "created_by, created_at "
        f"FROM expenses WHERE {where} AND deleted_at IS NULL "
        "AND expense_date BETWEEN :start AND :end"
    )
    if category:
        sql += " AND category=:cat"
        params["cat"] = category
    if payment_mode:
        sql += " AND payment_mode=:pm"
        params["pm"] = payment_mode
    if q:
        sql += " AND (description LIKE :q OR vendor LIKE :q OR paid_to LIKE :q)"
        params["q"] = f"%{q}%"
    sql += " ORDER BY expense_date DESC, id DESC"

    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).fetchall()

    items = [_row_to_dict(r) for r in rows]
    total = round(sum(i["amount"] for i in items), 2)
    return {"items": items, "total": total, "count": len(items),
            "period": {"from": start, "to": end}}


@router.post("/api/admin/expenses")
async def create_expense(request: Request, payload: ExpenseIn):
    scope = _require_scope(request)
    company_id, actor = scope["company_id"], scope["actor"]
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")
    if payload.category not in EXPENSE_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")
    try:
        datetime.fromisoformat(payload.expense_date)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid expense_date (YYYY-MM-DD)")

    with engine.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO expenses (company_id, expense_date, category, sub_category, "
                "amount, payment_mode, vendor, paid_to, description, attachment, created_by, "
                "sub_lco_id, employee_id) "
                "VALUES (:cid,:dt,:cat,:sub,:amt,:pm,:vendor,:paid,:desc,:att,:by,:sid,:eid)"
            ),
            {
                "cid": company_id,
                "dt": payload.expense_date,
                "cat": payload.category,
                "sub": payload.sub_category or "",
                "amt": float(payload.amount),
                "pm": payload.payment_mode or "",
                "vendor": payload.vendor or "",
                "paid": payload.paid_to or "",
                "desc": payload.description or "",
                "att": payload.attachment or "",
                "by": actor,
                "sid": scope["sub_lco_id"], "eid": scope["employee_id"],
            },
        )
        new_id = result.lastrowid
    return {"success": True, "id": new_id}


@router.put("/api/admin/expenses/{eid}")
async def update_expense(eid: int, request: Request, payload: ExpenseIn):
    scope = _require_scope(request)
    company_id = scope["company_id"]
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than 0")
    if payload.category not in EXPENSE_CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    with engine.begin() as conn:
        where, _wp = _scope_where(scope)
        existing = conn.execute(
            text(f"SELECT id FROM expenses WHERE id=:id AND {where} AND deleted_at IS NULL"),
            {**_wp, "id": eid},
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Expense not found")
        conn.execute(
            text(
                "UPDATE expenses SET expense_date=:dt, category=:cat, sub_category=:sub, "
                "amount=:amt, payment_mode=:pm, vendor=:vendor, paid_to=:paid, "
                "description=:desc, attachment=:att, updated_at=CURRENT_TIMESTAMP "
                f"WHERE id=:id AND {where}"
            ),
            {
                **_wp, "id": eid,
                "dt": payload.expense_date, "cat": payload.category,
                "sub": payload.sub_category or "", "amt": float(payload.amount),
                "pm": payload.payment_mode or "", "vendor": payload.vendor or "",
                "paid": payload.paid_to or "", "desc": payload.description or "",
                "att": payload.attachment or "",
            },
        )
    return {"success": True}


@router.delete("/api/admin/expenses/{eid}")
async def delete_expense(eid: int, request: Request):
    scope = _require_scope(request)
    where, _wp = _scope_where(scope)
    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE expenses SET deleted_at=CURRENT_TIMESTAMP "
                 f"WHERE id=:id AND {where} AND deleted_at IS NULL"),
            {**_wp, "id": eid},
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Expense not found")
    return {"success": True}


@router.get("/api/admin/expenses/summary")
async def expenses_summary(
    request: Request,
    period: str = "month",
    dfrom: Optional[str] = None,
    dto: Optional[str] = None,
):
    scope = _require_scope(request)
    start, end = _parse_period(period, dfrom, dto)
    where, params = _scope_where(scope)
    params.update({"start": start, "end": end})
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT category, COALESCE(SUM(amount),0), COUNT(*) "
                f"FROM expenses WHERE {where} AND deleted_at IS NULL "
                "AND expense_date BETWEEN :start AND :end "
                "GROUP BY category ORDER BY 2 DESC"
            ),
            params,
        ).fetchall()
        rows_pm = conn.execute(
            text(
                "SELECT COALESCE(payment_mode,''), COALESCE(SUM(amount),0), COUNT(*) "
                f"FROM expenses WHERE {where} AND deleted_at IS NULL "
                "AND expense_date BETWEEN :start AND :end "
                "GROUP BY payment_mode ORDER BY 2 DESC"
            ),
            params,
        ).fetchall()

    by_cat = [{"category": r[0], "amount": round(float(r[1]), 2), "count": int(r[2])} for r in rows]
    by_pm = [{"payment_mode": r[0] or "Unspecified",
              "amount": round(float(r[1]), 2), "count": int(r[2])} for r in rows_pm]
    total = round(sum(c["amount"] for c in by_cat), 2)
    return {"total": total, "by_category": by_cat, "by_payment_mode": by_pm,
            "period": {"from": start, "to": end},
            "categories_master": EXPENSE_CATEGORIES}


# ---------- Revenue ----------

@router.get("/api/admin/revenue/summary")
async def revenue_summary(
    request: Request,
    period: str = "month",
    dfrom: Optional[str] = None,
    dto: Optional[str] = None,
    db: Session = Depends(get_db),
):
    company_id, _ = _require_admin(request)
    start, end = _parse_period(period, dfrom, dto)
    start_dt = datetime.fromisoformat(start + "T00:00:00")
    end_dt = datetime.fromisoformat(end + "T23:59:59")

    payments = db.query(Payment).filter(
        Payment.company_id == company_id,
        Payment.paid_at >= start_dt,
        Payment.paid_at <= end_dt,
    ).all()

    total = round(sum(p.amount or 0 for p in payments), 2)
    by_mode: dict[str, dict] = {}
    for p in payments:
        m = p.payment_mode or "Unspecified"
        slot = by_mode.setdefault(m, {"amount": 0.0, "count": 0})
        slot["amount"] += p.amount or 0
        slot["count"] += 1
    by_mode_list = [{"payment_mode": k, "amount": round(v["amount"], 2), "count": v["count"]}
                    for k, v in sorted(by_mode.items(), key=lambda kv: -kv[1]["amount"])]
    return {"total": total, "count": len(payments),
            "by_payment_mode": by_mode_list,
            "period": {"from": start, "to": end}}


@router.get("/api/admin/revenue/list")
async def revenue_list(
    request: Request,
    period: str = "month",
    dfrom: Optional[str] = None,
    dto: Optional[str] = None,
    payment_mode: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
):
    company_id, _ = _require_admin(request)
    start, end = _parse_period(period, dfrom, dto)
    start_dt = datetime.fromisoformat(start + "T00:00:00")
    end_dt = datetime.fromisoformat(end + "T23:59:59")

    qry = db.query(Payment).filter(
        Payment.company_id == company_id,
        Payment.paid_at >= start_dt,
        Payment.paid_at <= end_dt,
    )
    if payment_mode:
        qry = qry.filter(Payment.payment_mode == payment_mode)
    payments = qry.order_by(Payment.paid_at.desc()).all()

    cust_ids = {p.customer_id for p in payments if p.customer_id}
    cust_map: dict[str, str] = {}
    if cust_ids:
        for c in db.query(Customer).filter(
            Customer.company_id == company_id,
            Customer.customer_id.in_(list(cust_ids)),
        ).all():
            cust_map[c.customer_id] = c.customer_name or ""

    items = []
    for p in payments:
        if q and q.strip():
            ql = q.strip().lower()
            if (ql not in (p.customer_id or "").lower()
                    and ql not in (cust_map.get(p.customer_id, "")).lower()
                    and ql not in (p.transaction_no or "").lower()):
                continue
        items.append({
            "id": p.id,
            "customer_id": p.customer_id or "",
            "customer_name": cust_map.get(p.customer_id, ""),
            "amount": round(float(p.amount or 0), 2),
            "discount": round(float(p.discount or 0), 2),
            "payment_mode": p.payment_mode or "",
            "transaction_no": p.transaction_no or "",
            "paid_at": p.paid_at.isoformat() if p.paid_at else "",
            "remarks": p.remarks or "",
            "employee_id": p.employee_id or "",
        })

    total = round(sum(i["amount"] for i in items), 2)
    return {"items": items, "total": total, "count": len(items),
            "period": {"from": start, "to": end}}


# ---------- Combined finance dashboard ----------

@router.get("/api/admin/finance/dashboard")
async def finance_dashboard(
    request: Request,
    period: str = "month",
    dfrom: Optional[str] = None,
    dto: Optional[str] = None,
    db: Session = Depends(get_db),
):
    company_id, _ = _require_admin(request)
    start, end = _parse_period(period, dfrom, dto)
    start_dt = datetime.fromisoformat(start + "T00:00:00")
    end_dt = datetime.fromisoformat(end + "T23:59:59")

    revenue = db.query(Payment).filter(
        Payment.company_id == company_id,
        Payment.paid_at >= start_dt,
        Payment.paid_at <= end_dt,
    ).all()
    rev_total = round(sum(p.amount or 0 for p in revenue), 2)

    with engine.begin() as conn:
        exp_total = conn.execute(
            text("SELECT COALESCE(SUM(amount),0) FROM expenses "
                 "WHERE company_id=:cid AND deleted_at IS NULL "
                 "AND expense_date BETWEEN :start AND :end"),
            {"cid": company_id, "start": start, "end": end},
        ).scalar() or 0.0
        cat_rows = conn.execute(
            text("SELECT category, COALESCE(SUM(amount),0) FROM expenses "
                 "WHERE company_id=:cid AND deleted_at IS NULL "
                 "AND expense_date BETWEEN :start AND :end "
                 "GROUP BY category ORDER BY 2 DESC"),
            {"cid": company_id, "start": start, "end": end},
        ).fetchall()
        # Monthly trend (last 6 months)
        trend_rows = conn.execute(
            text("SELECT substr(expense_date,1,7), COALESCE(SUM(amount),0) "
                 "FROM expenses WHERE company_id=:cid AND deleted_at IS NULL "
                 "GROUP BY substr(expense_date,1,7) ORDER BY 1 DESC LIMIT 6"),
            {"cid": company_id},
        ).fetchall()

    exp_total = round(float(exp_total), 2)
    profit = round(rev_total - exp_total, 2)
    margin = round((profit / rev_total) * 100, 2) if rev_total > 0 else 0.0

    # Build trend with revenue per month from payments
    months_set = {r[0] for r in trend_rows}
    today = date.today()
    for i in range(6):
        m = (today.replace(day=1) - timedelta(days=30 * i)).strftime("%Y-%m")
        months_set.add(m)
    monthly = []
    for ym in sorted(months_set, reverse=True)[:6]:
        y, mo = ym.split("-")
        first = date(int(y), int(mo), 1)
        next_m = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
        s = datetime.combine(first, datetime.min.time())
        e = datetime.combine(next_m, datetime.min.time())
        rev_m = round(sum(p.amount or 0 for p in db.query(Payment).filter(
            Payment.company_id == company_id,
            Payment.paid_at >= s, Payment.paid_at < e).all()), 2)
        with engine.begin() as conn:
            exp_m = conn.execute(
                text("SELECT COALESCE(SUM(amount),0) FROM expenses "
                     "WHERE company_id=:cid AND deleted_at IS NULL "
                     "AND substr(expense_date,1,7)=:m"),
                {"cid": company_id, "m": ym},
            ).scalar() or 0.0
        exp_m = round(float(exp_m), 2)
        prof_m = round(rev_m - exp_m, 2)
        marg_m = round((prof_m / rev_m) * 100, 2) if rev_m > 0 else 0.0
        monthly.append({"month": ym, "revenue": rev_m, "expense": exp_m,
                        "profit": prof_m, "margin": marg_m})

    by_category = [{"category": r[0], "amount": round(float(r[1]), 2)} for r in cat_rows]

    return {
        "period": {"from": start, "to": end},
        "totals": {"revenue": rev_total, "expense": exp_total,
                   "profit": profit, "margin": margin},
        "expense_by_category": by_category,
        "monthly": monthly,
    }


# ---------- _S39R5A_RECEIPT — Receipt photo upload ---------------------------
import os as _os, time as _time, mimetypes as _mt, uuid as _uuid
from pathlib import Path as _Path
from fastapi import UploadFile as _UploadFile, File as _File
from fastapi.responses import FileResponse as _FileResponse

_RECEIPT_BASE = _Path("/opt/ispbilling/admin-portal/static/uploads")
_ALLOWED_RECEIPT_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf", ".heic"}
_MAX_RECEIPT_BYTES = 8 * 1024 * 1024  # 8 MB


@router.post("/api/admin/expenses/{eid}/receipt")
async def upload_expense_receipt(
    eid: int,
    request: Request,
    file: _UploadFile = _File(...),
):
    company_id, _actor = _require_admin(request)

    # Make sure the row exists & belongs to this tenant
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM expenses WHERE id=:i AND company_id=:c "
                 "AND deleted_at IS NULL"),
            {"i": eid, "c": company_id},
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Expense not found")

    ext = _os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_RECEIPT_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported file type {ext}")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(raw) > _MAX_RECEIPT_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 8 MB)")

    folder = _RECEIPT_BASE / company_id / "expense_receipts"
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"{eid}_{int(_time.time())}_{_uuid.uuid4().hex[:8]}{ext}"
    fs_path = folder / fname
    fs_path.write_bytes(raw)
    rel_url = f"/static/uploads/{company_id}/expense_receipts/{fname}"

    with engine.begin() as conn:
        conn.execute(
            text("UPDATE expenses SET attachment=:a, updated_at=CURRENT_TIMESTAMP "
                 "WHERE id=:i AND company_id=:c"),
            {"a": rel_url, "i": eid, "c": company_id},
        )
    return {"success": True, "url": rel_url, "filename": fname}


@router.delete("/api/admin/expenses/{eid}/receipt")
async def delete_expense_receipt(eid: int, request: Request):
    company_id, _actor = _require_admin(request)
    with engine.begin() as conn:
        cur = conn.execute(
            text("SELECT attachment FROM expenses WHERE id=:i AND company_id=:c "
                 "AND deleted_at IS NULL"),
            {"i": eid, "c": company_id},
        ).fetchone()
    if not cur:
        raise HTTPException(status_code=404, detail="Expense not found")
    url = cur[0] or ""
    if url.startswith("/static/uploads/"):
        try:
            fs = _Path("/opt/ispbilling/admin-portal" + url)
            if fs.exists() and fs.is_file():
                fs.unlink()
        except Exception:
            pass
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE expenses SET attachment='', updated_at=CURRENT_TIMESTAMP "
                 "WHERE id=:i AND company_id=:c"),
            {"i": eid, "c": company_id},
        )
    return {"success": True}
