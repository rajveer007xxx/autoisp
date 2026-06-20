"""
patch_s36d.py — QR-URL fix + final backlog
  1. Fix voucher QR URL (was pointing to 127.0.0.1:8001)
  2. Voucher generate modal UI: delivery + phones
  3. Per-NAS walled-garden hostname whitelist (CaptivePortalSettings + push)
  4. Voucher redeem webhook (POST /api/vouchers/redeem/{code})
  5. TOTP 2FA for admin login (enrollment page + login flow)
"""
from __future__ import annotations
import os, shutil, sqlite3
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _bak(p: str) -> None:
    if os.path.exists(p):
        shutil.copy2(p, f"{p}.bak_s36d_{TS}")


def _read(p: str) -> str:
    with open(p) as f: return f.read()


def _write(p: str, c: str) -> None:
    with open(p, "w") as f: f.write(c)


def _sq_add_col(table: str, col: str, col_def: str) -> None:
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = {r[1] for r in cur.fetchall()}
        if col not in cols:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
            conn.commit()
            print(f"  ✓ {table}.{col} added")
        else:
            print(f"  ↷ {table}.{col} exists")
    finally:
        conn.close()


# =========================================================================
# 1. QR URL fix + hotspot_portal_url column
# =========================================================================
def fix_qr_url() -> None:
    print("\n[1] QR-URL fix + hotspot_portal_url")
    # 1a. SQLite column
    _sq_add_col("captive_portal_settings", "hotspot_portal_url",
                "TEXT DEFAULT ''")

    # 1b. ORM field
    db = os.path.join(ROOT, "database.py")
    src = _read(db)
    if "hotspot_portal_url" not in src:
        anchor = '    post_login_redirect_url = Column(String, nullable=True, default="")\n'
        if anchor in src:
            _bak(db)
            src = src.replace(anchor, anchor +
                '    hotspot_portal_url = Column(String, nullable=True, default="")\n'
                '    walled_garden_hosts = Column(String, nullable=True, default="")\n'
                '    voucher_webhook_url = Column(String, nullable=True, default="")\n', 1)
            _write(db, src)
            print("  ✓ ORM hotspot_portal_url / walled_garden_hosts / voucher_webhook_url added")

    # 1c. QR URL generation in routes/vouchers.py
    vpath = os.path.join(ROOT, "routes/vouchers.py")
    v = _read(vpath)
    if "# s36d-qr-url-fix" in v:
        print("  ↷ routes/vouchers.py already patched"); return
    old = '        base_url = request.headers.get("x-forwarded-host") or request.url.netloc\n        login_base = f"https://{base_url}/hotspot/login.html"\n'
    new = '''        # s36d-qr-url-fix: prefer admin-configured portal URL; then honour
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
'''
    if old in v:
        _bak(vpath)
        v = v.replace(old, new, 1)
        # Replace `qr.add_data(f"{login_base}?code={v.code}")` with safe-fallback logic
        v = v.replace(
            'qr.add_data(f"{login_base}?code={v.code}"); qr.make(fit=True)',
            'qr.add_data(f"{login_base}?code={v.code}" if login_base else v.code); qr.make(fit=True)',
            1)
        _write(vpath, v)
        print("  ✓ QR URL generator hardened")


# =========================================================================
# 2. Voucher modal UI: delivery + phones
# =========================================================================
def voucher_ui_delivery() -> None:
    print("\n[2] Voucher modal: delivery + phones")
    tpl = os.path.join(ROOT, "templates/admin_vouchers.html")
    t = _read(tpl)
    if "s36d-voucher-delivery" in t:
        print("  ↷ UI already patched"); return

    # Anchor: the Generate button (id="s36b7-generate-form" maybe, or the submit button)
    anchor = '<button type="submit" class="btn btn-primary btn-block" data-testid="voucher-generate-btn">'
    if anchor not in t:
        print("  ✗ anchor not found"); return
    _bak(tpl)
    ui_block = '''<!-- s36d-voucher-delivery -->
                <div class="form-group col-sm-12" style="border-top:1px dashed #d1d5db;padding-top:12px;margin-top:8px;">
                    <label style="font-weight:600;"><i class="bi bi-whatsapp" style="color:#16a34a;"></i> Delivery (optional)</label>
                    <div class="checkbox"><label>
                        <input type="checkbox" id="s36d-wa-enabled" data-testid="voucher-delivery-whatsapp">
                        Send each code via WhatsApp (one phone number per code, in the order below)
                    </label></div>
                    <textarea id="s36d-phones" class="form-control" rows="3" placeholder="One number per line e.g. 9876543210" data-testid="voucher-phones" style="font-family:monospace;"></textarea>
                    <small class="text-muted">Must match or exceed the number of codes being generated. Requires Twilio keys in Settings → Integrations.</small>
                </div>
                '''
    # Insert immediately before the submit button's parent form-group
    # Find the enclosing <div class="form-group"> for the button
    idx = t.find(anchor)
    btn_div_start = t.rfind('<div class="form-group', 0, idx)
    if btn_div_start < 0:
        btn_div_start = idx
    t = t[:btn_div_start] + ui_block + t[btn_div_start:]

    # JS — submit payload with delivery fields
    js_old = """fetch('/api/vouchers/generate', {"""
    js_new = """var _s36d_wa = document.getElementById('s36d-wa-enabled');
    var _s36d_ph = document.getElementById('s36d-phones');
    var _s36d_delivery = (_s36d_wa && _s36d_wa.checked) ? 'whatsapp' : 'none';
    var _s36d_phones = _s36d_ph ? _s36d_ph.value.split('\\n').map(function(s){return s.trim();}).filter(Boolean) : [];
    fetch('/api/vouchers/generate', {"""
    if js_old in t and "_s36d_delivery" not in t:
        t = t.replace(js_old, js_new, 1)
        # Inject delivery + phones into the JSON body
        body_old = 'body: JSON.stringify({'
        body_new = 'body: JSON.stringify({delivery: _s36d_delivery, phones: _s36d_phones, '
        if body_old in t:
            t = t.replace(body_old, body_new, 1)

        # Update the success alert to show whatsapp_queued
        alert_old = "alert('Generated ' + j.generated + ' vouchers in batch ' + j.batch_id);"
        alert_new = "alert('Generated ' + j.generated + ' vouchers in batch ' + j.batch_id + (j.whatsapp_queued ? (' · WhatsApp queued: ' + j.whatsapp_queued) : ''));"
        if alert_old in t:
            t = t.replace(alert_old, alert_new, 1)
    _write(tpl, t)
    print("  ✓ voucher modal UI updated (delivery toggle + phones textarea)")


# =========================================================================
# 3. Per-NAS walled-garden hostnames + UI + push
# =========================================================================
def walled_garden_hosts() -> None:
    print("\n[3] Walled-garden host whitelist")
    # 3a. SQLite already added in step 1
    _sq_add_col("captive_portal_settings", "walled_garden_hosts",
                "TEXT DEFAULT ''")

    # 3b. UI — new textarea on designer page
    tpl = os.path.join(ROOT, "templates/admin_captive_portal.html")
    t = _read(tpl)
    if 'data-testid="cp-wg-hosts"' not in t:
        _bak(tpl)
        anchor = 'data-testid="cp-redirect-url">'
        ins_after = t.find(anchor)
        if ins_after >= 0:
            # find the closing </div> of this form-group
            close_idx = t.find('</div>', ins_after) + len('</div>')
            inject = '''
                    <div class="form-group"><label>Walled-Garden Hosts
                        <small class="text-muted" style="font-weight:normal;">— comma/newline separated hostnames or IPs users can reach BEFORE logging in (pushed to MikroTik <code>/ip hotspot walled-garden</code>)</small>
                    </label>
                        <textarea name="walled_garden_hosts" class="form-control" rows="3" placeholder="payment-gateway.example.com&#10;facebook.com" data-testid="cp-wg-hosts">{{portal.walled_garden_hosts or ''}}</textarea>
                    </div>
                    <div class="form-group"><label>Canonical Hotspot Portal URL
                        <small class="text-muted" style="font-weight:normal;">— base URL embedded in printed voucher QR codes. If empty we auto-detect from the current request.</small>
                    </label>
                        <input name="hotspot_portal_url" class="form-control" value="{{portal.hotspot_portal_url or ''}}" placeholder="https://wifi.yourdomain.com/hotspot/login.html" data-testid="cp-portal-url">
                    </div>
                    <div class="form-group"><label>Voucher Redeem Webhook URL
                        <small class="text-muted" style="font-weight:normal;">— POSTed JSON when a voucher is marked used (useful for CRM / auto-reply integrations)</small>
                    </label>
                        <input name="voucher_webhook_url" class="form-control" value="{{portal.voucher_webhook_url or ''}}" placeholder="https://your-crm.example/webhook/voucher" data-testid="cp-webhook-url">
                    </div>
                '''
            t = t[:close_idx] + inject + t[close_idx:]
            _write(tpl, t)
            print("  ✓ UI: walled-garden + portal-url + webhook fields")

    # 3c. routes/captive_portal.py — save+push
    rc = os.path.join(ROOT, "routes/captive_portal.py")
    r = _read(rc)
    if '"walled_garden_hosts"' not in r:
        _bak(rc)
        # Add the 3 new fields to the settable list in save()
        old = '        for key in ("title", "welcome_text", "terms_text", "primary_color",\n                    "accent_color", "login_mode", "footer_text",\n                    "post_login_redirect_url"):'
        new = '        for key in ("title", "welcome_text", "terms_text", "primary_color",\n                    "accent_color", "login_mode", "footer_text",\n                    "post_login_redirect_url", "hotspot_portal_url",\n                    "walled_garden_hosts", "voucher_webhook_url"):'
        r = r.replace(old, new, 1)
        # Add walled-garden push into push-to-nas path
        push_old = '                redirect_url = (portal.post_login_redirect_url or "").strip()\n                if redirect_url and cli._api is not None:'
        push_new = '''                redirect_url = (portal.post_login_redirect_url or "").strip()
                wg_hosts_raw = (portal.walled_garden_hosts or "").strip()
                wg_hosts = [h.strip() for h in wg_hosts_raw.replace(",", "\\n").splitlines() if h.strip()]
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
                if redirect_url and cli._api is not None:'''
        r = r.replace(push_old, push_new, 1)
        _write(rc, r)
        print("  ✓ save() accepts new fields + push_to_nas pushes walled-garden")


# =========================================================================
# 4. Voucher redeem webhook
# =========================================================================
def voucher_webhook() -> None:
    print("\n[4] Voucher redeem endpoint + webhook")
    # 4a. SQLite column already added in step 1
    _sq_add_col("captive_portal_settings", "voucher_webhook_url",
                "TEXT DEFAULT ''")

    # 4b. Add POST /api/vouchers/redeem/{code} to routes/vouchers.py
    rc = os.path.join(ROOT, "routes/vouchers.py")
    r = _read(rc)
    if "/api/vouchers/redeem/{code}" in r:
        print("  ↷ redeem endpoint already present"); return
    _bak(rc)
    anchor = "    app.include_router(router)\n"
    if anchor not in r:
        print("  ✗ include_router anchor not found"); return
    extra = '''
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

'''
    r = r.replace(anchor, extra + anchor, 1)
    _write(rc, r)
    print("  ✓ /api/vouchers/redeem/{code} endpoint added with webhook")


# =========================================================================
# 5. TOTP 2FA for admin login
# =========================================================================
def totp_mfa() -> None:
    print("\n[5] TOTP 2FA for admin login")
    # 5a. SQLite columns on admins
    _sq_add_col("admins", "totp_secret", "TEXT DEFAULT ''")
    _sq_add_col("admins", "totp_enabled", "INTEGER DEFAULT 0")

    # 5b. ORM fields
    db = os.path.join(ROOT, "database.py")
    src = _read(db)
    if "totp_secret" not in src:
        _bak(db)
        anchor = '    profile_image_path = Column(String, nullable=True)\n'
        if anchor in src:
            src = src.replace(anchor, anchor +
                '    totp_secret = Column(String, nullable=True, default="")\n'
                '    totp_enabled = Column(Integer, default=0)\n', 1)
            _write(db, src)
            print("  ✓ ORM fields on Admin")

    # 5c. Modify /api/auth/login to require TOTP when enabled
    main = os.path.join(ROOT, "main.py")
    m = _read(main)
    if "# s36d-totp-check" not in m:
        _bak(main)
        # Inject after `user = authenticate_admin(db, companyId, userId, password)`
        # and before `if user:`
        anchor = '        user = authenticate_admin(db, companyId, userId, password)\n        if user:\n'
        inject = '''        user = authenticate_admin(db, companyId, userId, password)
        # s36d-totp-check
        if user and getattr(user, "totp_enabled", 0):
            otp = (await request.form()).get("totp") if request.method == "POST" else None
            # Accept either a body field or header (for API clients)
            if not otp:
                otp = request.headers.get("x-totp-code", "").strip()
            if not otp:
                return JSONResponse({"success": False, "mfa_required": True,
                    "message": "TOTP code required"}, status_code=401)
            try:
                import pyotp
                tot = pyotp.TOTP(user.totp_secret)
                if not tot.verify(str(otp).strip(), valid_window=1):
                    return JSONResponse({"success": False, "mfa_required": True,
                        "message": "Invalid TOTP code"}, status_code=401)
            except Exception as _e:
                return JSONResponse({"success": False, "mfa_required": True,
                    "message": f"TOTP check failed: {_e}"}, status_code=401)
        if user:
'''
        m = m.replace(anchor, inject, 1)
        _write(main, m)
        print("  ✓ /api/auth/login honours totp_enabled admins")

    # 5d. TOTP enrollment page + endpoints (append to main.py tail)
    if "# s36d-totp-endpoints" not in m:
        m = _read(main)
        _bak(main)
        enroll = '''

# s36d-totp-endpoints
@app.get("/admin/security/totp", response_class=HTMLResponse)
async def admin_totp_page(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return auth
    from database import Admin, Company
    admin_id = request.session.get("user_id")
    company_id = request.session.get("company_id")
    admin = db.query(Admin).filter(Admin.admin_id == admin_id,
                                    Admin.company_id == company_id).first()
    if not admin:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/admin/dashboard", status_code=303)
    company = db.query(Company).filter(Company.company_id == company_id).first()
    # Generate a fresh secret if none
    if not admin.totp_secret:
        import pyotp
        admin.totp_secret = pyotp.random_base32()
        db.commit()
    import pyotp, qrcode, io, base64
    issuer = (company.company_name if company else "ISP Billing")
    uri = pyotp.TOTP(admin.totp_secret).provisioning_uri(
        name=admin.admin_email or admin.admin_id,
        issuer_name=issuer)
    img = qrcode.make(uri)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    ctx = get_admin_context(request, db, "security")
    ctx.update({"admin": admin, "totp_qr_b64": qr_b64,
                "totp_secret": admin.totp_secret, "totp_uri": uri,
                "totp_enabled": bool(admin.totp_enabled)})
    return templates.TemplateResponse("admin_totp.html", ctx)

@app.post("/api/admin/totp/enable")
async def api_admin_totp_enable(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Admin
    body = await request.json()
    code = (body.get("code") or "").strip()
    admin = db.query(Admin).filter(
        Admin.admin_id == request.session.get("user_id"),
        Admin.company_id == request.session.get("company_id")).first()
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "error": "no secret"}, status_code=400)
    import pyotp
    if not pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1):
        return JSONResponse({"ok": False, "error": "invalid code"}, status_code=400)
    admin.totp_enabled = 1
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA enabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": True}

@app.post("/api/admin/totp/disable")
async def api_admin_totp_disable(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Admin
    body = await request.json()
    code = (body.get("code") or "").strip()
    admin = db.query(Admin).filter(
        Admin.admin_id == request.session.get("user_id"),
        Admin.company_id == request.session.get("company_id")).first()
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "error": "no secret"}, status_code=400)
    import pyotp
    if not pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1):
        return JSONResponse({"ok": False, "error": "invalid code"}, status_code=400)
    admin.totp_enabled = 0
    admin.totp_secret = ""
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA disabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": False}
'''
        if 'if __name__ == "__main__":' in m:
            idx = m.rfind('if __name__ == "__main__":')
            m = m[:idx] + enroll + "\n" + m[idx:]
        else:
            m = m.rstrip() + "\n" + enroll
        _write(main, m)
        print("  ✓ TOTP endpoints appended")

    # 5e. admin_totp.html template
    tpl_path = os.path.join(ROOT, "templates/admin_totp.html")
    if not os.path.exists(tpl_path):
        with open(tpl_path, "w") as f:
            f.write('''{% extends "base_admin.html" %}
{% block title %}2FA Security — Auto ISP Billing{% endblock %}
{% block content %}
<section class="content-header">
    <h1>Two-Factor Authentication <small>Secure your admin login with a 6-digit code</small></h1>
</section>
<section class="content">
<div class="row">
  <div class="col-md-6">
    <div class="box box-primary">
      <div class="box-header"><h3 class="box-title">{% if totp_enabled %}2FA is ENABLED{% else %}Enable 2FA{% endif %}</h3></div>
      <div class="box-body">
        {% if not totp_enabled %}
        <p>Scan this QR code with <b>Google Authenticator</b>, <b>Authy</b>, or any TOTP app, then enter the 6-digit code below to activate 2FA.</p>
        <div style="text-align:center;background:#f7fafc;padding:20px;border-radius:6px;">
          <img src="data:image/png;base64,{{totp_qr_b64}}" alt="TOTP QR" style="max-width:220px;" data-testid="totp-qr">
          <div style="margin-top:12px;font-family:monospace;font-size:14px;">
            <code>{{totp_secret}}</code>
          </div>
        </div>
        <hr>
        <div class="form-group">
          <label>Enter 6-digit code from the app:</label>
          <input id="totp-code" class="form-control" maxlength="6" placeholder="123456" data-testid="totp-code-input" style="font-size:20px;text-align:center;letter-spacing:6px;font-family:monospace;">
        </div>
        <button class="btn btn-success btn-block" onclick="s36dTotpEnable()" data-testid="totp-enable-btn">
          <i class="bi bi-shield-check"></i> Activate 2FA
        </button>
        <div id="totp-out" style="margin-top:10px;"></div>
        {% else %}
        <div class="alert alert-success"><i class="bi bi-check-circle"></i>
          <b>2FA is active</b> on your account. You'll be prompted for a code at every login.</div>
        <div class="form-group">
          <label>To disable, enter your current 6-digit code:</label>
          <input id="totp-code" class="form-control" maxlength="6" placeholder="123456" data-testid="totp-code-input">
        </div>
        <button class="btn btn-danger btn-block" onclick="s36dTotpDisable()" data-testid="totp-disable-btn">
          <i class="bi bi-shield-slash"></i> Disable 2FA
        </button>
        <div id="totp-out" style="margin-top:10px;"></div>
        {% endif %}
      </div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="box box-default">
      <div class="box-header"><h3 class="box-title">How it works</h3></div>
      <div class="box-body" style="font-size:13px;">
        <ol>
          <li>Install a TOTP app on your phone (Google Authenticator, Authy, Microsoft Authenticator, 1Password).</li>
          <li>Scan the QR code. The app will add a 6-digit rolling code that refreshes every 30 seconds.</li>
          <li>Enter the current code here to activate 2FA.</li>
          <li>From now on, the login page will ask for your 2FA code after your password.</li>
        </ol>
        <p class="text-muted" style="margin-top:12px;">Lost your device? Disable 2FA from another logged-in admin session, or contact your superadmin.</p>
      </div>
    </div>
  </div>
</div>
</section>
<script>
function s36dTotpEnable(){
  var c = document.getElementById('totp-code').value.trim();
  var out = document.getElementById('totp-out');
  if(!/^\\d{6}$/.test(c)){ out.innerHTML='<span class="text-danger">Enter a 6-digit code</span>'; return; }
  fetch('/api/admin/totp/enable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
    .then(r=>r.json()).then(j=>{
      if(j.ok){ out.innerHTML='<span class="text-success">2FA enabled</span>'; setTimeout(function(){location.reload();},900); }
      else out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
    });
}
function s36dTotpDisable(){
  var c = document.getElementById('totp-code').value.trim();
  var out = document.getElementById('totp-out');
  if(!/^\\d{6}$/.test(c)){ out.innerHTML='<span class="text-danger">Enter a 6-digit code</span>'; return; }
  fetch('/api/admin/totp/disable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
    .then(r=>r.json()).then(j=>{
      if(j.ok){ out.innerHTML='<span class="text-success">2FA disabled</span>'; setTimeout(function(){location.reload();},900); }
      else out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
    });
}
</script>
{% endblock %}
''')
        print("  ✓ admin_totp.html template written")

    # 5f. Add sidebar link to base_admin.html
    base = os.path.join(ROOT, "templates/base_admin.html")
    b = _read(base)
    if "/admin/security/totp" not in b:
        _bak(base)
        # Add inside sidebar — use a stable anchor like the Captive Portal link
        anchor = '/admin/captive-portal'
        if anchor in b:
            # Insert a new <li> right after the captive-portal link block
            # Find the </li> that closes the captive-portal li
            link_i = b.find(anchor)
            li_close = b.find("</li>", link_i)
            if li_close >= 0:
                link_html = '''\n                <li><a href="/admin/security/totp" data-testid="sidebar-totp"><i class="bi bi-shield-lock"></i> <span>2FA Security</span></a></li>'''
                b = b[:li_close + 5] + link_html + b[li_close + 5:]
                _write(base, b)
                print("  ✓ sidebar link to /admin/security/totp")

    # 5g. Login page — add optional TOTP field
    lg = os.path.join(ROOT, "templates/login.html")
    if os.path.exists(lg):
        L = _read(lg)
        if "totp" not in L.lower():
            _bak(lg)
            # Best-effort: inject a hidden TOTP field that pops up if 401 mfa_required
            # Find the login form's password input and inject after it.
            anchor = 'name="password"'
            if anchor in L:
                pwd_i = L.find(anchor)
                # close the containing tag
                end_tag = L.find(">", pwd_i) + 1
                inject = '''
                <div id="s36d-totp-row" class="form-group" style="display:none;">
                    <label><i class="bi bi-shield-lock"></i> 2FA Code</label>
                    <input name="totp" id="s36d-totp" class="form-control" maxlength="6" placeholder="6-digit code" autocomplete="one-time-code" data-testid="login-totp">
                </div>'''
                L = L[:end_tag] + inject + L[end_tag:]
                # Also inject a JS hook that shows the field on mfa_required responses.
                # Search for the existing fetch('/api/auth/login') call.
                if "mfa_required" not in L:
                    js_hook = '''
<script>
(function(){
  // s36d: toggle TOTP field when backend signals mfa_required.
  var origFetch = window.fetch;
  window.fetch = function(){
    return origFetch.apply(this, arguments).then(function(resp){
      try {
        if (arguments[0] && String(arguments[0]).indexOf('/api/auth/login') > -1 && !resp.ok) {
          resp.clone().json().then(function(j){
            if (j && j.mfa_required) {
              var row = document.getElementById('s36d-totp-row');
              if (row) { row.style.display = 'block'; document.getElementById('s36d-totp').focus(); }
            }
          }).catch(function(){});
        }
      } catch(e){}
      return resp;
    });
  };
})();
</script>
'''
                    L = L.rstrip() + js_hook + "\n"
                _write(lg, L)
                print("  ✓ login.html: TOTP field + mfa_required hook")


# =========================================================================
def main() -> None:
    print("═" * 60)
    print(f" patch_s36d — QR fix + backlog ({TS})")
    print("═" * 60)
    fix_qr_url()
    voucher_ui_delivery()
    walled_garden_hosts()
    voucher_webhook()
    totp_mfa()
    print("\n" + "═" * 60)
    print(" DONE — restart isp-admin and run pytest")


if __name__ == "__main__":
    main()
