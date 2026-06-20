"""routes/vouchers.py — s36c refactor."""
from __future__ import annotations
import io
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from sqlalchemy.orm import Session

router = APIRouter(tags=["vouchers"])



# ============================================================================
# __radius_provision_voucher__ — write a voucher's code into the FreeRADIUS
# sqlite backend so that Mikrotik's PAP auth (username=password=<code>)
# succeeds. The SQL module is enabled in sites-enabled/default and reads
# /var/lib/freeradius/radacct.db (radcheck/radreply tables). No FreeRADIUS
# restart is required — the SQL module is queried per Access-Request.
# ============================================================================
import sqlite3 as _voucher_sqlite3, os as _voucher_os
_FREERADIUS_DB = "/var/lib/freeradius/radacct.db"


def _radius_provision_voucher(v):
    """Idempotently install <code> as a Hotspot-authable user in FreeRADIUS.
    `v` is a HotspotVoucher row (must have .code, .duration_minutes)."""
    if not _voucher_os.path.exists(_FREERADIUS_DB):
        print(f"[voucher-radius] FreeRADIUS DB missing at {_FREERADIUS_DB}")
        return False
    try:
        con = _voucher_sqlite3.connect(_FREERADIUS_DB, timeout=5)
        try:
            con.execute("PRAGMA busy_timeout = 3000")
            cur = con.cursor()
            uname = v.code
            cur.execute("DELETE FROM radcheck WHERE username = ?", (uname,))
            cur.execute(
                "INSERT INTO radcheck (username, attribute, op, value) "
                "VALUES (?, 'Cleartext-Password', ':=', ?)",
                (uname, uname),
            )
            cur.execute("DELETE FROM radreply WHERE username = ?", (uname,))
            replies = [
                (uname, 'Mikrotik-Group',         ':=', 'active-hotspot'),
                (uname, 'Service-Type',           ':=', 'Login-User'),
                (uname, 'Acct-Interim-Interval',  ':=', '60'),
            ]
            try:
                dur = int(getattr(v, 'duration_minutes', 0) or 0)
                if dur > 0:
                    replies.append((uname, 'Session-Timeout', ':=', str(dur * 60)))
            except Exception:
                pass
            cur.executemany(
                "INSERT INTO radreply (username, attribute, op, value) "
                "VALUES (?, ?, ?, ?)",
                replies,
            )
            con.commit()
            return True
        finally:
            con.close()
    except Exception as _e:
        print(f"[voucher-radius] provision failed: {_e}")
        return False


def _radius_revoke_voucher(code):
    """Used by future revoke endpoint — keeps FreeRADIUS in sync."""
    if not _voucher_os.path.exists(_FREERADIUS_DB):
        return
    try:
        con = _voucher_sqlite3.connect(_FREERADIUS_DB, timeout=5)
        cur = con.cursor()
        cur.execute("DELETE FROM radcheck WHERE username = ?", (code,))
        cur.execute("DELETE FROM radreply WHERE username = ?", (code,))
        con.commit(); con.close()
    except Exception as _e:
        print(f"[voucher-radius] revoke failed: {_e}")


def register(app, *, templates, require_admin, get_db, get_admin_context,
             log_admin_activity, gen_code):

    @router.get("/admin/vouchers", response_class=HTMLResponse)
    async def admin_vouchers_page(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        from database import HotspotVoucher, Plan
        company_id = request.session.get("company_id")
        plans = db.query(Plan).filter(Plan.company_id == company_id).all()
        vouchers = db.query(HotspotVoucher).filter(
            HotspotVoucher.company_id == company_id
        ).order_by(HotspotVoucher.id.desc()).limit(500).all()
        ctx = get_admin_context(request, db, "vouchers")
        ctx["plans"] = plans
        ctx["vouchers"] = vouchers
        return templates.TemplateResponse("admin_vouchers.html", ctx)

    @router.get("/api/vouchers/list")
    async def api_vouchers_list(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        # __voucher_list_redemption_sync__ — sync RADIUS redemptions inline
        # so the admin sees vouchers as used in real-time, not after 5 min cron.
        try:
            import sys as _sys
            if '/opt/ispbilling/scripts' not in _sys.path:
                _sys.path.insert(0, '/opt/ispbilling/scripts')
            from voucher_redemption_sync import sync_voucher_redemptions
            sync_voucher_redemptions()
            db.expire_all()
        except Exception as _vrx:
            print(f"[voucher list] redemption sync error: {_vrx}")
        from database import HotspotVoucher
        company_id = request.session.get("company_id", "N/A")
        rows = db.query(HotspotVoucher).filter(
            HotspotVoucher.company_id == company_id
        ).order_by(HotspotVoucher.id.desc()).limit(1000).all()
        return JSONResponse({"ok": True, "rows": [{
            "id": r.id, "batch_id": r.batch_id, "code": r.code,
            "plan_name": r.plan_name, "duration_minutes": r.duration_minutes,
            "data_cap_mb": r.data_cap_mb, "status": r.status,
            "used_by": r.used_by, "expires_at": str(r.expires_at) if r.expires_at else "",
            "created_at": str(r.created_at) if r.created_at else "",
        } for r in rows]})

    @router.post("/api/vouchers/generate")
    async def api_vouchers_generate(request: Request, background: BackgroundTasks,
                                     db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import HotspotVoucher, Plan, Company
        company_id = request.session.get("company_id", "N/A")
        body = await request.json()
        count = max(1, min(int(body.get("count") or 10), 500))
        plan_id = body.get("plan_id")
        duration_minutes = int(body.get("duration_minutes") or 0)
        data_cap_mb = int(body.get("data_cap_mb") or 0)
        expires_days = int(body.get("expires_days") or 0)
        batch_id = body.get("batch_id") or datetime.utcnow().strftime("BAT-%Y%m%d-%H%M%S")

        # s36c delivery
        delivery = (body.get("delivery") or "none").lower()
        phones = body.get("phones") or []
        if isinstance(phones, str):
            phones = [p.strip() for p in phones.split(",") if p.strip()]

        plan_name = None
        if plan_id:
            p = db.query(Plan).filter(Plan.id == plan_id,
                                       Plan.company_id == company_id).first()
            plan_name = p.plan_name if p else None
        exp_at = datetime.utcnow() + timedelta(days=expires_days) if expires_days > 0 else None

        created = []
        attempts = 0
        while len(created) < count and attempts < count * 5:
            attempts += 1
            code = gen_code(10)
            if db.query(HotspotVoucher).filter(HotspotVoucher.code == code).first():
                continue
            v = HotspotVoucher(
                company_id=company_id, batch_id=batch_id, code=code,
                plan_id=plan_id, plan_name=plan_name,
                duration_minutes=duration_minutes, data_cap_mb=data_cap_mb,
                expires_at=exp_at, status="unused",
                created_by=request.session.get("user_id", ""))
            db.add(v)
            created.append(code)
        db.commit()
        # __radius_provision_on_generate__ — push every new voucher into
        # FreeRADIUS so Mikrotik PAP succeeds the very first time the code
        # is entered (no race between redeem-POST + Mikrotik auth).
        try:
            for _v in db.query(HotspotVoucher).filter(
                    HotspotVoucher.batch_id == batch_id,
                    HotspotVoucher.company_id == company_id).all():
                _radius_provision_voucher(_v)
        except Exception as _re:
            print(f"[voucher-radius] generate hook: {_re}")

        delivered = 0
        if delivery == "whatsapp" and phones and created:
            try:
                from twilio_whatsapp import _send_freeform, _normalise_phone
                comp = db.query(Company).filter(Company.company_id == company_id).first()
                comp_name = (comp.company_name if comp else "Hotspot")
                pairs = list(zip(phones, created))
                def _deliver():
                    ok = 0
                    for phone, code in pairs:
                        to_ = _normalise_phone(phone)
                        if not to_:
                            continue
                        body_ = (f"{comp_name} — Your hotspot voucher code: {code}\n"
                                 f"Valid for {duration_minutes or 'unlimited'} min" +
                                 (f", {data_cap_mb}MB data cap." if data_cap_mb else "."))
                        r = _send_freeform(to_, body_)
                        if r.get("success"): ok += 1
                    print(f"[s36c voucher-delivery] {ok}/{len(pairs)} WhatsApp sent")
                background.add_task(_deliver)
                delivered = len(pairs)
            except Exception as e:
                print(f"[s36c voucher-delivery] init failed: {e}")

        try:
            log_admin_activity(db, request, "create", "voucher_batch",
                target_id=batch_id,
                summary=f"generated {len(created)} vouchers"
                        + (f", WhatsApp x{delivered}" if delivered else ""))
        except Exception:
            pass
        return JSONResponse({"ok": True, "batch_id": batch_id,
                              "generated": len(created), "codes": created,
                              "whatsapp_queued": delivered})

    # S38k — bulk delete  __S38K_VOUCHER_REDESIGN__
    @router.post("/api/vouchers/delete")
    async def api_vouchers_delete(request: Request, db: Session = Depends(get_db)):
        """Hard-delete the given voucher IDs (admin-scoped to their company)."""
        auth = require_admin(request)
        if auth:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import HotspotVoucher
        body = await request.json()
        ids = body.get("ids") or []
        if not isinstance(ids, list) or not ids:
            return JSONResponse({"ok": False, "error": "no ids"}, status_code=400)
        ids = [int(i) for i in ids if str(i).isdigit()][:500]
        # __radius_revoke_on_delete__ — capture codes about to be deleted so
        # we can clean their RADIUS rows after the SQL DELETE runs.
        codes_to_revoke = []
        try:
            for v in db.query(HotspotVoucher).filter(HotspotVoucher.id.in_(ids)).all():
                if v.code:
                    codes_to_revoke.append(v.code)
        except Exception:
            pass
        if not ids:
            return JSONResponse({"ok": False, "error": "invalid ids"}, status_code=400)
        company_id = request.session.get("company_id", "N/A")
        n = db.query(HotspotVoucher).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.id.in_(ids),
        ).delete(synchronize_session=False)
        db.commit()
        return JSONResponse({"ok": True, "deleted": n})

    @router.post("/api/vouchers/{voucher_id}/revoke")
    async def api_vouchers_revoke(voucher_id: int, request: Request,
                                    db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import HotspotVoucher
        company_id = request.session.get("company_id", "N/A")
        v = db.query(HotspotVoucher).filter(
            HotspotVoucher.id == voucher_id,
            HotspotVoucher.company_id == company_id).first()
        if not v:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        v.status = "revoked"
        db.commit()
        return JSONResponse({"ok": True})

    @router.post("/api/vouchers/batch/{batch_id}/revoke")
    async def api_vouchers_batch_revoke(batch_id: str, request: Request,
                                          db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import HotspotVoucher
        company_id = request.session.get("company_id", "N/A")
        n = db.query(HotspotVoucher).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.batch_id == batch_id,
            HotspotVoucher.status == "unused",
        ).update({HotspotVoucher.status: "revoked"})
        db.commit()
        return JSONResponse({"ok": True, "revoked": n})

    @router.get("/api/vouchers/batch/{batch_id}/pdf")
    async def api_vouchers_batch_pdf(batch_id: str, request: Request,
                                      db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import HotspotVoucher, Company, CaptivePortalSettings
        company_id = request.session.get("company_id", "N/A")
        vouchers = db.query(HotspotVoucher).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.batch_id == batch_id,
        ).order_by(HotspotVoucher.id.asc()).all()
        if not vouchers:
            return JSONResponse({"ok": False, "error": "batch not found"},
                                status_code=404)
        comp = db.query(Company).filter(Company.company_id == company_id).first()
        comp_name = (comp.company_name if comp else "Hotspot")
        portal = db.query(CaptivePortalSettings).filter(
            CaptivePortalSettings.company_id == company_id).first()
        primary = (portal.primary_color if portal else None) or "#7c3aed"
        # s36d-qr-url-fix: prefer admin-configured portal URL; then honour
        # X-Forwarded-Host/Proto from nginx; last-ditch fallback is the voucher
        # code as plain text (QR scanners will show the code).
        _cfg_url = (portal.hotspot_portal_url if portal else "").strip() if portal else ""
        if _cfg_url:
            login_base = _cfg_url.rstrip("/")
            if not login_base.endswith("/hotspot/login.html"):
                login_base = login_base.rstrip("/") + "/hotspot/login.html"
        else:
            fwd_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
            fwd_proto = request.headers.get("x-forwarded-proto") or ("https" if request.url.scheme == "https" else "http")
            if fwd_host and "127.0.0.1" not in fwd_host and "localhost" not in fwd_host:
                login_base = f"{fwd_proto}://{fwd_host}/hotspot/login.html"
            else:
                login_base = ""  # will degrade to voucher-code QR

        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
            from reportlab.lib.units import inch, mm
            from reportlab.lib.colors import HexColor
            from reportlab.lib.utils import ImageReader
            import qrcode
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"pdf libs missing: {e}"},
                                status_code=500)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        W, H = A4
        cols = 2
        card_w = 3.5 * inch
        card_h = 2.0 * inch
        left_margin = (W - (card_w * cols + 10 * mm)) / 2
        top_margin = H - 15 * mm
        brand_col = HexColor(primary)

        for i, v in enumerate(vouchers):
            col = i % cols
            row_idx = (i % 10) // cols
            x = left_margin + col * (card_w + 10 * mm)
            y = top_margin - (row_idx + 1) * (card_h + 5 * mm)
            c.setLineWidth(0.5)
            c.setStrokeColor(HexColor("#bfbfbf"))
            c.rect(x, y, card_w, card_h)
            c.setFillColor(brand_col)
            c.rect(x, y + card_h - 9 * mm, card_w, 9 * mm, fill=1, stroke=0)
            c.setFillColor(HexColor("#ffffff"))
            c.setFont("Helvetica-Bold", 11)
            c.drawString(x + 4 * mm, y + card_h - 7 * mm, comp_name.upper()[:36])
            c.setFillColor(HexColor("#111827"))
            c.setFont("Courier-Bold", 18)
            c.drawString(x + 4 * mm, y + card_h - 22 * mm, v.code)
            c.setFont("Helvetica", 8)
            bits = []
            if v.plan_name: bits.append(v.plan_name)
            if v.duration_minutes: bits.append(f"{v.duration_minutes} min")
            if v.data_cap_mb: bits.append(f"{v.data_cap_mb} MB")
            c.drawString(x + 4 * mm, y + card_h - 27 * mm,
                         " · ".join(bits) or "Pre-paid voucher")
            if v.expires_at:
                c.drawString(x + 4 * mm, y + card_h - 31 * mm,
                             f"Valid till: {v.expires_at.strftime('%d-%m-%Y')}")
            try:
                qr = qrcode.QRCode(version=1, box_size=4, border=1)
                qr.add_data(f"{login_base}?code={v.code}" if login_base else v.code); qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                tmp = io.BytesIO(); img.save(tmp, format="PNG"); tmp.seek(0)
                c.drawImage(ImageReader(tmp),
                            x + card_w - 22 * mm, y + 4 * mm, 18 * mm, 18 * mm)
            except Exception:
                pass
            c.setFont("Helvetica-Oblique", 6)
            c.setFillColor(HexColor("#888888"))
            c.drawString(x + 4 * mm, y + 4 * mm,
                         "Scan QR or enter code to connect")
            if (i + 1) % 10 == 0 and (i + 1) < len(vouchers):
                c.showPage()
        c.save()
        pdf = buf.getvalue()
        try:
            log_admin_activity(db, request, "invoke", "voucher_batch",
                                target_id=batch_id,
                                summary=f"printed {len(vouchers)} voucher cards")
        except Exception:
            pass
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition":
                                 f'attachment; filename="vouchers_{batch_id}.pdf"'})


    @router.post("/api/vouchers/redeem/{code}")
    async def api_vouchers_redeem(code: str, request: Request,
                                    background: BackgroundTasks,
                                    db: Session = Depends(get_db)):
        """Mark a voucher used and fire the redeem webhook.

        Callable from the captive-portal login flow (MikroTik → admin server)
        or manually from the admin portal. Idempotent: calling twice is safe.
        """
        from database import HotspotVoucher, CaptivePortalSettings
        v = db.query(HotspotVoucher).filter(HotspotVoucher.code == code).first()
        if not v:
            return JSONResponse({"ok": False, "error": "code not found"},
                                status_code=404)
        if v.status in ("expired", "revoked"):
            return JSONResponse({"ok": False, "error": f"code {v.status}"},
                                status_code=400)

        already_used = v.status == "used"
        if not already_used:
            v.status = "used"
            v.used_by = (request.headers.get("x-mac") or
                         request.headers.get("x-real-ip") or
                         request.client.host if request.client else "") or ""
            v.used_at = datetime.utcnow()
            db.commit()
            try:
                _radius_provision_voucher(v)
            except Exception as _re:
                print(f"[voucher-radius] hook error: {_re}")
        else:
            # Already-used codes: ensure the RADIUS row still exists
            # so the customer can re-enter the code on another device.
            try:
                _radius_provision_voucher(v)
            except Exception as _re:
                print(f"[voucher-radius] re-provision: {_re}")

        # s36f-redemption-log — append-only audit row for the dashboard.
        if not already_used:
            try:
                from database import VoucherRedemption
                rec = VoucherRedemption(
                    company_id=v.company_id, voucher_id=v.id, batch_id=v.batch_id,
                    code=v.code, used_by=v.used_by,
                    mac_address=request.headers.get("x-mac") or "",
                    ip_address=(request.headers.get("x-real-ip")
                                or (request.client.host if request.client else "") or ""),
                    user_agent=request.headers.get("user-agent") or "",
                    duration_minutes=v.duration_minutes or 0,
                    data_cap_mb=v.data_cap_mb or 0,
                    plan_name=v.plan_name,
                )
                db.add(rec); db.commit()
            except Exception as _e:
                db.rollback()
                print(f"[s36f redemption-log] {_e}")

        # Fire webhook (always, unless status was already used).
        if not already_used:
            cp = db.query(CaptivePortalSettings).filter(
                CaptivePortalSettings.company_id == v.company_id).first()
            hook = (cp.voucher_webhook_url or "").strip() if cp else ""
            if hook:
                payload = {
                    "event": "voucher.redeemed",
                    "company_id": v.company_id,
                    "batch_id": v.batch_id,
                    "code": v.code,
                    "plan_name": v.plan_name,
                    "duration_minutes": v.duration_minutes,
                    "data_cap_mb": v.data_cap_mb,
                    "used_by": v.used_by,
                    "used_at": v.used_at.isoformat() if v.used_at else "",
                }
                def _post_webhook():
                    try:
                        import urllib.request, json as _json
                        req = urllib.request.Request(
                            hook, data=_json.dumps(payload).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST")
                        urllib.request.urlopen(req, timeout=5)
                    except Exception as e:
                        print(f"[s36d voucher-webhook] POST failed: {e}")
                background.add_task(_post_webhook)

        return {"ok": True, "status": v.status,
                "already_used": already_used,
                "code": v.code, "batch_id": v.batch_id}


    @router.get("/admin/vouchers/redemptions", response_class=HTMLResponse)
    async def redemptions_page(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        ctx = get_admin_context(request, db, "vouchers")
        return templates.TemplateResponse("admin_voucher_redemptions.html", ctx)

    @router.get("/api/vouchers/redemptions/list")
    async def redemptions_list(request: Request, limit: int = 200,
                                 batch_id: str = "",
                                 db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import VoucherRedemption
        company_id = request.session.get("company_id", "N/A")
        q = db.query(VoucherRedemption).filter(
            VoucherRedemption.company_id == company_id)
        if batch_id:
            q = q.filter(VoucherRedemption.batch_id == batch_id)
        rows = q.order_by(VoucherRedemption.id.desc()).limit(
            max(1, min(limit, 1000))).all()
        # s36g-loyalty: for each MAC in the page, pre-compute how many
        # PRIOR redemptions it has (company-scoped). One grouped query.
        macs = {r.mac_address for r in rows if r.mac_address}
        mac_totals = {}
        if macs:
            from sqlalchemy import func as _func_s36g
            q_tot = db.query(
                VoucherRedemption.mac_address,
                _func_s36g.count(VoucherRedemption.id),
            ).filter(
                VoucherRedemption.company_id == company_id,
                VoucherRedemption.mac_address.in_(list(macs))
            ).group_by(VoucherRedemption.mac_address)
            for m, c in q_tot.all():
                mac_totals[m] = int(c)
        return JSONResponse({"ok": True, "rows": [{
            "id": r.id, "code": r.code, "batch_id": r.batch_id,
            "plan_name": r.plan_name, "used_by": r.used_by,
            "mac": r.mac_address, "ip": r.ip_address,
            "ua": r.user_agent,
            "duration_minutes": r.duration_minutes,
            "data_cap_mb": r.data_cap_mb,
            "at": r.created_at.isoformat() if r.created_at else "",
            # s36g-loyalty: total redemptions by this MAC (this row included).
            "mac_total": mac_totals.get(r.mac_address, 0) if r.mac_address else 0,
        } for r in rows]})


    @router.get("/api/vouchers/redemptions/analytics")
    async def redemptions_analytics(request: Request, days: int = 30,
                                      db: Session = Depends(get_db)):
        """Summary stats for the Redemption Dashboard charts."""
        auth = require_admin(request)
        if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import VoucherRedemption
        from datetime import timedelta
        company_id = request.session.get("company_id", "N/A")
        days = max(1, min(days, 365))
        since = datetime.utcnow() - timedelta(days=days)

        # Build per-day counts (SQLite: strftime)
        from sqlalchemy import func as _func
        daily = db.query(
            _func.substr(VoucherRedemption.created_at, 1, 10).label("d"),
            _func.count(VoucherRedemption.id),
        ).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= since,
        ).group_by("d").order_by("d").all()

        # Hour-of-day histogram
        hourly = db.query(
            _func.substr(VoucherRedemption.created_at, 12, 2).label("h"),
            _func.count(VoucherRedemption.id),
        ).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= since,
        ).group_by("h").all()
        hour_map = {int(h): c for h, c in hourly if h and h.isdigit()}
        hour_series = [hour_map.get(i, 0) for i in range(24)]

        # Top 5 batches
        top = db.query(
            VoucherRedemption.batch_id,
            _func.count(VoucherRedemption.id).label("cnt"),
        ).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= since,
            VoucherRedemption.batch_id.isnot(None),
        ).group_by(VoucherRedemption.batch_id).order_by(_func.count(VoucherRedemption.id).desc()).limit(5).all()

        # Totals
        total_week = db.query(_func.count(VoucherRedemption.id)).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= datetime.utcnow() - timedelta(days=7),
        ).scalar() or 0
        total_month = db.query(_func.count(VoucherRedemption.id)).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= datetime.utcnow() - timedelta(days=30),
        ).scalar() or 0
        total_all = db.query(_func.count(VoucherRedemption.id)).filter(
            VoucherRedemption.company_id == company_id,
        ).scalar() or 0

        # Distinct MACs (repeat visitors)
        distinct_macs = db.query(_func.count(_func.distinct(VoucherRedemption.mac_address))).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.mac_address.isnot(None),
            VoucherRedemption.mac_address != "",
        ).scalar() or 0

        # S38 loyalty: repeat visitors = MACs with >=2 redemptions in window
        repeat_row = db.query(
            VoucherRedemption.mac_address,
            _func.count(VoucherRedemption.id).label("c"),
        ).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= since,
            VoucherRedemption.mac_address.isnot(None),
            VoucherRedemption.mac_address != "",
        ).group_by(VoucherRedemption.mac_address).having(
            _func.count(VoucherRedemption.id) >= 2
        ).all()
        repeat_visitors_count = len(repeat_row)
        repeat_redemptions_sum = sum(int(r.c) for r in repeat_row)

        # S38 loyalty: top 10 loyal devices (most redemptions in window)
        top_macs = db.query(
            VoucherRedemption.mac_address,
            _func.count(VoucherRedemption.id).label("c"),
            _func.max(VoucherRedemption.created_at).label("last_seen"),
        ).filter(
            VoucherRedemption.company_id == company_id,
            VoucherRedemption.created_at >= since,
            VoucherRedemption.mac_address.isnot(None),
            VoucherRedemption.mac_address != "",
        ).group_by(VoucherRedemption.mac_address).order_by(
            _func.count(VoucherRedemption.id).desc()
        ).limit(10).all()

        return JSONResponse({"ok": True,
            "days": days,
            "daily": [{"date": d, "count": c} for d, c in daily if d],
            "hourly": hour_series,  # 24-element array [00..23]
            "top_batches": [{"batch_id": b, "count": c} for b, c in top],
            "top_devices": [
                {"mac": m.mac_address, "count": int(m.c),
                 "last_seen": str(m.last_seen) if m.last_seen else ""}
                for m in top_macs
            ],  # __S38_LOYALTY_ANALYTICS__
            "totals": {"week": int(total_week), "month": int(total_month),
                       "all": int(total_all),
                       "distinct_devices": int(distinct_macs),
                       "repeat_visitors": int(repeat_visitors_count),
                       "repeat_redemptions": int(repeat_redemptions_sum)},
        })

    app.include_router(router)
