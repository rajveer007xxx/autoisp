"""patch_s36f.py — Final-final backlog:
  1. 2FA recovery codes
  2. Extract /api/admin/data-mgmt/* + /api/admin/notifications/* via AST
  3. Redemption dashboard (/admin/vouchers/redemptions) + log table
"""
from __future__ import annotations
import ast, os, shutil, sqlite3
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _bak(p):
    if os.path.exists(p): shutil.copy2(p, f"{p}.bak_s36f_{TS}")


def _read(p):
    with open(p) as f: return f.read()


def _write(p, c):
    with open(p, "w") as f: f.write(c)


def _sq_add_col(table, col, col_def):
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


def _find_route_ranges(src: str, route_paths: set[str]):
    """AST-safe route finder — returns list of (start_line, end_line, path)."""
    tree = ast.parse(src)
    lines = src.splitlines()
    hits = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        dec = node.decorator_list[0]
        path = None
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path = dec.args[0].value
        if path not in route_paths:
            continue
        start = dec.lineno
        i = start - 2
        while i >= 0:
            s = lines[i].rstrip()
            if s.startswith("#") and not s.startswith("#!"):
                start = i + 1
                i -= 1
            else:
                break
        end = node.end_lineno
        j = end
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        hits.append((start, j, path))
    return hits


def _remove_lines(src: str, ranges):
    lines = src.splitlines(keepends=True)
    drop = set()
    for s, e, _ in ranges:
        for i in range(s - 1, min(e, len(lines))):
            drop.add(i)
    return "".join(ln for i, ln in enumerate(lines) if i not in drop)


def _extract_block(src: str, ranges):
    """Return the concatenated text of line-ranges (for moving into module)."""
    lines = src.splitlines(keepends=True)
    out = []
    for s, e, _ in ranges:
        out.extend(lines[s - 1:min(e, len(lines))])
    return "".join(out)


# =========================================================================
# 1. 2FA recovery codes
# =========================================================================
def recovery_codes():
    print("\n[1] 2FA recovery codes")
    # 1a. Column
    _sq_add_col("admins", "totp_recovery_codes",
                "TEXT DEFAULT ''")  # JSON-encoded list of hashed codes

    # 1b. ORM
    db = os.path.join(ROOT, "database.py")
    src = _read(db)
    if "totp_recovery_codes" not in src:
        _bak(db)
        anchor = '    totp_enabled = Column(Integer, default=0)\n'
        # only the Admin class (not SuperAdmin which is above). Use second occurrence.
        # Because we put totp_enabled in Admin only, first occurrence is the right one.
        src = src.replace(anchor, anchor +
            '    totp_recovery_codes = Column(String, nullable=True, default="")  # JSON list of bcrypt hashes\n', 1)
        _write(db, src)
        print("  ✓ ORM field added")

    # 1c. Modify /admin/security/totp + enable endpoint to generate codes
    main = os.path.join(ROOT, "main.py")
    m = _read(main)
    if "# s36f-recovery-codes" in m:
        print("  ↷ TOTP flow already has recovery codes"); return
    _bak(main)

    # Inject helper at tail
    helper = '''

# s36f-recovery-codes
def _s36f_gen_recovery_codes(n: int = 10) -> list:
    """Generate n human-friendly recovery codes of format XXXX-XXXX."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(n):
        a = "".join(secrets.choice(alphabet) for _ in range(4))
        b = "".join(secrets.choice(alphabet) for _ in range(4))
        codes.append(f"{a}-{b}")
    return codes


def _s36f_hash_codes(plain: list) -> list:
    import hashlib
    return [hashlib.sha256(c.upper().strip().encode()).hexdigest() for c in plain]


def _s36f_check_recovery(admin, submitted: str) -> bool:
    """Returns True if `submitted` matches a stored recovery hash and
    consumes the code (removes it from the list, persists)."""
    import json, hashlib
    if not admin or not (admin.totp_recovery_codes or "").strip():
        return False
    try:
        stored = json.loads(admin.totp_recovery_codes)
    except Exception:
        return False
    sub_hash = hashlib.sha256(submitted.upper().strip().encode()).hexdigest()
    if sub_hash in stored:
        stored.remove(sub_hash)
        admin.totp_recovery_codes = json.dumps(stored)
        return True
    return False


@app.post("/api/admin/totp/regenerate-codes")
async def api_admin_totp_regen_codes(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Admin
    import json
    body = await request.json()
    code = (body.get("code") or "").strip()
    admin = db.query(Admin).filter(
        Admin.admin_id == request.session.get("user_id"),
        Admin.company_id == request.session.get("company_id")).first()
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "error": "2FA not enabled"}, status_code=400)
    import pyotp
    if not pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1):
        return JSONResponse({"ok": False, "error": "invalid code"}, status_code=400)
    plain = _s36f_gen_recovery_codes(10)
    admin.totp_recovery_codes = json.dumps(_s36f_hash_codes(plain))
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id,
                           summary=f"regenerated {len(plain)} recovery codes")
    except Exception:
        pass
    return {"ok": True, "codes": plain}  # show plaintext ONCE to caller
'''
    # Append right before the first `if __name__ == "__main__":`
    if 'if __name__ == "__main__":' in m:
        idx = m.rfind('if __name__ == "__main__":')
        m = m[:idx] + helper + "\n" + m[idx:]
    else:
        m = m.rstrip() + helper + "\n"

    # Modify enable endpoint to also generate + return recovery codes (first time)
    enable_old = '''    admin.totp_enabled = 1
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA enabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": True}'''
    enable_new = '''    admin.totp_enabled = 1
    # s36f: generate one-time recovery codes at first-enable.
    import json as _json_s36f
    _plain_codes = None
    if not (admin.totp_recovery_codes or "").strip():
        _plain_codes = _s36f_gen_recovery_codes(10)
        admin.totp_recovery_codes = _json_s36f.dumps(_s36f_hash_codes(_plain_codes))
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA enabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": True, "recovery_codes": _plain_codes}'''
    if enable_old in m:
        m = m.replace(enable_old, enable_new, 1)
        print("  ✓ enable endpoint now returns recovery codes")

    # Modify /api/auth/login TOTP branch to also accept a recovery code.
    login_old = '''            try:
                import pyotp
                tot = pyotp.TOTP(user.totp_secret)
                if not tot.verify(str(otp).strip(), valid_window=1):
                    return JSONResponse({"success": False, "mfa_required": True,
                        "message": "Invalid TOTP code"}, status_code=401)
            except Exception as _e:'''
    login_new = '''            try:
                import pyotp
                tot = pyotp.TOTP(user.totp_secret)
                otp_stripped = str(otp).strip()
                if not tot.verify(otp_stripped, valid_window=1):
                    # s36f: fall back to recovery code consumption.
                    if _s36f_check_recovery(user, otp_stripped):
                        db.commit()  # persist the consumed recovery code
                    else:
                        return JSONResponse({"success": False, "mfa_required": True,
                            "message": "Invalid TOTP code"}, status_code=401)
            except Exception as _e:'''
    if login_old in m:
        m = m.replace(login_old, login_new, 1)
        print("  ✓ login accepts recovery codes")

    _write(main, m)

    # Update /admin/security/totp page — show recovery-codes section
    tpl = os.path.join(ROOT, "templates/admin_totp.html")
    if os.path.exists(tpl):
        _bak(tpl)
        t = _read(tpl)
        if "s36f-recovery-ui" not in t:
            anchor = "{% endblock %}"
            if anchor in t:
                ui = '''
<!-- s36f-recovery-ui -->
<section class="content" style="padding-top:0;">
<div class="row"><div class="col-md-12">
  <div class="box box-warning">
    <div class="box-header with-border"><h3 class="box-title"><i class="bi bi-key"></i> Recovery Codes</h3></div>
    <div class="box-body">
      <p>Single-use codes for when you lose your 2FA device. Print them and keep them in a safe place.</p>
      {% if totp_enabled %}
        <div class="form-group">
          <label>Enter current 2FA code to regenerate 10 new recovery codes:</label>
          <input id="s36f-recov-code" class="form-control" maxlength="6" placeholder="123456" data-testid="totp-recov-code-input">
        </div>
        <button class="btn btn-warning" onclick="s36fRegen()" data-testid="totp-recov-regen-btn"><i class="bi bi-arrow-clockwise"></i> Regenerate Recovery Codes</button>
        <div id="s36f-recov-out" style="margin-top:12px;"></div>
      {% else %}
        <p class="text-muted">Enable 2FA above first. Recovery codes will be generated automatically.</p>
      {% endif %}
    </div>
  </div>
</div></div>
</section>
<script>
function s36fRegen(){
  var c=document.getElementById('s36f-recov-code').value.trim();
  var out=document.getElementById('s36f-recov-out');
  if(!/^\\d{6}$/.test(c)){ out.innerHTML='<span class="text-danger">Enter a 6-digit code</span>'; return; }
  fetch('/api/admin/totp/regenerate-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
    .then(r=>r.json()).then(j=>{
      if(j.ok && j.codes){
        var html='<div class="alert alert-info"><b>SAVE THESE — they will NOT be shown again:</b><pre style="font-size:16px;letter-spacing:2px;margin:10px 0 0;padding:12px;background:#fff;border:1px solid #d1d5db;border-radius:4px;">'+
          j.codes.map(function(c){return '<span data-testid="totp-recov-code">'+c+'</span>';}).join('\\n')+'</pre>'+
          '<button class="btn btn-xs btn-default" onclick="window.print()"><i class="bi bi-printer"></i> Print</button></div>';
        out.innerHTML=html;
      } else out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
    });
}
// Capture recovery codes shown right after enabling 2FA (returned from /api/admin/totp/enable).
(function(){
  var oldEnable = window.s36dTotpEnable;
  if(typeof oldEnable !== 'function') return;
  window.s36dTotpEnable = function(){
    var c = document.getElementById('totp-code').value.trim();
    var out = document.getElementById('totp-out');
    if(!/^\\d{6}$/.test(c)){ out.innerHTML='<span class="text-danger">Enter a 6-digit code</span>'; return; }
    fetch('/api/admin/totp/enable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
      .then(r=>r.json()).then(j=>{
        if(j.ok){
          var html='<div class="alert alert-success">2FA enabled ✓</div>';
          if(j.recovery_codes && j.recovery_codes.length){
            html+='<div class="alert alert-warning" style="margin-top:8px;"><b>Save your recovery codes NOW:</b><pre style="font-size:16px;letter-spacing:2px;margin:10px 0 0;padding:12px;background:#fff;border:1px solid #d1d5db;border-radius:4px;">'+
              j.recovery_codes.map(function(c){return '<span data-testid="totp-recov-code">'+c+'</span>';}).join('\\n')+'</pre>'+
              '<button class="btn btn-xs btn-default" onclick="window.print()"><i class="bi bi-printer"></i> Print</button></div>';
          }
          out.innerHTML = html;
          setTimeout(function(){location.reload();}, 8000);  // longer so user can save codes
        }
        else out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
      });
  };
})();
</script>
'''
                t = t.replace(anchor, ui + "\n" + anchor, 1)
                _write(tpl, t)
                print("  ✓ admin_totp.html shows recovery codes")


# =========================================================================
# 2. Extract data-mgmt + notifications
# =========================================================================
DATA_MGMT_PATHS = {
    "/api/admin/data-mgmt/sample",
    "/api/admin/data-mgmt/import",
    "/api/admin/data-mgmt/export",
    "/api/admin/data-mgmt/backup",
    "/api/admin/data-mgmt/history",
}
NOTIF_PATHS = {
    "/api/admin/notifications/feed",
    "/api/admin/notifications/summary",
    "/api/admin/notifications/mark-read",
}


def extract_router(label: str, module_name: str, paths: set):
    """Generic extraction using app.include_router(keep existing @app decorators
    — we convert to a router by wrapping with a thin module that re-registers
    the stripped blocks as-is on a child APIRouter)."""
    print(f"\n[2-{module_name}] Extracting {label} routes")
    main_path = os.path.join(ROOT, "main.py")
    target    = os.path.join(ROOT, "routes", f"{module_name}.py")
    if os.path.exists(target):
        print("  ↷ already extracted"); return

    src = _read(main_path)
    ranges = _find_route_ranges(src, paths)
    if not ranges:
        print("  ✗ no matching routes found"); return
    _bak(main_path)

    extracted = _extract_block(src, ranges)
    src = _remove_lines(src, ranges)

    # Re-indent decorators from `@app.` → `@router.`
    extracted_rw = extracted.replace("@app.", "@router.")

    module_src = f'''"""routes/{module_name}.py — s36f refactor (extracted from main.py).

Owns: {sorted(paths)}
"""
from __future__ import annotations
import os, io, json, zipfile, tempfile, subprocess, shutil, datetime as _dt
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, text, select, or_

router = APIRouter(tags=["{module_name.replace('_', '-')}"])


def register(app, *, templates, require_admin, get_db, get_admin_context,
             log_admin_activity, **deps):
    """Register this module's router with `app`. `deps` accepts extra kwargs so
    main.py may pass helpers without breaking the contract.

    Note: the routes below refer to module-level names at runtime. We inject
    them via closure into a dict that functions resolve through globals().
    """
    # Expose shared helpers into this module's globals so the raw-extracted
    # endpoint bodies (which reference `templates`, `require_admin`, etc.
    # as bare names) resolve correctly.
    g = globals()
    g["templates"] = templates
    g["require_admin"] = require_admin
    g["get_db"] = get_db
    g["get_admin_context"] = get_admin_context
    g["log_admin_activity"] = log_admin_activity
    for k, v in deps.items():
        g[k] = v

    app.include_router(router)


# =========================================================================
# EXTRACTED ROUTES (verbatim from main.py, s/app/router/)
# =========================================================================
{extracted_rw}
'''
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _write(target, module_src)

    # Wire at tail of main.py
    wire = f'''

# s36f-{module_name}-router-wired
try:
    from routes import {module_name} as _s36f_{module_name}_mod
    _s36f_{module_name}_mod.register(
        app,
        templates=templates,
        require_admin=require_admin,
        get_db=get_db,
        get_admin_context=get_admin_context,
        log_admin_activity=log_admin_activity,
    )
except Exception as _e:
    import logging
    logging.getLogger("main").exception("{module_name} router wire failed: %s", _e)
'''
    src = src.rstrip() + "\n" + wire + "\n"
    _write(main_path, src)
    print(f"  ✓ {target} written, wire appended, {len(ranges)} routes extracted")


# =========================================================================
# 3. Redemption dashboard
# =========================================================================
def redemption_dashboard():
    print("\n[3] Redemption dashboard")

    # 3a. Create voucher_redemptions table + ORM model
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS voucher_redemptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            voucher_id INTEGER,
            batch_id TEXT,
            code TEXT NOT NULL,
            used_by TEXT,
            mac_address TEXT,
            ip_address TEXT,
            user_agent TEXT,
            duration_minutes INTEGER DEFAULT 0,
            data_cap_mb INTEGER DEFAULT 0,
            plan_name TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_voucher_redemptions_company "
                     "ON voucher_redemptions(company_id, created_at DESC)")
        conn.commit()
        print("  ✓ voucher_redemptions table + index created")
    finally:
        conn.close()

    # 3b. ORM model
    db = os.path.join(ROOT, "database.py")
    src = _read(db)
    if "class VoucherRedemption" not in src:
        _bak(db)
        anchor = 'class CaptivePortalSettings(Base):'
        if anchor in src:
            model = '''class VoucherRedemption(Base):
    """s36f — append-only log of every voucher redemption. Powers the
    Redemption Dashboard and (via webhook) downstream CRM integrations."""
    __tablename__ = "voucher_redemptions"
    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(String, index=True, nullable=False)
    voucher_id = Column(Integer, nullable=True)
    batch_id = Column(String, index=True, nullable=True)
    code = Column(String, index=True, nullable=False)
    used_by = Column(String, nullable=True)
    mac_address = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    duration_minutes = Column(Integer, default=0)
    data_cap_mb = Column(Integer, default=0)
    plan_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


'''
            src = src.replace(anchor, model + anchor, 1)
            _write(db, src)
            print("  ✓ VoucherRedemption ORM model added")

    # 3c. Wire redemption log into /api/vouchers/redeem/{code}
    rv = os.path.join(ROOT, "routes/vouchers.py")
    r = _read(rv)
    if "# s36f-redemption-log" not in r:
        _bak(rv)
        # Insert log creation AFTER mark-used commit, before webhook fire.
        anchor = '''        already_used = v.status == "used"
        if not already_used:
            v.status = "used"
            v.used_by = (request.headers.get("x-mac") or
                         request.headers.get("x-real-ip") or
                         request.client.host if request.client else "") or ""
            v.used_at = datetime.utcnow()
            db.commit()'''
        injection = anchor + '''

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
                print(f"[s36f redemption-log] {_e}")'''
        r = r.replace(anchor, injection, 1)
        _write(rv, r)
        print("  ✓ redemption log wired into /api/vouchers/redeem/{code}")

    # 3d. Dashboard page + API
    # Add /admin/vouchers/redemptions + /api/vouchers/redemptions/list to routes/vouchers.py
    r = _read(rv)
    if "redemptions_page" not in r:
        _bak(rv)
        anchor = "    app.include_router(router)\n"
        extra = '''
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
        return JSONResponse({"ok": True, "rows": [{
            "id": r.id, "code": r.code, "batch_id": r.batch_id,
            "plan_name": r.plan_name, "used_by": r.used_by,
            "mac": r.mac_address, "ip": r.ip_address,
            "ua": r.user_agent,
            "duration_minutes": r.duration_minutes,
            "data_cap_mb": r.data_cap_mb,
            "at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows]})

'''
        r = r.replace(anchor, extra + anchor, 1)
        _write(rv, r)
        print("  ✓ redemption dashboard routes added")

    # 3e. HTML template
    tpl = os.path.join(ROOT, "templates/admin_voucher_redemptions.html")
    if not os.path.exists(tpl):
        with open(tpl, "w") as f:
            f.write('''{% extends "base_admin.html" %}
{% block title %}Voucher Redemptions{% endblock %}
{% block content %}
<section class="content-header">
  <h1>Voucher Redemptions <small>Real-time feed powered by the redeem webhook</small></h1>
  <div style="float:right;margin-top:-32px;">
    <input id="s36f-filter-batch" class="form-control" style="display:inline-block;width:220px;" placeholder="Filter by batch_id" data-testid="redemptions-filter-batch">
    <button class="btn btn-default" onclick="s36fLoad()" data-testid="redemptions-refresh"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
  </div>
</section>
<section class="content">
  <div class="box box-primary">
    <div class="box-body">
      <table class="table table-hover" id="s36f-table" data-testid="redemptions-table">
        <thead><tr>
          <th>#</th><th>When</th><th>Code</th><th>Batch</th><th>Plan</th>
          <th>MAC</th><th>IP</th><th>User Agent</th>
        </tr></thead>
        <tbody id="s36f-rows"><tr><td colspan="8">Loading…</td></tr></tbody>
      </table>
      <div id="s36f-empty" style="display:none;padding:40px;text-align:center;color:#64748b;">
        <i class="bi bi-inbox" style="font-size:40px;"></i>
        <p style="margin-top:12px;font-size:15px;">No redemptions yet. They will appear here once customers start using their vouchers.</p>
      </div>
    </div>
  </div>
</section>
<script>
function s36fFmtTime(iso){
  if(!iso) return '';
  var d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
  return d.toLocaleString();
}
function s36fLoad(){
  var batch = document.getElementById('s36f-filter-batch').value.trim();
  var url = '/api/vouchers/redemptions/list?limit=500' + (batch ? '&batch_id=' + encodeURIComponent(batch) : '');
  fetch(url).then(r => r.json()).then(function(j){
    var tb = document.getElementById('s36f-rows');
    var empty = document.getElementById('s36f-empty');
    if(!j.ok){ tb.innerHTML = '<tr><td colspan="8" class="text-danger">'+(j.error||'error')+'</td></tr>'; return; }
    if(!j.rows.length){ tb.innerHTML = ''; empty.style.display='block'; return; }
    empty.style.display='none';
    tb.innerHTML = j.rows.map(function(r, i){
      return '<tr>'+
        '<td>'+(i+1)+'</td>'+
        '<td>'+s36fFmtTime(r.at)+'</td>'+
        '<td style="font-family:monospace;font-weight:600;color:#7c3aed;">'+r.code+'</td>'+
        '<td>'+(r.batch_id||'—')+'</td>'+
        '<td>'+(r.plan_name||'—')+'</td>'+
        '<td style="font-family:monospace;font-size:12px;">'+(r.mac||'—')+'</td>'+
        '<td style="font-family:monospace;font-size:12px;">'+(r.ip||'—')+'</td>'+
        '<td style="font-size:11px;color:#6b7280;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="'+(r.ua||'')+'">'+(r.ua||'—')+'</td>'+
        '</tr>';
    }).join('');
  }).catch(function(e){
    document.getElementById('s36f-rows').innerHTML = '<tr><td colspan="8" class="text-danger">'+e+'</td></tr>';
  });
}
document.addEventListener('DOMContentLoaded', s36fLoad);
document.getElementById('s36f-filter-batch').addEventListener('keydown', function(e){
  if(e.key==='Enter') s36fLoad();
});
setInterval(s36fLoad, 30000);  // auto-refresh every 30s
</script>
{% endblock %}
''')
        print("  ✓ admin_voucher_redemptions.html template written")

    # 3f. Sidebar link
    base = os.path.join(ROOT, "templates/base_admin.html")
    b = _read(base)
    if "/admin/vouchers/redemptions" not in b:
        _bak(base)
        anchor = '/admin/vouchers'
        idx = b.find(anchor)
        if idx >= 0:
            li_close = b.find("</li>", idx)
            if li_close >= 0:
                link = '''\n                <li><a href="/admin/vouchers/redemptions" data-testid="sidebar-redemptions"><i class="bi bi-receipt"></i> <span>Redemptions</span></a></li>'''
                b = b[:li_close + 5] + link + b[li_close + 5:]
                _write(base, b)
                print("  ✓ sidebar link to redemptions page")


# =========================================================================
def main():
    print("═" * 60)
    print(f" patch_s36f — recovery codes + refactor + redemption dashboard ({TS})")
    print("═" * 60)
    recovery_codes()
    extract_router("data-mgmt", "admin_data_mgmt", DATA_MGMT_PATHS)
    extract_router("notifications", "admin_notifications", NOTIF_PATHS)
    redemption_dashboard()
    print("\n" + "═" * 60)
    print(" DONE — restart isp-admin and run pytest")


if __name__ == "__main__":
    main()
