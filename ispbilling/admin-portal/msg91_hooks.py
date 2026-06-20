"""
msg91_hooks.py — Session 2026.02
Thin convenience layer around msg91_whatsapp.py.

Public functions are safe to call from inside any DB transaction:
they NEVER raise. All errors are logged and swallowed so they never
break the calling code path (invoice creation, payment creation, etc.).

  notify_invoice_generated(invoice, customer, company)
  notify_payment_received(payment, customer, company, plan_active_till=None)
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any, Optional

log = logging.getLogger("msg91_hooks")


def _company_address(company) -> str:
    """Multi-line full address string. Empty if no address on file."""
    for attr in ("company_address", "address"):
        v = (getattr(company, attr, None) or "").strip()
        if v:
            return v
    return ""


def _company_support_phone(company) -> str:
    """Pick the best phone number to put in the message footer."""
    for attr in ("support_phone", "company_phone", "phone"):
        v = (getattr(company, attr, None) or "").strip()
        if v:
            return v
    return "+918085868114"  # global support fallback


def _company_name(company) -> str:
    for attr in ("company_name", "name"):
        v = (getattr(company, attr, None) or "").strip()
        if v:
            return v
    return "AUTO ISP BILLING"


def _lookup_company(company_id: str):
    """Fetch a Company row by company_id without an injected session.
    Returns an object with `.company_name` / `.company_phone`, or None."""
    if not company_id:
        return None
    try:
        from database import SessionLocal, Company
        sess = SessionLocal()
        try:
            return sess.query(Company).filter(
                Company.company_id == str(company_id)).first()
        finally:
            sess.close()
    except Exception:
        return None


def _fmt_date(v) -> str:
    """Render an SQL date/datetime to '12-May-2026'."""
    if not v:
        return ""
    try:
        if hasattr(v, "strftime"):
            return v.strftime("%d-%b-%Y")
        s = str(v).strip()
        # accept YYYY-MM-DD
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d-%b-%Y")
        return s
    except Exception:
        return str(v)


def _fmt_amount(v) -> str:
    try:
        f = float(v or 0)
        if abs(f - int(f)) < 0.01:
            return str(int(round(f)))
        return f"{f:.2f}"
    except Exception:
        return "0"


def _spawn(fn, *args, **kwargs) -> None:
    """Fire-and-forget background thread for the actual HTTP call.
    Keeps the calling request fast and resilient to MSG91 latency."""
    def runner():
        try:
            fn(*args, **kwargs)
        except Exception as e:
            log.exception("msg91 hook background send failed: %s", e)
    t = threading.Thread(target=runner, daemon=True, name="msg91-send")
    t.start()


# ───────────────────────── public hooks ───────────────────────────
def notify_invoice_generated(invoice: Any, customer: Any, company: Any) -> None:
    """Send the `invoice_generated` template to the customer."""
    try:
        phone = (getattr(customer, "customer_phone", "") or "").strip()
        if not phone:
            log.info("invoice notify skipped: no phone on customer %s",
                     getattr(customer, "customer_id", "?"))
            return
        from msg91_whatsapp import send_invoice_generated, normalise_phone
        # Pre-check phone validity to skip threads we know will fail.
        if not normalise_phone(phone):
            return
        _spawn(
            send_invoice_generated,
            phone=phone,
            customer_name=(getattr(customer, "customer_name", "") or "Customer"),
            invoice_no=(getattr(invoice, "invoice_no", "")
                         or f"#{getattr(invoice, 'id', '')}"),
            amount=_fmt_amount(getattr(invoice, "total_amount", 0)),
            due_date=_fmt_date(getattr(invoice, "due_date", "")),
            support_phone=_company_support_phone(company),
            company_name=_company_name(company),
            company_address=_company_address(company),
            invoice_id=getattr(invoice, "id", None),
            company_id=getattr(invoice, "company_id", None)
                       or getattr(customer, "company_id", None),
        )
    except Exception as e:
        log.exception("notify_invoice_generated failed: %s", e)


def notify_payment_received(
    payment: Any, customer: Any, company: Any,
    plan_active_till: Optional[str] = None,
) -> None:
    """Send the `payment_received` template to the customer."""
    try:
        phone = (getattr(customer, "customer_phone", "") or "").strip()
        if not phone:
            log.info("payment notify skipped: no phone on customer %s",
                     getattr(customer, "customer_id", "?"))
            return
        from msg91_whatsapp import send_payment_received, normalise_phone
        if not normalise_phone(phone):
            return
        # Resolve company if not provided
        if company is None:
            company = _lookup_company(getattr(payment, "company_id", None)
                                      or getattr(customer, "company_id", None))
        # __RENEWAL_PERIOD_MSG91__  plan_active_till from invoice, not
        # customer.end_date — postpaid customers' end_date may already
        # have advanced past the invoice this payment covers.
        try:
            from invoice_period_for_payment import invoice_period_for_payment as _ipfp_msg
            _inv_p_msg = _ipfp_msg(payment, None)
            if _inv_p_msg and _inv_p_msg.get("end_date"):
                _ed = _inv_p_msg["end_date"]
                # Format YYYY-MM-DD → DD-Mon-YYYY for the template
                plan_active_till = _fmt_date(_ed)
        except Exception as _e_ipm:
            log.warning("plan_active_till from invoice failed: %s", _e_ipm)
        # Plan-active-till: prefer customer.end_date if not explicitly given
        if not plan_active_till:
            plan_active_till = _fmt_date(getattr(customer, "end_date", ""))
        # Receipt no: prefer transaction_no, fall back to payment id
        receipt_no = (getattr(payment, "transaction_no", "") or "").strip() \
                      or f"RCPT-{getattr(payment, 'id', '')}"
        _spawn(
            send_payment_received,
            phone=phone,
            customer_name=(getattr(customer, "customer_name", "") or "Customer"),
            amount=_fmt_amount(getattr(payment, "amount", 0)),
            paid_date=_fmt_date(getattr(payment, "paid_at", None)
                                  or datetime.utcnow()),
            receipt_no=receipt_no,
            plan_active_till=plan_active_till or "",
            company_name=_company_name(company),
            company_address=_company_address(company),
            payment_id=getattr(payment, "id", None),
            company_id=getattr(payment, "company_id", None)
                        or getattr(customer, "company_id", None),
        )
    except Exception as e:
        log.exception("notify_payment_received failed: %s", e)
