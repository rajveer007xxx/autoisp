"""
invoice_period_for_payment.py — Session 2026.02
Single source of truth for "which invoice does this payment cover?".

Used by:
  • PDF print receipt (generate_receipt)
  • Customer web portal /customer/payments table
  • Mobile app receipt detail (transactions/{id})
  • MSG91 payment_received WhatsApp template plan_active_till

Rule: pick the LATEST invoice with `issue_date <= payment.paid_at`
(falls back to the most recent invoice if none match — defensive).
This matches what an accountant calls "the active billing period at
the moment of payment" and correctly handles BOTH prepaid (invoice
issued at renewal, payment same day) AND postpaid (invoice issued
at end of cycle, payment days later) flows without ever touching
the renewal / invoice-generation pipelines.

Pure SQL implementation so the mobile_api_v3 helper can reuse it.
"""
from __future__ import annotations

import sqlite3
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
from datetime import date, datetime
from typing import Optional, Dict, Any

DB_PATH = "/var/lib/autoispbilling/autoispbilling.db"


def _norm_ymd(s) -> str:
    """Normalise dates to YYYY-MM-DD string for SQL comparison."""
    if not s:
        return ""
    if hasattr(s, "strftime"):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 10 and s[2] == "-" and s[5] == "-":
        # DD-MM-YYYY → YYYY-MM-DD
        return f"{s[6:10]}-{s[3:5]}-{s[0:2]}"
    return s[:10]


def _fmt_ddmm(ymd: str) -> str:
    if not ymd or len(ymd) < 10:
        return ymd or ""
    if ymd[4] == "-" and ymd[7] == "-":
        return f"{ymd[8:10]}-{ymd[5:7]}-{ymd[0:4]}"
    return ymd


def invoice_period_for_payment_sql(
    cur: sqlite3.Cursor,
    *,
    company_id: str,
    customer_id: str,
    paid_at: Any,
) -> Optional[Dict[str, Any]]:
    """SQL helper. `cur` must be a sqlite3 cursor on the billing DB.
    Returns {"start_date","end_date","period_months","plan_name",
             "renewal_period"} or None."""
    if not company_id or not customer_id:
        return None
    paid_ymd = _norm_ymd(paid_at)
    # s41o: detect postpaid once.
    cust_type = ""
    try:
        ct_row = cur.execute(
            "SELECT UPPER(IFNULL(customer_type,'PREPAID')) FROM customers "
            " WHERE customer_id=? AND company_id=? LIMIT 1",
            (customer_id, company_id)).fetchone()
        if ct_row:
            cust_type = (ct_row[0] if not isinstance(ct_row, dict)
                         else ct_row.get(list(ct_row.keys())[0]))
            cust_type = str(cust_type or "").upper()
    except Exception:
        cust_type = ""
    # _S58X_POSTPAID_LATEST_  User spec (2026-06-06):
    #   "postpaid receipt must EXACTLY match the period from the latest
    #    generated invoice — no upcoming/renewal period, no shifts."
    # For postpaid we therefore pick the LATEST invoice by id (=most
    # recently generated) and use its start_date / end_date AS-IS.
    row = None
    if paid_ymd and cust_type == "POSTPAID":
        row = cur.execute(
            "SELECT start_date, end_date, period_months, plan_name, "
            "       issue_date FROM invoices "
            " WHERE company_id=? AND customer_id=? "
            " ORDER BY id DESC LIMIT 1",
            (company_id, customer_id),
        ).fetchone()
    if paid_ymd and not row:
        if cust_type == "POSTPAID":
            # s41o: For postpaid renewals the invoice describes the
            # PAST period that the customer just consumed. When two
            # invoices exist with issue_date <= paid_at (e.g., the
            # past-period invoice issued at renewal + a brand-new
            # next-period one), the receipt must mirror the past-period
            # one — i.e., the most recent ALREADY-COMPLETED period.
            row = cur.execute(
                "SELECT start_date, end_date, period_months, plan_name, "
                "       issue_date FROM invoices "
                " WHERE company_id=? AND customer_id=? "
                "   AND COALESCE(issue_date,'') != '' "
                "   AND COALESCE(issue_date,'') <= ? "
                "   AND COALESCE(end_date,'') != '' "
                "   AND COALESCE(end_date,'') <= ? "
                " ORDER BY end_date DESC, id DESC LIMIT 1",
                (company_id, customer_id, paid_ymd, paid_ymd),
            ).fetchone()
            # Fallback to the universal rule if no completed-period
            # invoice exists yet (very-first cycle for a postpaid user).
            if not row:
                row = cur.execute(
                    "SELECT start_date, end_date, period_months, plan_name, "
                    "       issue_date FROM invoices "
                    " WHERE company_id=? AND customer_id=? "
                    "   AND COALESCE(issue_date,'') != '' "
                    "   AND COALESCE(issue_date,'') <= ? "
                    " ORDER BY issue_date DESC, id DESC LIMIT 1",
                    (company_id, customer_id, paid_ymd),
                ).fetchone()
        else:
            row = cur.execute(
                "SELECT start_date, end_date, period_months, plan_name, "
                "       issue_date FROM invoices "
                " WHERE company_id=? AND customer_id=? "
                "   AND COALESCE(issue_date,'') != '' "
                "   AND COALESCE(issue_date,'') <= ? "
                " ORDER BY issue_date DESC, id DESC LIMIT 1",
                (company_id, customer_id, paid_ymd),
            ).fetchone()
    # 2) Fallback: most recent invoice for the customer (used when
    #    payment is older than the earliest invoice — rare seed data).
    if not row:
        row = cur.execute(
            "SELECT start_date, end_date, period_months, plan_name, "
            "       issue_date FROM invoices "
            " WHERE company_id=? AND customer_id=? "
            " ORDER BY id DESC LIMIT 1",
            (company_id, customer_id),
        ).fetchone()
    if not row:
        return None
    # Support both Row-style and tuple-style cursors
    try:
        start = row["start_date"]
        end = row["end_date"]
        months = row["period_months"]
        plan = row["plan_name"]
        issue = row["issue_date"]
    except (KeyError, IndexError, TypeError):
        start, end, months, plan, issue = row[0], row[1], row[2], row[3], row[4]
    sd = _norm_ymd(start); ed = _norm_ymd(end)
    pm_int = int(months or 0) if months else 0
    # __s56AK_double_shift_fix__  Apply display_period shift only when
    # the postpaid invoice is in 'next-cycle' shape (end_date > paid_at).
    # Past-cycle-shape invoices already represent the consumed period.
    # _S58X_POSTPAID_LATEST_  postpaid never shifts — show invoice AS-IS.
    _needs_shift = False if cust_type == 'POSTPAID' else False
    if cust_type != 'POSTPAID':
        _needs_shift = False
    elif paid_ymd and ed and ed <= paid_ymd:
        _needs_shift = False
    try:
        if _needs_shift:
            from display_period import get_display_period as _gdp
            _new_sd, _new_ed = _gdp(sd, ed, pm_int, cust_type)
            if _new_sd: sd = _new_sd
            if _new_ed: ed = _new_ed
    except Exception:
        # Defensive fallback matches the prior s41o-2 inline logic byte-for-byte.
        if cust_type == "POSTPAID" and sd and pm_int:
            try:
                from datetime import datetime as _dt41o2, timedelta as _td41o2
                from dateutil.relativedelta import relativedelta as _rd41o2
                _sd_dt = _dt41o2.strptime(sd, "%Y-%m-%d")
                _disp_start = _sd_dt - _rd41o2(months=pm_int)
                _disp_end = _sd_dt - _td41o2(days=1)
                sd = _disp_start.strftime("%Y-%m-%d")
                ed = _disp_end.strftime("%Y-%m-%d")
            except Exception:
                pass
    period = ""
    if sd and ed:
        period = f"{_fmt_ddmm(sd)} to {_fmt_ddmm(ed)}"
    elif ed:
        period = f"till {_fmt_ddmm(ed)}"
    return {
        "start_date": sd,
        "end_date": ed,
        "period_months": pm_int,
        "plan_name": plan or "",
        "renewal_period": period,
        "issue_date": _norm_ymd(issue),
    }


def invoice_period_for_payment(payment, db=None) -> Optional[Dict[str, Any]]:
    """ORM-friendly variant. `payment` must expose company_id,
    customer_id, paid_at. `db` is the SQLAlchemy session — if not
    given, we open a sqlite connection directly."""
    if payment is None:
        return None
    company_id = getattr(payment, "company_id", "") or ""
    customer_id = getattr(payment, "customer_id", "") or ""
    paid_at = getattr(payment, "paid_at", None)
    if not company_id or not customer_id:
        return None
    if db is not None:
        try:
            # __s56AJ_orm_postpaid_parity__  Mirror the SQL helper.
            # 1. Detect customer billing type so we can apply the postpaid
            #    "past period" shift consistently with PDF receipts.
            from sqlalchemy import text as _t
            paid_ymd = _norm_ymd(paid_at)
            cust_type = ""
            try:
                ct_row = db.execute(_t(
                    "SELECT UPPER(IFNULL(customer_type,'PREPAID')) "
                    "  FROM customers WHERE customer_id=:c AND company_id=:co LIMIT 1"
                ), {"c": customer_id, "co": company_id}).fetchone()
                if ct_row:
                    cust_type = str(ct_row[0] or "").upper()
            except Exception:
                cust_type = ""

            row = None
            if paid_ymd:
                if cust_type == "POSTPAID":
                    # Past period that the customer JUST consumed —
                    # prefer the latest invoice whose end_date <= paid_at.
                    row = db.execute(_t(
                        "SELECT start_date, end_date, period_months, plan_name, "
                        "       issue_date FROM invoices "
                        " WHERE company_id=:co AND customer_id=:c "
                        "   AND COALESCE(issue_date,'') != '' "
                        "   AND COALESCE(issue_date,'') <= :d "
                        "   AND COALESCE(end_date,'') != '' "
                        "   AND COALESCE(end_date,'') <= :d "
                        " ORDER BY end_date DESC, id DESC LIMIT 1"
                    ), {"co": company_id, "c": customer_id, "d": paid_ymd}).fetchone()
                    if not row:
                        row = db.execute(_t(
                            "SELECT start_date, end_date, period_months, plan_name, "
                            "       issue_date FROM invoices "
                            " WHERE company_id=:co AND customer_id=:c "
                            "   AND COALESCE(issue_date,'') != '' "
                            "   AND COALESCE(issue_date,'') <= :d "
                            " ORDER BY issue_date DESC, id DESC LIMIT 1"
                        ), {"co": company_id, "c": customer_id, "d": paid_ymd}).fetchone()
                else:
                    row = db.execute(_t(
                        "SELECT start_date, end_date, period_months, plan_name, "
                        "       issue_date FROM invoices "
                        " WHERE company_id=:co AND customer_id=:c "
                        "   AND COALESCE(issue_date,'') != '' "
                        "   AND COALESCE(issue_date,'') <= :d "
                        " ORDER BY issue_date DESC, id DESC LIMIT 1"
                    ), {"co": company_id, "c": customer_id, "d": paid_ymd}).fetchone()
            if not row:
                row = db.execute(_t(
                    "SELECT start_date, end_date, period_months, plan_name, "
                    "       issue_date FROM invoices "
                    " WHERE company_id=:co AND customer_id=:c "
                    " ORDER BY id DESC LIMIT 1"
                ), {"co": company_id, "c": customer_id}).fetchone()
            if not row:
                return None
            sd = _norm_ymd(row[0]); ed = _norm_ymd(row[1])
            pm_int = int(row[2] or 0) if row[2] else 0
            # 2. __s56AK_double_shift_fix__  Shift only when postpaid
            #    invoice is in next-cycle shape (end_date > paid_at).
            _needs_shift = True
            if cust_type != 'POSTPAID':
                _needs_shift = False
            elif paid_ymd and ed and ed <= paid_ymd:
                _needs_shift = False
            if _needs_shift:
                try:
                    from display_period import get_display_period as _gdp
                    _new_sd, _new_ed = _gdp(sd, ed, pm_int, cust_type)
                    if _new_sd: sd = _new_sd
                    if _new_ed: ed = _new_ed
                except Exception:
                    pass
            period = ""
            if sd and ed:
                period = f"{_fmt_ddmm(sd)} to {_fmt_ddmm(ed)}"
            elif ed:
                period = f"till {_fmt_ddmm(ed)}"
            return {
                "start_date": sd, "end_date": ed,
                "period_months": pm_int,
                "plan_name": row[3] or "",
                "renewal_period": period,
                "issue_date": _norm_ymd(row[4]),
            }
        except Exception as _e:
            print(f"[invoice_period_for_payment ORM] {_e}")
            pass
    # Fallback: direct sqlite
    try:
        con = _compat_conn(timeout=3.0)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        try:
            return invoice_period_for_payment_sql(
                cur, company_id=company_id, customer_id=customer_id,
                paid_at=paid_at,
            )
        finally:
            con.close()
    except Exception:
        return None
