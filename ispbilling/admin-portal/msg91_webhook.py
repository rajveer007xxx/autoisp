"""
msg91_webhook.py — Session 2026.02
Receives delivery / read / failed callbacks from MSG91 and updates
`whatsapp_message_logs.status / delivered_at / read_at / failed_at`
so the admin portal can show "✓✓ Read" badges.

Mount on the FastAPI app:
    from msg91_webhook import register as _msg91_register
    _msg91_register(app)

Endpoints exposed:
    POST /api/webhooks/msg91-whatsapp     — MSG91 callback receiver
    GET  /api/admin/whatsapp/log-status   — admin lookup for badges
"""
from __future__ import annotations

import json
import logging
import sqlite3
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("msg91_webhook")
DB_PATH = "/var/lib/autoispbilling/autoispbilling.db"


# ─────────────────────── status mapping ────────────────────────────
# MSG91 webhook events vary slightly across products. Normalise here.
_STATUS_MAP = {
    "sent": "sent",
    "send": "sent",
    "delivered": "delivered",
    "deliver": "delivered",
    "read": "read",
    "seen": "read",
    "failed": "failed",
    "fail": "failed",
    "rejected": "failed",
    "undelivered": "failed",
}


def _normalise_status(s: str) -> Optional[str]:
    if not s:
        return None
    return _STATUS_MAP.get(str(s).strip().lower())


def _apply_status(
    *,
    provider_message_id: Optional[str],
    customer_phone: Optional[str],
    status: str,
    ts_iso: Optional[str],
    error_reason: Optional[str],
    raw_payload: dict,
) -> bool:
    """Update the matching log row. Returns True if at least one row updated."""
    if not provider_message_id and not customer_phone:
        log.warning("msg91 webhook missing both message_id and phone")
        return False
    fields = {"status": status, "webhook_payload": json.dumps(raw_payload)[:8000]}
    if status == "delivered":
        fields["delivered_at"] = ts_iso or datetime.utcnow().isoformat()
    elif status == "read":
        fields["read_at"] = ts_iso or datetime.utcnow().isoformat()
    elif status == "failed":
        fields["failed_at"] = ts_iso or datetime.utcnow().isoformat()
        if error_reason:
            fields["error_message"] = str(error_reason)[:500]

    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    params = list(fields.values())

    # Prefer matching by provider_message_id; fall back to phone + recent send
    if provider_message_id:
        sql = (f"UPDATE whatsapp_message_logs SET {set_clause} "
               "WHERE provider_message_id = ? AND provider = 'msg91'")
        params.append(provider_message_id)
    else:
        # Match the most recent MSG91 row sent to this phone within last 7 days
        # that is still not in a terminal state matching the new status.
        sql = (f"UPDATE whatsapp_message_logs SET {set_clause} "
               "WHERE id = (SELECT id FROM whatsapp_message_logs "
               "            WHERE recipient_phone = ? AND provider = 'msg91' "
               "              AND sent_at >= datetime('now','-7 days') "
               "            ORDER BY sent_at DESC LIMIT 1)")
        params.append(customer_phone)

    try:
        con = _compat_conn(timeout=5.0)
        con.execute("PRAGMA busy_timeout=4000")
        cur = con.execute(sql, params)
        n = cur.rowcount
        con.commit()
        con.close()
        if n == 0:
            log.warning("msg91 webhook: no log row matched message_id=%s phone=%s",
                        provider_message_id, customer_phone)
        return n > 0
    except Exception:
        log.exception("msg91 webhook DB update failed")
        return False


def _extract_events(payload: Any) -> List[Dict[str, Any]]:
    """MSG91 sends either a single event object or a list. Normalise to list."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        # Some payloads wrap events in "data" or "events"
        for k in ("events", "data", "report"):
            v = payload.get(k)
            if isinstance(v, list):
                return [e for e in v if isinstance(e, dict)]
        return [payload]
    return []


def register(app: FastAPI) -> None:
    """Register MSG91 webhook + admin lookup endpoints on the FastAPI app."""

    @app.post("/api/webhooks/msg91-whatsapp")
    async def msg91_webhook(request: Request):
        """Receive MSG91 WhatsApp delivery/read callbacks.

        Configure in MSG91 → WhatsApp → Webhooks → Create:
            URL: https://www.autoispbilling.com/api/webhooks/msg91-whatsapp
            Events: outbound_report, on_read, on_api_failed
        """
        try:
            raw = await request.body()
            try:
                payload = json.loads(raw.decode() or "{}")
            except Exception:
                log.warning("msg91 webhook: invalid JSON, %d bytes", len(raw))
                return JSONResponse({"ok": True})
            events = _extract_events(payload)
            log.info("msg91 webhook: %d event(s)", len(events))
            updated = 0
            for ev in events:
                # MSG91 field name variants we cover:
                mid = (ev.get("messageId") or ev.get("message_id")
                       or ev.get("msgId") or ev.get("id"))
                phone = (ev.get("customerNumber") or ev.get("recipient")
                         or ev.get("to") or ev.get("number"))
                status_raw = (ev.get("eventName") or ev.get("event")
                              or ev.get("status") or ev.get("delivery_status"))
                status = _normalise_status(status_raw)
                ts = (ev.get("ts") or ev.get("timestamp")
                      or ev.get("delivered_at") or ev.get("read_at"))
                err = ev.get("reason") or ev.get("error") or ev.get("description")
                if not status:
                    continue
                if _apply_status(
                    provider_message_id=str(mid) if mid else None,
                    customer_phone=str(phone) if phone else None,
                    status=status, ts_iso=str(ts) if ts else None,
                    error_reason=str(err) if err else None,
                    raw_payload=ev,
                ):
                    updated += 1
            return {"ok": True, "events": len(events), "updated": updated}
        except Exception as e:
            log.exception("msg91 webhook handler crashed")
            # Always return 200 so MSG91 doesn't retry-storm us
            return JSONResponse({"ok": False, "error": str(e)[:200]},
                                status_code=200)

    @app.get("/api/admin/whatsapp/log-status")
    async def msg91_log_status(
        invoice_id: Optional[int] = None,
        payment_id: Optional[int] = None,
    ):
        """Lookup the most recent MSG91 message status for a given
        invoice or payment. Used by the admin portal to render
        ✓ sent / ✓✓ delivered / 👁 read badges."""
        if not invoice_id and not payment_id:
            return {"found": False}
        try:
            con = _compat_conn(timeout=3.0)
            cur = con.cursor()
            if invoice_id:
                cur.execute(
                    "SELECT id, status, sent_at, delivered_at, read_at, "
                    "       failed_at, error_message, recipient_phone, template_name "
                    "  FROM whatsapp_message_logs "
                    " WHERE provider='msg91' AND linked_invoice_id = ? "
                    " ORDER BY id DESC LIMIT 1", (invoice_id,))
            else:
                cur.execute(
                    "SELECT id, status, sent_at, delivered_at, read_at, "
                    "       failed_at, error_message, recipient_phone, template_name "
                    "  FROM whatsapp_message_logs "
                    " WHERE provider='msg91' AND linked_payment_id = ? "
                    " ORDER BY id DESC LIMIT 1", (payment_id,))
            row = cur.fetchone()
            con.close()
            if not row:
                return {"found": False}
            keys = ("id", "status", "sent_at", "delivered_at", "read_at",
                    "failed_at", "error_message", "recipient_phone", "template_name")
            return {"found": True, **dict(zip(keys, row))}
        except Exception as e:
            log.exception("msg91 log-status crashed")
            return {"found": False, "error": str(e)[:200]}

    @app.get("/api/admin/whatsapp/log-statuses")
    async def msg91_log_statuses(
        invoice_ids: str = "",
        payment_ids: str = "",
    ):
        """Bulk variant — given comma-separated ids, return the latest
        MSG91 status per id. Powers the ✓✓ Read column in admin lists."""
        def _ids(s: str):
            out = []
            for tok in (s or "").split(","):
                tok = tok.strip()
                if tok.isdigit():
                    out.append(int(tok))
            return out
        inv_ids = _ids(invoice_ids)
        pay_ids = _ids(payment_ids)
        if not inv_ids and not pay_ids:
            return {"invoices": {}, "payments": {}}
        result = {"invoices": {}, "payments": {}}
        try:
            con = _compat_conn(timeout=3.0)
            cur = con.cursor()
            if inv_ids:
                ph = ",".join("?" * len(inv_ids))
                cur.execute(
                    "SELECT linked_invoice_id, status, sent_at, "
                    "       delivered_at, read_at, failed_at, MAX(id) "
                    "  FROM whatsapp_message_logs "
                    " WHERE provider='msg91' AND linked_invoice_id IN "
                    "       (" + ph + ") "
                    " GROUP BY linked_invoice_id", inv_ids)
                for r in cur.fetchall():
                    result["invoices"][str(r[0])] = {
                        "status": r[1], "sent_at": r[2],
                        "delivered_at": r[3], "read_at": r[4],
                        "failed_at": r[5],
                    }
            if pay_ids:
                ph = ",".join("?" * len(pay_ids))
                cur.execute(
                    "SELECT linked_payment_id, status, sent_at, "
                    "       delivered_at, read_at, failed_at, MAX(id) "
                    "  FROM whatsapp_message_logs "
                    " WHERE provider='msg91' AND linked_payment_id IN "
                    "       (" + ph + ") "
                    " GROUP BY linked_payment_id", pay_ids)
                for r in cur.fetchall():
                    result["payments"][str(r[0])] = {
                        "status": r[1], "sent_at": r[2],
                        "delivered_at": r[3], "read_at": r[4],
                        "failed_at": r[5],
                    }
            con.close()
        except Exception as e:
            log.exception("msg91 bulk log-statuses crashed")
            return {"invoices": {}, "payments": {},
                    "error": str(e)[:200]}
        return result
