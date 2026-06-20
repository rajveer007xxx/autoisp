"""Phase 2.7 — Online Payment for Plan Upgrade (S44A)

Goal: after the customer hits "Pay & Upgrade" on /customer/change-plan,
they should be sent through the same multi-tenant payment-gateway stack
that powers /pay/{customer_id} — supporting Razorpay, PayU, CCAvenue,
Cashfree, PhonePe and Stripe — paying the merged amount
(plan_amount + previous_dues) and having the plan applied on success.

If the ISP has no gateway configured AND no env Razorpay, /api/customer/
upgrade/create returns the existing ticket-only response.
"""
from __future__ import annotations
import os, time, json, urllib.parse
from datetime import datetime, timezone

from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session


def _is_customer(req):
    return (req.session.get("user_type") or "").lower() == "customer"


def _tenant_default_gateway(db, company_id: str):
    """(name, is_default) of the tenant's default gateway, or None."""
    try:
        from payments.routes_admin import get_default_gateway
        return get_default_gateway(db, company_id)
    except Exception:
        return None


def _tenant_has_gateway(db, company_id: str) -> bool:
    return bool(_tenant_default_gateway(db, company_id))


def _env_razorpay_available() -> bool:
    return bool(os.environ.get("RAZORPAY_KEY_ID") and
                os.environ.get("RAZORPAY_KEY_SECRET"))


def _company_online_pay_enabled(db, company_id: str) -> bool:
    """The legacy enable_online_payment flag (per-company), needed when
    relying on env-fallback Razorpay (no tenant payment_gateways row)."""
    try:
        row = db.execute(sa_text(
            "SELECT COALESCE(enable_online_payment,0) FROM companies "
            "WHERE company_id=:c"), {"c": company_id}).fetchone()
        return bool(int((row or [0])[0] or 0))
    except Exception:
        return False


def _tenant_available_gateways(db, company_id: str):
    """List of gateways configured for this tenant. Mirrors the logic in
    main.py / pay_suspension_page so the upgrade-checkout shows the same
    options the customer sees on /pay/{customer_id}."""
    from payments.registry import GATEWAYS as _SCHEMAS
    out = []
    default_name = None
    try:
        rows = db.execute(sa_text(
            "SELECT gateway_name, is_default FROM payment_gateways "
            "WHERE company_id=:c AND status='Active'"
        ), {"c": company_id}).fetchall()
        for r in rows:
            gw = (r[0] or "").lower()
            schema = _SCHEMAS.get(gw)
            if not schema or not schema.get("active"):
                continue
            out.append({"name": gw, "label": schema["label"],
                        "default": bool(r[1])})
            if int(r[1] or 0): default_name = gw
        if not default_name and out:
            default_name = out[0]["name"]
    except Exception:
        pass
    # Env-fallback Razorpay only when tenant has nothing AND the
    # legacy per-company flag is on.
    if not out and _env_razorpay_available() and _company_online_pay_enabled(db, company_id):
        out.append({"name": "razorpay", "label": "Razorpay", "default": True})
        default_name = "razorpay"
    return out, default_name


def _apply_upgrade(db, oid: int) -> dict:
    """Apply a pending plan_change_orders row: update customer's plan_id,
    mark order applied. Idempotent. Returns {ok, message?}."""
    row = db.execute(sa_text(
        "SELECT id, company_id, customer_id, target_plan_id, status, amount_due "
        "FROM plan_change_orders WHERE id=:i"
    ), {"i": oid}).fetchone()
    if not row:
        return {"ok": False, "message": "order_not_found"}
    if (row[4] or "") == "applied":
        return {"ok": True, "already": True}
    db.execute(sa_text(
        "UPDATE customers SET plan_id=:p, last_renewal_date=date('now') "
        "WHERE customer_id=:cu AND company_id=:c"
    ), {"p": row[3], "cu": row[2], "c": row[1]})
    db.execute(sa_text(
        "UPDATE plan_change_orders "
        "  SET status='applied', applied_at=datetime('now') "
        "WHERE id=:i"
    ), {"i": oid})
    db.commit()
    return {"ok": True}


def apply_pending_upgrade_for_customer(db, company_id: str, customer_id: str,
                                        amount_paid: float | None = None) -> dict:
    """Hook called from routes_pay._post_success_chain. Finds the most
    recently created pending plan_change_orders for this customer and
    applies it. If `amount_paid` is given, prefers the order whose
    amount_due is within 1 INR of the paid amount."""
    try:
        rows = db.execute(sa_text(
            "SELECT id, amount_due FROM plan_change_orders "
            "WHERE customer_id=:cu AND company_id=:c AND status='pending' "
            "ORDER BY id DESC LIMIT 5"
        ), {"cu": customer_id, "c": company_id}).fetchall()
    except Exception:
        return {"ok": False, "message": "lookup_failed"}
    if not rows:
        return {"ok": False, "no_pending": True}
    target = None
    if amount_paid is not None:
        for r in rows:
            if abs(float(r[1] or 0) - float(amount_paid)) < 1.0:
                target = r[0]; break
    if not target:
        target = rows[0][0]
    return _apply_upgrade(db, target)


# ─────────────────────────────────────────────────────────────────────────

def register(app, templates, get_db):
    # ── GET /customer/upgrade/{oid}/checkout ───────────────────────────
    @app.get("/customer/upgrade/{oid}/checkout", response_class=HTMLResponse)
    async def customer_upgrade_checkout(oid: int, request: Request,
                                         db: Session = Depends(get_db)):
        if not _is_customer(request):
            return RedirectResponse("/login", 302)
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        row = db.execute(sa_text(
            "SELECT pco.id, pco.amount_due, pco.target_plan_id, pco.status, "
            "       p.plan_name "
            "FROM plan_change_orders pco "
            "LEFT JOIN plans p ON p.id = pco.target_plan_id "
            "                  AND p.company_id = pco.company_id "
            "WHERE pco.id=:i AND pco.company_id=:c AND pco.customer_id=:cu"
        ), {"i": oid, "c": company_id, "cu": cust_id}).fetchone()
        if not row:
            return HTMLResponse("Order not found.", status_code=404)
        if (row[3] or "") == "applied":
            # already done — push to dashboard
            return RedirectResponse("/customer/dashboard", 303)
        # Re-derive plan amount + previous dues for the breakdown.
        from database import Customer, Plan, Company
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()
        plan = db.query(Plan).filter(Plan.id == row[2],
                                      Plan.company_id == company_id).first()
        plan_amt = float(plan.after_tax_amount or 0) if plan else 0.0
        total = float(row[1] or 0)
        prev_due = max(0.0, round(total - plan_amt, 2))
        company = db.query(Company).filter(Company.company_id == company_id).first()

        # __S43ZS__-style gateway list
        gateways, default_gw = _tenant_available_gateways(db, company_id)

        ctx = {
            "request": request,
            "active_page": "change_plan",
            "order_id": oid,
            "plan_name": (plan.plan_name if plan else row[4] or "-"),
            "plan_amount": plan_amt,
            "previous_dues": prev_due,
            "total_amount": total,
            "available_gateways": gateways,
            "default_gateway": default_gw,
            "customer": cust,
            "customer_name": (cust.customer_name if cust else "Customer"),
            "company": company,
            "company_id": company_id,
            "company_name": (company.company_name if company else "ISP"),
        }
        return templates.TemplateResponse("customer_upgrade_checkout.html", ctx)

    # ── POST /api/customer/upgrade/{oid}/start/{gateway} ────────────────
    @app.post("/api/customer/upgrade/{oid}/start/{gateway}")
    async def customer_upgrade_start(oid: int, gateway: str, request: Request,
                                       db: Session = Depends(get_db)):
        if not _is_customer(request):
            return {"success": False, "message": "Unauthorized"}
        gateway = (gateway or "").lower()
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        row = db.execute(sa_text(
            "SELECT id, amount_due, target_plan_id, status "
            "FROM plan_change_orders "
            "WHERE id=:i AND company_id=:c AND customer_id=:cu"
        ), {"i": oid, "c": company_id, "cu": cust_id}).fetchone()
        if not row:
            return {"success": False, "message": "order_not_found"}
        if (row[3] or "") == "applied":
            return {"success": False, "message": "Already applied"}
        amt = float(row[1] or 0)
        if amt <= 0:
            return {"success": False, "message": "invalid amount"}

        from database import Customer
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()

        # Resolve credentials — prefer tenant row, env-fallback only for razorpay.
        creds = None
        try:
            from payments.routes_admin import get_company_credentials
            creds = get_company_credentials(db, company_id, gateway)
        except Exception:
            creds = None
        if not creds and gateway == "razorpay" and _env_razorpay_available() \
                and _company_online_pay_enabled(db, company_id):
            creds = {
                "key_id": os.environ["RAZORPAY_KEY_ID"],
                "key_secret": os.environ["RAZORPAY_KEY_SECRET"],
                "webhook_secret": os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""),
                "merchant_id": "", "extra_config": "",
            }
        if not creds:
            return {"success": False,
                    "message": f"{gateway} is not configured for this ISP."}

        from payments.adapters import make as make_adapter
        adapter = make_adapter(gateway, creds)
        if not adapter:
            return {"success": False, "message": "Unknown gateway"}

        notes = {
            "customer_id": cust_id,
            "company_id": company_id,
            "purpose": "plan_upgrade",
            "upgrade_order_id": str(oid),
            "firstname": (getattr(cust, "customer_name", None) or "Customer")[:60],
            "email":     (getattr(cust, "customer_email", None) or "noreply@autoispbilling.com")[:80],
            "productinfo": f"Plan Upgrade #{oid}",
        }
        try:
            out = adapter.create_order(amount_inr=amt,
                                        customer_id=cust_id,
                                        company_id=company_id,
                                        notes=notes)
        except Exception as e:
            return {"success": False, "message": f"gateway error: {str(e)[:200]}"}

        if not out.get("ok"):
            return {"success": False,
                    "message": out.get("message") or "gateway create-order failed"}

        # Persist gateway order id on plan_change_orders for traceability.
        db.execute(sa_text(
            "UPDATE plan_change_orders "
            "  SET rzp_order_id=:r "
            "WHERE id=:i"
        ), {"r": str(out.get("order_id") or "")[:80], "i": oid})
        db.commit()

        # Per-gateway response shaping for the frontend.
        if gateway == "razorpay":
            return {"success": True, "gateway": "razorpay",
                    "key_id": creds["key_id"],
                    "order_id": out["order_id"],
                    "amount": out["amount"],   # paise
                    "amount_inr": amt,
                    "currency": "INR"}
        if gateway == "payu":
            base = f"{request.url.scheme}://{request.url.netloc}"
            fields = dict(out.get("fields") or {})
            fields["surl"] = f"{base}/api/pay/payu/return?status=success"
            fields["furl"] = f"{base}/api/pay/payu/return?status=failure"
            # PayU has no metadata channel for upgrade_order_id; we'll rely
            # on amount-match in the post-success scan.
            return {"success": True, "gateway": "payu",
                    "post_url": out["post_url"],
                    "fields": fields,
                    "amount_inr": amt}
        if gateway == "cashfree":
            return {"success": True, "gateway": "cashfree",
                    "redirect_url": out.get("payment_link") or out.get("redirect_url") or "",
                    "order_id": out.get("order_id") or "",
                    "amount_inr": amt}
        if gateway == "phonepe":
            return {"success": True, "gateway": "phonepe",
                    "redirect_url": out.get("redirect_url") or "",
                    "order_id": out.get("order_id") or "",
                    "amount_inr": amt}
        if gateway == "ccavenue":
            return {"success": True, "gateway": "ccavenue",
                    "post_url": out.get("post_url") or "",
                    "fields": out.get("fields") or {},
                    "amount_inr": amt}
        if gateway == "stripe":
            return {"success": True, "gateway": "stripe",
                    "redirect_url": out.get("redirect_url")
                                    or out.get("session_url") or "",
                    "session_id": out.get("session_id") or "",
                    "amount_inr": amt}
        return {"success": True, "gateway": gateway, "raw": out,
                "amount_inr": amt}

    # ── POST /api/customer/upgrade/{oid}/confirm-razorpay ───────────────
    # Browser-side verify for Razorpay popup. PayU/CCAvenue/Cashfree/
    # PhonePe/Stripe each return server-side via their existing handlers
    # in payments/routes_pay.py + our _post_success_chain hook below.
    @app.post("/api/customer/upgrade/{oid}/confirm-razorpay")
    async def customer_upgrade_confirm_razorpay(oid: int, request: Request,
                                                 db: Session = Depends(get_db)):
        if not _is_customer(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        try:
            body = await request.json()
        except Exception:
            body = dict((await request.form()).items())
        order_id   = (body.get("razorpay_order_id") or "").strip()
        payment_id = (body.get("razorpay_payment_id") or "").strip()
        signature  = (body.get("razorpay_signature") or "").strip()
        if not (order_id and payment_id and signature):
            return {"success": False, "message": "missing razorpay fields"}
        row = db.execute(sa_text(
            "SELECT id, amount_due, status FROM plan_change_orders "
            "WHERE id=:i AND company_id=:c AND customer_id=:cu"
        ), {"i": oid, "c": company_id, "cu": cust_id}).fetchone()
        if not row:
            return {"success": False, "message": "order_not_found"}
        if (row[2] or "") == "applied":
            return {"success": True, "already": True}
        amt = float(row[1] or 0)
        # Resolve credentials (tenant → env)
        try:
            from payments.routes_admin import get_company_credentials
            creds = get_company_credentials(db, company_id, "razorpay") or {}
        except Exception:
            creds = {}
        key_id = (creds.get("key_id") or os.environ.get("RAZORPAY_KEY_ID") or "")
        key_secret = (creds.get("key_secret") or os.environ.get("RAZORPAY_KEY_SECRET") or "")
        if not key_id or not key_secret:
            return {"success": False, "message": "Razorpay not configured"}
        try:
            import razorpay
            cl = razorpay.Client(auth=(key_id, key_secret))
            cl.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
        except Exception as e:
            return {"success": False, "message": f"signature invalid: {e}"}
        # Record Payment row (idempotent on transaction_no)
        from database import Customer, Payment
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()
        already = db.query(Payment).filter(Payment.transaction_no == payment_id).first()
        if not already and cust:
            try:
                p = Payment(customer_id=cust.customer_id,
                             company_id=cust.company_id,
                             amount=amt,
                             payment_mode="Razorpay",
                             transaction_no=payment_id,
                             paid_at=datetime.utcnow(),
                             remarks=f"Plan upgrade {oid} (Razorpay order {order_id})",
                             created_at=datetime.utcnow())
                db.add(p); db.commit()
            except Exception:
                db.rollback()
        # Apply the upgrade
        applied = _apply_upgrade(db, oid)
        # Reactivate + RADIUS resync (same chain as /pay/{cid})
        try:
            from main import _enforce_user_state
            if cust:
                if (cust.status or "") != "Active":
                    cust.status = "Active"; cust.is_suspended = False; db.commit()
                _enforce_user_state(db, cust)
        except Exception as e:
            print(f"[upgrade-razorpay-verify] enforce skip: {e}")
        return {"success": True, "applied": applied.get("ok", False),
                "message": "Plan upgraded successfully."}

    print("[phase27_upgrade_pay] registered: /customer/upgrade/{oid}/checkout, "
          "/api/customer/upgrade/{oid}/start/{gateway}, "
          "/api/customer/upgrade/{oid}/confirm-razorpay")
