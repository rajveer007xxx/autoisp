"""routes/captive_portal.py — s36c refactor.

Owns:
  GET  /admin/captive-portal
  POST /api/captive-portal/save
  POST /api/captive-portal/push-to-nas/{nas_id}
  GET  /api/captive-portal/nas-list
  GET  /hotspot/login.html
"""
from __future__ import annotations
import os, uuid
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

router = APIRouter(tags=["captive-portal"])

UPLOAD_BASE = "/opt/ispbilling/admin-portal/static/uploads/portal"


def register(app, *, templates, require_admin, get_db, get_admin_context,
             log_admin_activity):
    os.makedirs(UPLOAD_BASE, exist_ok=True)

    @router.get("/admin/captive-portal", response_class=HTMLResponse)
    async def admin_captive_portal_page(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        from database import CaptivePortalSettings
        company_id = request.session.get("company_id")
        row = db.query(CaptivePortalSettings).filter(
            CaptivePortalSettings.company_id == company_id).first()
        if not row:
            row = CaptivePortalSettings(company_id=company_id,
                title="Free Wi-Fi", welcome_text="Please sign in to access the internet.",
                primary_color="#7c3aed", accent_color="#06b6d4", login_mode="voucher")
            db.add(row); db.commit(); db.refresh(row)
        ctx = get_admin_context(request, db, "captive_portal")
        ctx["portal"] = row
        return templates.TemplateResponse("admin_captive_portal.html", ctx)

    @router.post("/api/captive-portal/save")
    async def api_captive_portal_save(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        from database import CaptivePortalSettings
        form = await request.form()
        company_id = request.session.get("company_id")
        row = db.query(CaptivePortalSettings).filter(
            CaptivePortalSettings.company_id == company_id).first()
        if not row:
            row = CaptivePortalSettings(company_id=company_id)
            db.add(row)
        def _normalize_url(u: str) -> str:
            # __S43AA_URL_NORMALIZE__: strip accidental http://https:// or
            # https://http:// double schemes that creep in via copy-paste.
            if not u: return u
            u = u.strip()
            for _ in range(3):
                low = u.lower()
                if low.startswith("http://https://"):
                    u = u[len("http://"):]
                elif low.startswith("https://http://"):
                    u = u[len("https://"):]
                elif low.startswith("https://https://"):
                    u = u[len("https://"):]
                elif low.startswith("http://http://"):
                    u = u[len("http://"):]
                else:
                    break
            return u

        for key in ("title", "welcome_text", "terms_text", "primary_color",
                    "accent_color", "login_mode", "footer_text",
                    "post_login_redirect_url", "hotspot_portal_url",
                    "walled_garden_hosts", "voucher_webhook_url"):
            v = form.get(key)
            if v is not None:
                if key in ("post_login_redirect_url", "hotspot_portal_url",
                           "voucher_webhook_url"):
                    v = _normalize_url(v)
                setattr(row, key, v)
        row.whatsapp_otp_enabled = 1 if form.get("whatsapp_otp_enabled") else 0

        async def _store(field: str, attr: str) -> None:
            f = form.get(field)
            if not f or not hasattr(f, "filename") or not f.filename:
                return
            ext = os.path.splitext(f.filename)[1].lower() or ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".webp", ".svg"):
                return
            name = f"{uuid.uuid4().hex}{ext}"
            dst = os.path.join(UPLOAD_BASE, name)
            with open(dst, "wb") as fh:
                fh.write(await f.read())
            setattr(row, attr, f"/static/uploads/portal/{name}")

        await _store("logo", "logo_path")
        await _store("background", "background_path")
        # s36e-autofill-portal-url — never leave the canonical portal URL
        # empty; derive from the current request's Host header so QR codes
        # always work even if the admin forgets to set it.
        if not (row.hotspot_portal_url or "").strip():
            fwd_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
            fwd_proto = request.headers.get("x-forwarded-proto") or "https"
            if fwd_host and "127.0.0.1" not in fwd_host and "localhost" not in fwd_host:
                row.hotspot_portal_url = f"{fwd_proto}://{fwd_host}/hotspot/login.html"
        db.commit()
        try:
            log_admin_activity(db, request, "update", "captive_portal",
                               target_id=company_id, summary="portal settings saved")
        except Exception:
            pass
        return {"ok": True}

    @router.post("/api/captive-portal/push-to-nas/{nas_id}")
    async def api_captive_portal_push_to_nas(nas_id: int, request: Request,
                                              db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        from database import CaptivePortalSettings
        from radius_network import NasDevice
        company_id = request.session.get("company_id")
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id, NasDevice.company_id == company_id).first()
        if not nas:
            return JSONResponse({"success": False, "message": "NAS not found"}, status_code=404)
        portal = db.query(CaptivePortalSettings).filter(
            CaptivePortalSettings.company_id == company_id).first()
        if not portal:
            return JSONResponse({"success": False,
                "message": "Captive portal not configured yet"}, status_code=400)
        try:
            tpl = templates.get_template("hotspot_login.html")
            html = tpl.render({"portal": portal, "request": request})
        except Exception as e:
            return JSONResponse({"success": False,
                "message": f"render failed: {e}"}, status_code=500)
        try:
            from routeros_provision import RouterOSClient
            with RouterOSClient(nas) as cli:
                res = cli.upload_file_sftp("hotspot/login.html", html.encode("utf-8"))
                redirect_url = (portal.post_login_redirect_url or "").strip()
                # s36e-per-nas-wg — NAS override wins over company default.
                nas_wg = (getattr(nas, "walled_garden_hosts", "") or "").strip()
                wg_hosts_raw = nas_wg or (portal.walled_garden_hosts or "").strip()
                wg_hosts = [h.strip() for h in wg_hosts_raw.replace(",", "\n").splitlines() if h.strip()]
                if wg_hosts and cli._api is not None:
                    try:
                        wg = cli._api.path("ip/hotspot/walled-garden")
                        existing = {row.get("dst-host"): row.get(".id") for row in list(wg)}
                        # Additive: push any host not already there.
                        for host in wg_hosts:
                            if host not in existing:
                                wg.add(**{"dst-host": host, "action": "allow",
                                          "comment": "isp-billing-auto"})
                    except Exception:
                        pass  # best-effort
                if redirect_url and cli._api is not None:
                    try:
                        prof = cli._api.path("ip/hotspot/user/profile")
                        for p in list(prof):
                            if p.get("name") == "default":
                                prof.update(**{".id": p[".id"],
                                               "http-redirect": redirect_url})
                                break
                    except Exception:
                        pass
        except Exception as e:
            return JSONResponse({"success": False,
                "message": f"connection failed: {e}"}, status_code=500)
        try:
            log_admin_activity(db, request, "push", "captive_portal",
                               target_id=str(nas_id),
                               summary=f"pushed to NAS {nas.name} ({nas.ip_address})")
        except Exception:
            pass
        if not res.get("success") and not res.get("dry_run"):
            return JSONResponse({"success": False,
                "message": res.get("error") or "upload failed"}, status_code=502)
        return {"success": True, "nas": nas.name, "size": res.get("size"),
                "remote": res.get("remote"), "html_bytes": len(html),
                "redirect_url": (portal.post_login_redirect_url or "")}

    @router.get("/api/captive-portal/nas-list")
    async def api_captive_portal_nas_list(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth: return auth
        from radius_network import NasDevice
        company_id = request.session.get("company_id")
        rows = db.query(NasDevice).filter(
            NasDevice.company_id == company_id,
            NasDevice.status == "Active").order_by(NasDevice.name).all()
        return {"success": True, "rows": [
            {"id": r.id, "name": r.name, "ip": r.ip_address,
             "use_ssh": bool(getattr(r, "use_ssh", False))}
            for r in rows]}

    @router.get("/hotspot/login.html", response_class=HTMLResponse)
    async def hotspot_login_page(request: Request, db: Session = Depends(get_db)):
        from database import CaptivePortalSettings
        cid = request.session.get("company_id")
        row = db.query(CaptivePortalSettings).filter(
            CaptivePortalSettings.company_id == cid).first() if cid else None
        if not row:
            row = db.query(CaptivePortalSettings).order_by(
                CaptivePortalSettings.id.asc()).first()
        return templates.TemplateResponse("hotspot_login.html",
            {"request": request, "portal": row})

    app.include_router(router)
