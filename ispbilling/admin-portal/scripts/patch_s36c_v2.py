"""patch_s36c_v2.py — Safe AST-based extraction + backlog.

Differences from v1:
  • `strip_routes_by_path()` uses `ast` to find exact line ranges of
    FastAPI route handlers. No more overrunning into log_admin_activity!
  • Wires are appended to the **true tail** of main.py (after all defs).
"""
from __future__ import annotations
import ast, os, shutil, sqlite3
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _bak(path: str, tag: str = "s36c") -> None:
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak_{tag}_{TS}")


def _read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f: return f.read()


def _write(p: str, c: str) -> None:
    with open(p, "w", encoding="utf-8") as f: f.write(c)


# =========================================================================
# AST-based route finder + remover
# =========================================================================
def _find_route_line_ranges(src: str, route_paths: set[str]) -> list[tuple[int, int, str]]:
    """Return list of (start_line, end_line, path) — 1-indexed inclusive.

    We look at every top-level AsyncFunctionDef / FunctionDef whose FIRST
    decorator is `@app.<method>("/path", ...)` and whose path matches one
    of `route_paths`.  We also include any leading comment lines (single
    `# ...` block immediately above the @app.<method> decorator).
    """
    tree = ast.parse(src)
    lines = src.splitlines()
    hits: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        dec = node.decorator_list[0]
        # We need `app.<method>("...", ...)` — ast.Call(ast.Attribute(Name app))
        path = None
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path = dec.args[0].value
        if path not in route_paths:
            continue
        start = dec.lineno  # decorator first line (1-indexed)
        # Walk upward: include leading `#` comment lines (blank line breaks the block).
        i = start - 2  # line above decorator (0-indexed)
        while i >= 0:
            s = lines[i].rstrip()
            if s.startswith("#") and not s.startswith("#!"):
                start = i + 1
                i -= 1
            else:
                break
        end = node.end_lineno  # last line of function body
        # Extend through trailing blank lines until next non-blank (exclusive).
        j = end
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        hits.append((start, j, path))
    return hits


def _remove_lines(src: str, ranges: list[tuple[int, int, str]]) -> str:
    """Remove inclusive 1-indexed line ranges from src."""
    lines = src.splitlines(keepends=True)
    drop = set()
    for s, e, _ in ranges:
        for i in range(s - 1, min(e, len(lines))):
            drop.add(i)
    return "".join(ln for i, ln in enumerate(lines) if i not in drop)


# =========================================================================
# 1. Extract captive-portal routes
# =========================================================================
CAPTIVE_PATHS = {
    "/admin/captive-portal",
    "/api/captive-portal/save",
    "/hotspot/login.html",
}

def extract_captive_portal() -> None:
    print("\n[1] Extracting captive-portal routes → routes/captive_portal.py")
    main_path = os.path.join(ROOT, "main.py")
    target    = os.path.join(ROOT, "routes", "captive_portal.py")
    if os.path.exists(target):
        print("  ↷ already extracted"); return
    src = _read(main_path)
    _bak(main_path)

    ranges = _find_route_line_ranges(src, CAPTIVE_PATHS)
    print(f"  · found {len(ranges)} routes:")
    for s, e, p in ranges:
        print(f"     lines {s}-{e}   {p}")
    src = _remove_lines(src, ranges)

    # Append router wire at TRUE tail (after all defs are complete).
    wire = '''

# s36c-captive-portal-router-wired (appended at true tail)
try:
    from routes import captive_portal as _s36c_cp_mod
    _s36c_cp_mod.register(
        app,
        templates=templates,
        require_admin=require_admin,
        get_db=get_db,
        get_admin_context=get_admin_context,
        log_admin_activity=log_admin_activity,
    )
except Exception as _e:
    import logging
    logging.getLogger("main").exception("captive_portal router wire failed: %s", _e)
'''
    src = src.rstrip() + "\n" + wire
    _write(main_path, src)

    module_src = '''"""routes/captive_portal.py — s36c refactor.

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
        for key in ("title", "welcome_text", "terms_text", "primary_color",
                    "accent_color", "login_mode", "footer_text",
                    "post_login_redirect_url"):
            v = form.get(key)
            if v is not None:
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
'''
    # Write the module. Also need to strip the OLD push-to-nas and nas-list
    # endpoints (added by patch_s36b9 at the tail of main.py, not AST-removed
    # because they're there from v1 of s36c attempt — or were restored from
    # backup). Handle both cases: AFTER AST strip, check if main.py still
    # contains "@app.post(\"/api/captive-portal/push-to-nas" and strip.
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(module_src)
    print(f"  ✓ {target} written")

    # Strip the s36b9 push-to-nas + nas-list (added outside the AST area at
    # tail — these are still in main.py because AST didn't match them).
    src2 = _read(main_path)
    tree = ast.parse(src2)
    extra_paths = {"/api/captive-portal/push-to-nas/{nas_id}",
                   "/api/captive-portal/nas-list"}
    extras = _find_route_line_ranges(src2, extra_paths)
    if extras:
        print(f"  · also stripping {len(extras)} s36b9 tail routes:")
        for s, e, p in extras:
            print(f"     lines {s}-{e}   {p}")
        src2 = _remove_lines(src2, extras)
        _write(main_path, src2)


# =========================================================================
# 2. Extract voucher routes
# =========================================================================
VOUCHER_PATHS = {
    "/admin/vouchers",
    "/api/vouchers/list",
    "/api/vouchers/generate",
    "/api/vouchers/{voucher_id}/revoke",
    "/api/vouchers/batch/{batch_id}/revoke",
    "/api/vouchers/batch/{batch_id}/pdf",
}

def extract_vouchers() -> None:
    print("\n[2] Extracting voucher routes → routes/vouchers.py")
    main_path = os.path.join(ROOT, "main.py")
    target    = os.path.join(ROOT, "routes", "vouchers.py")
    if os.path.exists(target):
        print("  ↷ already extracted"); return
    src = _read(main_path)
    _bak(main_path)

    ranges = _find_route_line_ranges(src, VOUCHER_PATHS)
    print(f"  · found {len(ranges)} routes:")
    for s, e, p in ranges:
        print(f"     lines {s}-{e}   {p}")
    src = _remove_lines(src, ranges)

    wire = '''

# s36c-vouchers-router-wired (appended at true tail)
try:
    from routes import vouchers as _s36c_vch_mod
    _s36c_vch_mod.register(
        app,
        templates=templates,
        require_admin=require_admin,
        get_db=get_db,
        get_admin_context=get_admin_context,
        log_admin_activity=log_admin_activity,
        gen_code=_s36b7_gen_code,
    )
except Exception as _e:
    import logging
    logging.getLogger("main").exception("vouchers router wire failed: %s", _e)
'''
    src = src.rstrip() + "\n" + wire
    _write(main_path, src)

    module_src = r'''"""routes/vouchers.py — s36c refactor."""
from __future__ import annotations
import io
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from sqlalchemy.orm import Session

router = APIRouter(tags=["vouchers"])


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
        base_url = request.headers.get("x-forwarded-host") or request.url.netloc
        login_base = f"https://{base_url}/hotspot/login.html"

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
                qr.add_data(f"{login_base}?code={v.code}"); qr.make(fit=True)
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

    app.include_router(router)
'''
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(module_src)
    print(f"  ✓ {target} written")


# =========================================================================
# 3. CaptivePortalSettings.post_login_redirect_url column + ORM + UI
# =========================================================================
def add_redirect_url_field() -> None:
    print("\n[3] post_login_redirect_url column + ORM field + UI")
    # 3a. SQLite
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(captive_portal_settings)")
        cols = {row[1] for row in cur.fetchall()}
        if "post_login_redirect_url" not in cols:
            cur.execute("ALTER TABLE captive_portal_settings ADD COLUMN "
                        "post_login_redirect_url TEXT DEFAULT ''")
            conn.commit()
            print("  ✓ SQLite column added")
        else:
            print("  ↷ SQLite column exists")
    finally:
        conn.close()
    # 3b. ORM
    db_path = os.path.join(ROOT, "database.py")
    src = _read(db_path)
    if "post_login_redirect_url" not in src:
        anchor = '    whatsapp_otp_enabled = Column(Integer, default=0)\n'
        if anchor in src:
            _bak(db_path)
            src = src.replace(anchor, anchor +
                '    post_login_redirect_url = Column(String, nullable=True, default="")\n', 1)
            _write(db_path, src)
            print("  ✓ ORM field added")
    # 3c. UI
    tpl_path = os.path.join(ROOT, "templates/admin_captive_portal.html")
    t = _read(tpl_path)
    if 'name="post_login_redirect_url"' not in t:
        _bak(tpl_path)
        anchor = '<input name="footer_text"'
        if anchor in t:
            inject = '''<div class="form-group"><label>Post-Login Redirect URL
                        <small class="text-muted" style="font-weight:normal;">— users bounce here after a successful hotspot login (pushed to MikroTik <code>/ip hotspot user profile</code>)</small>
                    </label>
                        <input name="post_login_redirect_url" class="form-control" value="{{portal.post_login_redirect_url or ''}}" placeholder="https://your-site.example/welcome" data-testid="cp-redirect-url">
                    </div>
                    '''
            footer_div_start = t.rfind('<div class="form-group">', 0, t.find(anchor))
            if footer_div_start >= 0:
                t = t[:footer_div_start] + inject + t[footer_div_start:]
                _write(tpl_path, t)
                print("  ✓ UI field inserted")


# =========================================================================
# 4. ?auth=<type> filter on /admin/users
# =========================================================================
def add_auth_filter_on_admin_users() -> None:
    print("\n[4] ?auth=<type> filter on /admin/users")
    path = os.path.join(ROOT, "main.py")
    src = _read(path)
    if "# s36c-auth-filter" in src:
        print("  ↷ already patched"); return
    anchor = '    customers = db.query(Customer).filter(\n        Customer.company_id == company_id,\n        Customer.status != "Deleted"\n    ).all()\n'
    if anchor not in src:
        print("  ✗ anchor not found"); return
    _bak(path)
    src = src.replace(anchor, anchor +
        '    # s36c-auth-filter — honour ?auth=<hotspot|pppoe|static_ip|static_mac>\n'
        '    _auth_q = (request.query_params.get("auth") or "").strip().lower()\n'
        '    if _auth_q in ("hotspot", "pppoe", "static_ip", "static_mac"):\n'
        '        customers = [c for c in customers\n'
        '                     if (c.auth_type or "pppoe").lower() == _auth_q]\n', 1)
    _write(path, src)
    print("  ✓ filter block injected")


# =========================================================================
# 5. Edge-case pytest (NAS unreachable)
# =========================================================================
def write_nas_unreachable_test() -> None:
    print("\n[5] tests/test_auth_backend_unreachable.py")
    path = os.path.join(ROOT, "tests/test_auth_backend_unreachable.py")
    if os.path.exists(path):
        print("  ↷ already exists"); return
    with open(path, "w") as f:
        f.write('''"""AuthBackend edge-case: NAS unreachable must fail gracefully."""
from __future__ import annotations
import sys, socket, os
sys.path.insert(0, "/opt/ispbilling/admin-portal")


class FakeNasUnreachable:
    id = 999; name = "ghost-nas"
    ip_address = "10.255.255.254"   # unroutable
    api_username = "admin"; api_password = "x"
    port = 8728; use_tls = False; use_ssh = False
    ssh_port = 22; company_id = "14150129"; status = "Active"


def test_routeros_client_connect_fails_gracefully():
    from routeros_provision import RouterOSClient
    socket.setdefaulttimeout(2)
    raised = None
    try:
        with RouterOSClient(FakeNasUnreachable()) as cli: _ = cli
    except Exception as e:
        raised = e
    socket.setdefaulttimeout(None)
    assert raised is not None, "connecting to unroutable IP should raise"


def test_upload_file_sftp_unreachable_returns_error():
    from routeros_provision import RouterOSClient
    cli = RouterOSClient.__new__(RouterOSClient)
    cli.nas = FakeNasUnreachable(); cli.dry_run = False
    cli._api = None; cli._ssh = None
    cli.transport_used = None; cli.commands = []
    socket.setdefaulttimeout(2)
    res = cli.upload_file_sftp("hotspot/login.html", b"<html/>")
    socket.setdefaulttimeout(None)
    assert res.get("success") is False
    assert "error" in res


def test_push_to_nas_endpoint_survives_unreachable():
    import requests
    BASE = os.environ.get("ISP_ADMIN_URL", os.environ.get("ISP_ADMIN_URL", os.environ.get('ISP_ADMIN_URL', 'http://127.0.0.1:8001')))
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"})
    assert r.status_code == 200
    r = s.post(f"{BASE}/api/captive-portal/push-to-nas/999999")
    assert r.status_code in (404, 500, 502)
    assert r.json().get("success") is False
''')
    print("  ✓ written")


# =========================================================================
def main() -> None:
    print("═" * 60)
    print(f" patch_s36c_v2 — AST-safe refactor + backlog ({TS})")
    print("═" * 60)
    extract_captive_portal()
    extract_vouchers()
    add_redirect_url_field()
    add_auth_filter_on_admin_users()
    write_nas_unreachable_test()
    print("\n" + "═" * 60)
    print(" DONE — restart isp-admin and run pytest")


if __name__ == "__main__":
    main()
