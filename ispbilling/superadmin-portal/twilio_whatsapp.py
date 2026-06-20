"""
twilio_whatsapp.py — Session 27
Lightweight Twilio WhatsApp sender with automatic fallback:

    • If TWILIO_USE_SANDBOX=1  → sandbox (`whatsapp:+14155238886`), freeform body
    • else                     → production FROM number, use Content Template SID
      (falls back to freeform body if template send fails)

Public helpers:
    send_pay_link_whatsapp(phone, customer_name, amount_due, pay_url) -> dict
    send_invoice_whatsapp(phone, customer_name, amount, pdf_url)       -> dict
    send_admin_reminder(phone, admin_name, company_name, pay_url, amount) -> dict
    bulk_send_pay_link(items) -> dict  # items = [{phone, name, amount, url}, …]

All helpers are sync and meant to be run in BackgroundTasks.

A thin `_get_client()` lazily builds the Twilio client once per worker.
"""
from __future__ import annotations

import os
import json
import time
import logging
from typing import Optional

log = logging.getLogger("twilio_whatsapp")

_client = None
_last_err: Optional[str] = None


# ---------------------------------------------------------------- utils
def _normalise_phone(raw: str) -> Optional[str]:
    """Return E.164 `whatsapp:+91...` or None if not a usable number."""
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:            # bare Indian mobile
        digits = "91" + digits
    if len(digits) < 11:
        return None
    return f"whatsapp:+{digits}"


def _get_client():
    global _client, _last_err
    if _client is not None:
        return _client
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not tok:
        _last_err = "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN missing"
        return None
    try:
        from twilio.rest import Client
        _client = Client(sid, tok)
        return _client
    except Exception as e:
        _last_err = f"twilio import/init failed: {e}"
        log.exception("twilio init")
        return None


def _from_number() -> str:
    if os.environ.get("TWILIO_USE_SANDBOX", "0").strip() in ("1", "true", "True"):
        return os.environ.get("TWILIO_WHATSAPP_SANDBOX_FROM",
                              "whatsapp:+14155238886")
    return os.environ.get("TWILIO_WHATSAPP_FROM", "")


def _send_freeform(to_: str, body: str) -> dict:
    cli = _get_client()
    if not cli:
        return {"success": False, "error": _last_err or "twilio client unavailable"}
    frm = _from_number()
    if not frm:
        return {"success": False, "error": "TWILIO_WHATSAPP_FROM not set"}
    try:
        msg = cli.messages.create(from_=frm, to=to_, body=body)
        return {"success": True, "sid": msg.sid, "status": msg.status}
    except Exception as e:
        log.error("twilio freeform send failed to %s: %s", to_, e)
        return {"success": False, "error": str(e)}


def _send_template(to_: str, content_sid: str, variables: dict,
                   fallback_body: str) -> dict:
    """Try Content Template first; if it fails (template not approved,
    24h-window closed on sandbox, etc.) fall back to freeform body."""
    cli = _get_client()
    if not cli:
        return {"success": False, "error": _last_err or "twilio client unavailable"}
    frm = _from_number()
    msvc = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "").strip()
    try:
        kwargs = {
            "to": to_,
            "content_sid": content_sid,
            "content_variables": json.dumps(variables),
        }
        if msvc:
            kwargs["messaging_service_sid"] = msvc
        else:
            kwargs["from_"] = frm
        msg = cli.messages.create(**kwargs)
        return {"success": True, "sid": msg.sid, "status": msg.status,
                "mode": "template"}
    except Exception as e:
        log.warning("twilio template send failed to %s: %s – falling back", to_, e)
        r = _send_freeform(to_, fallback_body)
        r["template_error"] = str(e)
        r["mode"] = "freeform_fallback"
        return r


# ---------------------------------------------------------------- public API
def send_pay_link_whatsapp(phone: str, customer_name: str,
                           amount_due: float, pay_url: str,
                           company_name: str = "") -> dict:
    to_ = _normalise_phone(phone)
    if not to_:
        return {"success": False, "error": "invalid phone"}
    name = (customer_name or "Customer").strip()
    amt  = f"{float(amount_due or 0):.2f}"
    body = (f"Hi {name}, your "
            f"{company_name or 'internet'} account is expired/suspended. "
            f"Pay Rs.{amt} here to reconnect: {pay_url}")
    sid  = os.environ.get("TWILIO_CONTENT_SID_PAY_LINK", "").strip()
    if sid and os.environ.get("TWILIO_USE_SANDBOX", "0") not in ("1", "true", "True"):
        return _send_template(to_, sid,
                              {"1": name, "2": amt, "3": pay_url},
                              body)
    return _send_freeform(to_, body)


def send_invoice_whatsapp(phone: str, customer_name: str,
                          amount: float, invoice_url: str,
                          invoice_no: str = "",
                          company_name: str = "") -> dict:
    to_ = _normalise_phone(phone)
    if not to_:
        return {"success": False, "error": "invalid phone"}
    body = (f"Hi {customer_name or 'Customer'}, "
            f"your new {company_name or ''} invoice {invoice_no} "
            f"for Rs.{float(amount or 0):.2f} is ready. "
            f"Download: {invoice_url}")
    return _send_freeform(to_, body)


def send_admin_reminder(phone: str, admin_name: str,
                        company_name: str, pay_url: str,
                        amount_due: float = 0.0,
                        reason: str = "expired") -> dict:
    to_ = _normalise_phone(phone)
    if not to_:
        return {"success": False, "error": "invalid phone"}
    body = (f"Hello {admin_name or 'Admin'}, your ISP-billing subscription "
            f"for '{company_name}' is {reason}. Outstanding: "
            f"Rs.{float(amount_due or 0):.2f}. Renew here: {pay_url}")
    return _send_freeform(to_, body)


def bulk_send_pay_link(items: list) -> dict:
    """items: list of dicts `{phone, name, amount, url, company_name?}`.
    Returns summary `{ok, failed, details:[…]}`. Enforces
    TWILIO_RATE_LIMIT_PER_SEC throttle to avoid Twilio 429s."""
    per_sec = float(os.environ.get("TWILIO_RATE_LIMIT_PER_SEC", "1") or 1)
    gap = 1.0 / per_sec if per_sec > 0 else 0
    ok, failed, details = 0, 0, []
    for it in items or []:
        r = send_pay_link_whatsapp(
            it.get("phone") or "",
            it.get("name") or "",
            it.get("amount") or 0,
            it.get("url") or "",
            it.get("company_name") or "",
        )
        if r.get("success"):
            ok += 1
        else:
            failed += 1
        details.append({
            "phone": it.get("phone"),
            "name":  it.get("name"),
            **r,
        })
        if gap:
            time.sleep(gap)
    return {"ok": ok, "failed": failed, "total": ok + failed, "details": details}
