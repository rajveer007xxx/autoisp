"""
msg91_whatsapp.py — Session 2026.02
MSG91 WhatsApp Business sender for transactional billing notifications.

Public helpers (all synchronous, safe to run inside FastAPI BackgroundTasks):

    send_invoice_generated(phone, customer_name, invoice_no, amount, due_date,
                            support_phone, company_name,
                            invoice_id=None, company_id=None) -> dict
    send_payment_received(phone, customer_name, amount, paid_date,
                           receipt_no, plan_active_till,
                           company_name, payment_id=None, company_id=None) -> dict
    send_plan_expiring_soon(phone, customer_name, plan_name, expiry_date,
                              outstanding, support_phone, company_name,
                              customer_id=None, company_id=None) -> dict

Each helper:
  • normalises the phone (E.164 digits-only, "91" prefix if 10 digits)
  • posts to MSG91 bulk endpoint
  • logs the send into `whatsapp_message_logs` (provider='msg91')
  • returns {success, msg91_message_id, log_id, error?}

Configuration (read from environment):
    MSG91_AUTHKEY              — required, master auth key
    MSG91_INTEGRATED_NUMBER    — required, E.164 sender number, no '+'
    MSG91_WHATSAPP_ENABLED     — '1' to enable; anything else = no-op (dry run)
"""
from __future__ import annotations

import os
import json
import logging
import sqlite3
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
import time
from datetime import datetime
from typing import Optional, Dict, Any

import requests

log = logging.getLogger("msg91_whatsapp")

# ───────────────────────────── config ──────────────────────────────
MSG91_URL = "https://control.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/bulk/"
DB_PATH   = "/var/lib/autoispbilling/autoispbilling.db"
HTTP_TIMEOUT = 15  # seconds


def _authkey() -> Optional[str]:
    return (os.environ.get("MSG91_AUTHKEY") or "").strip() or None


def _integrated_number() -> Optional[str]:
    return (os.environ.get("MSG91_INTEGRATED_NUMBER") or "").strip() or None


def _enabled() -> bool:
    return (os.environ.get("MSG91_WHATSAPP_ENABLED") or "0").strip() in (
        "1", "true", "True", "yes", "YES"
    )


# ─────────────────────────── helpers ───────────────────────────────
def normalise_phone(raw: str) -> Optional[str]:
    """Return E.164 digits-only ('919876543210') or None if unusable."""
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) < 11:
        return None
    return digits


def _log_send(
    *,
    company_id: Optional[str],
    phone: str,
    template_name: str,
    template_vars: Dict[str, str],
    status: str,
    provider_message_id: Optional[str] = None,
    error_message: Optional[str] = None,
    linked_invoice_id: Optional[int] = None,
    linked_payment_id: Optional[int] = None,
) -> Optional[int]:
    """Insert a row into whatsapp_message_logs; return its id."""
    try:
        con = _compat_conn(timeout=5.0)
        con.execute("PRAGMA busy_timeout=4000")
        cur = con.cursor()
        cur.execute(
            """INSERT INTO whatsapp_message_logs
               (provider, company_id, recipient_phone, template_name,
                message_content, status, error_message,
                provider_message_id, sent_at,
                linked_invoice_id, linked_payment_id)
               VALUES ('msg91', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (company_id, phone, template_name,
             json.dumps(template_vars, ensure_ascii=False),
             status, error_message, provider_message_id,
             datetime.utcnow().isoformat(),
             linked_invoice_id, linked_payment_id),
        )
        rid = cur.lastrowid
        con.commit()
        con.close()
        return rid
    except Exception as e:
        log.exception("msg91 log_send failed: %s", e)
        return None


def _post_template(
    *,
    to_phone: str,
    template_name: str,
    body_values: list,
) -> Dict[str, Any]:
    """Post a single template message to MSG91 bulk endpoint."""
    authkey = _authkey()
    integrated = _integrated_number()
    if not authkey or not integrated:
        return {"success": False,
                "error": "MSG91_AUTHKEY / MSG91_INTEGRATED_NUMBER not set"}

    components = {
        f"body_{i}": {"type": "text", "value": str(v)}
        for i, v in enumerate(body_values, start=1)
    }
    payload = {
        "integrated_number": integrated,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en", "policy": "deterministic"},
                "to_and_components": [
                    {"to": [to_phone], "components": components}
                ],
            },
        },
    }
    headers = {"Content-Type": "application/json", "authkey": authkey}
    try:
        r = requests.post(MSG91_URL, json=payload, headers=headers,
                           timeout=HTTP_TIMEOUT)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        ok = (200 <= r.status_code < 300)
        # MSG91 returns various shapes; try to dig out a message id.
        msg_id = None
        if isinstance(body, dict):
            # Bulk endpoint commonly returns request_id at the top level.
            msg_id = body.get("request_id") or body.get("requestId")
            # Fallback: nested "data" / "response" objects
            if not msg_id:
                data = body.get("data") or body.get("response")
                if isinstance(data, dict):
                    msg_id = data.get("messageId") or data.get("requestId")
                elif isinstance(data, list) and data:
                    first = data[0]
                    if isinstance(first, dict):
                        msg_id = first.get("messageId") or first.get("requestId")
            msg_id = msg_id or body.get("message_id")
        return {
            "success": ok,
            "msg91_response": body,
            "msg91_message_id": msg_id,
            "http_status": r.status_code,
            "error": None if ok else (body.get("message") if isinstance(body, dict)
                                       else f"HTTP {r.status_code}"),
        }
    except requests.RequestException as e:
        log.exception("msg91 post failed")
        return {"success": False, "error": str(e)}


# ───────────────────────── public senders ──────────────────────────
def _send_template(
    template: str,
    body_values: list,
    phone_raw: str,
    *,
    company_id: Optional[str] = None,
    linked_invoice_id: Optional[int] = None,
    linked_payment_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Shared wrapper: normalise, send, log."""
    phone = normalise_phone(phone_raw)
    if not phone:
        return {"success": False, "error": "invalid phone"}

    template_vars = {f"body_{i+1}": v for i, v in enumerate(body_values)}

    if not _enabled():
        log_id = _log_send(
            company_id=company_id, phone=phone, template_name=template,
            template_vars=template_vars, status="dry_run",
            linked_invoice_id=linked_invoice_id,
            linked_payment_id=linked_payment_id,
        )
        return {"success": True, "dry_run": True, "log_id": log_id}

    res = _post_template(
        to_phone=phone, template_name=template, body_values=body_values
    )
    log_id = _log_send(
        company_id=company_id, phone=phone, template_name=template,
        template_vars=template_vars,
        status=("sent" if res.get("success") else "failed"),
        provider_message_id=res.get("msg91_message_id"),
        error_message=res.get("error"),
        linked_invoice_id=linked_invoice_id,
        linked_payment_id=linked_payment_id,
    )
    res["log_id"] = log_id
    return res


def send_invoice_generated(
    phone: str, customer_name: str, invoice_no: str, amount: str,
    due_date: str, support_phone: str, company_name: str,
    company_address: str = "",
    *, invoice_id: Optional[int] = None, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _send_template(
        "invoice_generated",
        [customer_name, invoice_no, amount, due_date, support_phone,
         company_name, company_address or company_name],
        phone,
        company_id=company_id,
        linked_invoice_id=invoice_id,
    )


def send_payment_received(
    phone: str, customer_name: str, amount: str, paid_date: str,
    receipt_no: str, plan_active_till: str, company_name: str,
    company_address: str = "",
    *, payment_id: Optional[int] = None, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _send_template(
        "payment_received",
        [customer_name, amount, paid_date, receipt_no, plan_active_till,
         company_name, company_address or company_name],
        phone,
        company_id=company_id,
        linked_payment_id=payment_id,
    )


def send_plan_expiring_soon(
    phone: str, customer_name: str, plan_name: str, expiry_date: str,
    outstanding: str, support_phone: str, company_name: str,
    company_address: str = "",
    *, customer_id: Optional[str] = None, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _send_template(
        "plan_expiring_soon",
        [customer_name, plan_name, expiry_date, outstanding, support_phone,
         company_name, company_address or company_name],
        phone,
        company_id=company_id,
    )


def send_payment_link(
    phone: str, customer_name: str, company_name: str, outstanding: str,
    pay_url: str, support_phone: str, company_address: str = "",
    *, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Template 4 — payment_link (6 vars)."""
    return _send_template(
        "payment_link",
        [customer_name, company_name, outstanding, pay_url, support_phone,
         company_address or company_name],
        phone, company_id=company_id,
    )


def send_payment_receipt(
    phone: str, customer_name: str, transaction_no: str, amount: str,
    receipt_url: str, company_name: str, company_address: str = "",
    *, payment_id: Optional[int] = None, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Template 5 — payment_receipt (6 vars)."""
    return _send_template(
        "payment_receipt",
        [customer_name, transaction_no, amount, receipt_url, company_name,
         company_address or company_name],
        phone, company_id=company_id, linked_payment_id=payment_id,
    )


def send_wifi_recovery_link(
    phone: str, customer_name: str, recovery_url: str, company_name: str,
    company_address: str = "", *, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Template 6 — wifi_recovery_link (4 vars)."""
    return _send_template(
        "wifi_recovery_link",
        [customer_name, recovery_url, company_name,
         company_address or company_name],
        phone, company_id=company_id,
    )


def send_olt_critical_alert(
    phone: str, company_name: str, alert_title: str, alert_details: str,
    company_address: str = "", *, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Template 7 — olt_critical_alert (4 vars). Sent to admin's wa_target."""
    return _send_template(
        "olt_critical_alert",
        [company_name, alert_title, alert_details,
         company_address or company_name],
        phone, company_id=company_id,
    )


def send_sublco_commission_payout(
    phone: str, sublco_name: str, customer_name: str, base_amount: str,
    commission_pct: str, commission_amount: str, status: str,
    receipt_url: str, company_name: str, company_address: str = "",
    *, company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Template 8 — sublco_commission_payout (9 vars)."""
    return _send_template(
        "sublco_commission_payout",
        [sublco_name, customer_name, base_amount, commission_pct,
         commission_amount, status, receipt_url, company_name,
         company_address or company_name],
        phone, company_id=company_id,
    )


# CLI quick-test:
#   python3 msg91_whatsapp.py invoice 919876543210
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 3:
        print("usage: msg91_whatsapp.py {invoice|payment|expiry} <phone>")
        sys.exit(1)
    kind, ph = sys.argv[1], sys.argv[2]
    if kind == "invoice":
        r = send_invoice_generated(ph, "Test User", "INV-TEST-001", "599",
                                    "31-May-2026", "+918085868114",
                                    "CITY WIFI", invoice_id=0,
                                    company_id="14150129")
    elif kind == "payment":
        r = send_payment_received(ph, "Test User", "599", "12-May-2026",
                                   "RCPT-TEST-001", "10-Jun-2026",
                                   "CITY WIFI", payment_id=0,
                                   company_id="14150129")
    elif kind == "expiry":
        r = send_plan_expiring_soon(ph, "Test User", "Premium 100Mbps",
                                     "15-May-2026", "599",
                                     "+918085868114", "CITY WIFI",
                                     company_id="14150129")
    else:
        print("unknown kind"); sys.exit(2)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
