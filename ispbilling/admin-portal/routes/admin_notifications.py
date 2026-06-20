"""routes/admin_notifications.py — s36g refactor.

Extracted from main.py. All helpers are injected into this module's
`globals()` by `register()` BEFORE the @router decorators execute, so
things like `Depends(get_db)` resolve correctly.
"""
from __future__ import annotations
from fastapi import APIRouter
router = APIRouter(tags=["admin-notifications"])


def register(app, **deps):
    # Inject every shared helper into module globals so bare names inside
    # the routes (Customer, text, _s351_last_seen, datetime, timedelta, …)
    # resolve at call-time, and `Depends(get_db)` at def-time sees get_db.
    g = globals()
    g.update(deps)

    # === Session 35 notifications feed ===
    @router.get("/api/admin/notifications/feed")
    async def api_notifications_feed(request: Request,
                                     limit: int = 20,
                                     days: int = 30,
                                     db: Session = Depends(get_db)):
        """Aggregate recent events across Payments, Complaints, Support, Invoices,
        Customers into a single newest-first feed."""
        auth_check = require_admin(request)
        if auth_check: return auth_check
        company_id = request.session.get("company_id")
        since = datetime.now() - timedelta(days=max(1, int(days or 30)))
        items = []

        # -- Payments received
        try:
            from database import Payment, Customer
            rows = db.query(Payment, Customer).outerjoin(
                Customer,
                (Payment.customer_id == Customer.customer_id) & (Customer.company_id == company_id)
            ).filter(
                Payment.company_id == company_id,
                Payment.paid_at >= since,
            ).order_by(Payment.paid_at.desc()).limit(limit * 3).all()
            for p, c in rows:
                cname = c.customer_name if c else p.customer_id
                items.append({
                    "type": "payment",
                    "icon": "fa-money",
                    "color": "#10b981",
                    "title": f"Payment of \u20B9{float(p.amount or 0):.0f} received",
                    "subtitle": f"{cname or p.customer_id or '-'} \u00B7 {p.payment_mode or p.txn_mode or 'Cash'} \u00B7 {p.transaction_no or ''}",
                    "url": "/admin/transactions",
                    "ts": (p.paid_at or p.created_at or since).isoformat() if p.paid_at else since.isoformat(),
                })
        except Exception as e:
            print(f"[S35 feed payments] {e}")

        # -- Complaints
        try:
            comp = db.execute(text(
                "SELECT id, customer_id, ticket_no, subject, priority, status, created_at "
                "FROM complaints WHERE company_id = :cid AND created_at >= :since "
                "ORDER BY created_at DESC LIMIT :lim"
            ), {"cid": company_id, "since": since, "lim": limit * 3}).fetchall()
            for r in comp:
                items.append({
                    "type": "complaint",
                    "icon": "fa-exclamation-circle",
                    "color": "#ef4444",
                    "title": f"Complaint: {r[3] or 'New ticket'}",
                    "subtitle": f"{r[2] or ''} \u00B7 {(r[4] or 'Normal').title()} \u00B7 {(r[5] or 'Open').title()}",
                    "url": "/admin/complaints",
                    "ts": (r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6])),
                })
        except Exception as e:
            print(f"[S35 feed complaints] {e}")

        # -- Support tickets (admins raising issues to superadmin)
        try:
            sup = db.execute(text(
                "SELECT id, ticket_no, subject, priority, status, created_at "
                "FROM support_tickets WHERE company_id = :cid AND created_at >= :since "
                "ORDER BY created_at DESC LIMIT :lim"
            ), {"cid": company_id, "since": since, "lim": limit * 3}).fetchall()
            for r in sup:
                items.append({
                    "type": "support",
                    "icon": "fa-life-ring",
                    "color": "#6366f1",
                    "title": f"Support Ticket: {r[2] or 'New ticket'}",
                    "subtitle": f"{r[1] or ''} \u00B7 {(r[3] or 'Normal').title()} \u00B7 {(r[4] or 'Open').title()}",
                    "url": "/admin/support",
                    "ts": (r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5])),
                })
        except Exception as e:
            print(f"[S35 feed support] {e}")

        # -- New customers
        try:
            from database import Customer as _Cust
            custs = db.query(_Cust).filter(
                _Cust.company_id == company_id,
                _Cust.created_at >= since,
            ).order_by(_Cust.created_at.desc()).limit(limit * 3).all()
            for c in custs:
                items.append({
                    "type": "customer",
                    "icon": "fa-user-plus",
                    "color": "#3b82f6",
                    "title": f"New customer: {c.customer_name or c.customer_id}",
                    "subtitle": f"{c.customer_id or ''} \u00B7 {c.customer_phone or ''} \u00B7 {c.service_type or ''}",
                    "url": f"/admin/subscribers/{c.id}",
                    "ts": (c.created_at or since).isoformat(),
                })
        except Exception as e:
            print(f"[S35 feed customers] {e}")

        # -- Invoices generated
        try:
            from database import Invoice, Customer as _C2
            rows = db.query(Invoice, _C2).outerjoin(
                _C2,
                (Invoice.customer_id == _C2.customer_id) & (_C2.company_id == company_id)
            ).filter(
                Invoice.company_id == company_id,
                Invoice.created_at >= since,
            ).order_by(Invoice.created_at.desc()).limit(limit * 3).all()
            for inv, cust in rows:
                cname = cust.customer_name if cust else inv.customer_id
                items.append({
                    "type": "invoice",
                    "icon": "fa-file-text",
                    "color": "#0ea5e9",
                    "title": f"Invoice {inv.invoice_no} generated",
                    "subtitle": f"{cname or ''} \u00B7 \u20B9{float(inv.total_amount or 0):.0f}",
                    "url": "/admin/send-invoices",
                    "ts": (inv.created_at or since).isoformat(),
                })
        except Exception as e:
            print(f"[S35 feed invoices] {e}")

        # Sort newest first & cap
        items.sort(key=lambda x: x.get("ts") or "", reverse=True)
        items = items[: int(limit or 20)]

        # Human "ago" helper
        now_ts = datetime.now()
        def _ago(ts_str):
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "").split(".")[0])
                d = now_ts - ts
                s = int(d.total_seconds())
                if s < 60: return f"{s}s ago"
                if s < 3600: return f"{s//60}m ago"
                if s < 86400: return f"{s//3600}h ago"
                return f"{s//86400}d ago"
            except Exception:
                return ""
        # S35.1 — mark items newer than per-admin last_seen as "new"
        try:
            admin_id = str(request.session.get("user_id") or request.session.get("admin_id") or "")
            last_seen = _s351_last_seen(db, company_id, admin_id)
        except Exception:
            last_seen = datetime.min
        unread = 0
        # _S57C_NOTIF_NAIVE_  Guarantee tz-naive comparison on both sides.
        try:
            if getattr(last_seen, 'tzinfo', None) is not None:
                last_seen = last_seen.replace(tzinfo=None)
        except Exception:
            pass
        for it in items:
            it["ago"] = _ago(it.get("ts", ""))
            try:
                ts = datetime.fromisoformat(it["ts"].replace("Z","").split(".")[0])
                if getattr(ts, 'tzinfo', None) is not None:
                    ts = ts.replace(tzinfo=None)
            except Exception:
                ts = datetime.min
            try:
                it["is_new"] = ts > last_seen
            except TypeError:
                it["is_new"] = False
            if it["is_new"]: unread += 1

        return {"success": True, "count": len(items), "unread": unread,
                "last_seen_at": last_seen.isoformat() if last_seen != datetime.min else None,
                "items": items}


    @router.get("/api/admin/notifications/summary")
    async def api_notifications_summary(request: Request, db: Session = Depends(get_db)):
        """Counts for the 4 stat cards on the Notifications page."""
        auth_check = require_admin(request)
        if auth_check: return auth_check
        company_id = request.session.get("company_id")
        since = datetime.now() - timedelta(days=30)
        out = {"payments": 0, "complaints": 0, "support": 0, "invoices": 0, "customers": 0}
        try:
            from database import Payment, Invoice, Customer as _C
            out["payments"] = db.query(Payment).filter(
                Payment.company_id == company_id, Payment.paid_at >= since).count()
            out["invoices"] = db.query(Invoice).filter(
                Invoice.company_id == company_id, Invoice.created_at >= since).count()
            out["customers"] = db.query(_C).filter(
                _C.company_id == company_id, _C.created_at >= since).count()
        except Exception: pass
        try:
            out["complaints"] = int(db.execute(text(
                "SELECT COUNT(*) FROM complaints WHERE company_id = :c AND created_at >= :s"
            ), {"c": company_id, "s": since}).scalar() or 0)
        except Exception: pass
        try:
            out["support"] = int(db.execute(text(
                "SELECT COUNT(*) FROM support_tickets WHERE company_id = :c AND created_at >= :s"
            ), {"c": company_id, "s": since}).scalar() or 0)
        except Exception: pass
        out["total"] = sum(out.values())
        return {"success": True, **out}
    @router.post("/api/admin/notifications/mark-read")
    async def api_notifications_mark_read(request: Request, db: Session = Depends(get_db)):
        auth_check = require_admin(request)
        if auth_check: return auth_check
        company_id = request.session.get("company_id")
        admin_id   = str(request.session.get("user_id") or request.session.get("admin_id") or "")
        now = datetime.now()
        try:
            # upsert
            db.execute(text("""
                INSERT INTO admin_notification_state(company_id, admin_id, last_seen_at)
                VALUES (:c,:a,:t)
                ON CONFLICT(company_id, admin_id) DO UPDATE SET last_seen_at = :t
            """), {"c": company_id, "a": admin_id, "t": now})
            db.commit()
            return {"success": True, "last_seen_at": now.isoformat()}
        except Exception as e:
            db.rollback()
            return JSONResponse({"success": False, "message": str(e)}, status_code=500)


    app.include_router(router)
