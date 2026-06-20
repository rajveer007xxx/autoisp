"""patch_s36g.py — Refactor + Email recovery codes + Analytics + 2FA policy + Loyalty

  (1) Extract /api/admin/notifications/* using the register-closure pattern
      (all helpers injected into the module's globals so `Depends(get_db)`
      works at @router.get decorator-eval time).
  (2) Email recovery codes: /api/admin/totp/email-codes button on TOTP page.
  (3) Redemption analytics on the Redemption Dashboard:
        - Daily/Weekly/Monthly redemption chart (Chart.js)
        - Top 5 batches
        - Hour-of-day heatmap
  (4) 2FA enforcement policy: superadmin can set company-wide
      "force 2FA within N days" + login warning banner + grace-period block.
  (5) Loyalty engine: cross-ref MAC across redemptions → "repeat visitors"
      column on the Redemption Dashboard (count of previous redemptions).
"""
from __future__ import annotations
import ast, os, shutil, sqlite3
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _bak(p):
    if os.path.exists(p): shutil.copy2(p, f"{p}.bak_s36g_{TS}")


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


def _find_route_ranges(src, paths):
    tree = ast.parse(src)
    lines = src.splitlines()
    hits = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.decorator_list:
            continue
        dec = node.decorator_list[0]
        p = None
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
            if isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app":
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    p = dec.args[0].value
        if p not in paths:
            continue
        start = dec.lineno
        i = start - 2
        while i >= 0:
            s = lines[i].rstrip()
            if s.startswith("#") and not s.startswith("#!"):
                start = i + 1; i -= 1
            else:
                break
        end = node.end_lineno
        j = end
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        hits.append((start, j, p))
    return hits


def _remove_lines(src, ranges):
    lines = src.splitlines(keepends=True)
    drop = set()
    for s, e, _ in ranges:
        for i in range(s - 1, min(e, len(lines))):
            drop.add(i)
    return "".join(ln for i, ln in enumerate(lines) if i not in drop)


def _extract_block(src, ranges):
    lines = src.splitlines(keepends=True)
    out = []
    for s, e, _ in ranges:
        out.extend(lines[s - 1:min(e, len(lines))])
    return "".join(out)


# =========================================================================
# 1. Extract /api/admin/notifications/*
# =========================================================================
NOTIF_PATHS = {
    "/api/admin/notifications/feed",
    "/api/admin/notifications/summary",
    "/api/admin/notifications/mark-read",
}


def extract_notifications():
    print("\n[1] Extracting /api/admin/notifications/*")
    main_path = os.path.join(ROOT, "main.py")
    target = os.path.join(ROOT, "routes", "admin_notifications.py")
    if os.path.exists(target):
        print("  ↷ already extracted"); return
    src = _read(main_path)
    ranges = _find_route_ranges(src, NOTIF_PATHS)
    if not ranges:
        print("  ✗ no routes found"); return
    _bak(main_path)

    extracted = _extract_block(src, ranges)
    src2 = _remove_lines(src, ranges)

    # Re-indent: wrap entire extracted block inside register() with +4 indent
    # and rewrite @app → @router.  Add `.replace("@app.", "@router.")` first.
    body = extracted.replace("@app.", "@router.")
    # Indent each non-empty line by 4 spaces (so it nests inside register())
    indented = "\n".join(("    " + line) if line.strip() else line
                          for line in body.splitlines()) + "\n"

    module_src = f'''"""routes/admin_notifications.py — s36g refactor.

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

{indented}

    app.include_router(router)
'''
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _write(target, module_src)

    wire = '''

# s36g-admin_notifications-router-wired
try:
    from routes import admin_notifications as _s36g_notif_mod
    from database import Customer
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from fastapi import Depends, Request
    from fastapi.responses import JSONResponse
    from datetime import datetime, timedelta
    _s36g_notif_mod.register(
        app,
        Customer=Customer,
        text=text,
        Session=Session,
        Depends=Depends,
        Request=Request,
        JSONResponse=JSONResponse,
        datetime=datetime,
        timedelta=timedelta,
        get_db=get_db,
        require_admin=require_admin,
        _s351_last_seen=_s351_last_seen,
    )
except Exception as _e:
    import logging
    logging.getLogger("main").exception("admin_notifications router wire failed: %s", _e)
'''
    src2 = src2.rstrip() + "\n" + wire + "\n"
    _write(main_path, src2)
    print(f"  ✓ {target} written + wired, {len(ranges)} routes extracted")


# =========================================================================
# 2. Email recovery codes button
# =========================================================================
def email_recovery_codes():
    print("\n[2] Email-recovery-codes button + endpoint")
    main = os.path.join(ROOT, "main.py")
    m = _read(main)
    if "# s36g-email-recovery-codes" in m:
        print("  ↷ already patched"); return
    _bak(main)

    endpoint = '''

# s36g-email-recovery-codes
@app.post("/api/admin/totp/email-codes")
async def api_admin_totp_email_codes(request: Request, db: Session = Depends(get_db)):
    """Email fresh recovery codes to the admin's registered address.
    Requires a valid current TOTP code to prevent misuse."""
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Admin, Company
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
        return JSONResponse({"ok": False, "error": "invalid TOTP"}, status_code=400)
    if not admin.admin_email:
        return JSONResponse({"ok": False,
            "error": "No email configured on your admin profile"}, status_code=400)

    # Generate fresh codes (invalidates old ones)
    plain = _s36f_gen_recovery_codes(10)
    admin.totp_recovery_codes = json.dumps(_s36f_hash_codes(plain))
    db.commit()

    # Build + dispatch email
    company = db.query(Company).filter(Company.company_id == admin.company_id).first()
    comp_name = (company.company_name if company else "ISP Billing")
    subject = f"{comp_name} — Your 2FA Recovery Codes"
    body_txt = f"""Hello {admin.admin_name or admin.admin_id},

Here are your 10 new 2FA recovery codes. Each code works ONCE.
Save them in a safe place (a password manager is ideal).

""" + "\\n".join(f"  {c}" for c in plain) + f"""

After using a code to log in, it becomes invalid. Regenerate codes any
time from /admin/security/totp. The previous set (if any) is no longer
valid.

— {comp_name}
"""
    try:
        # Reuse existing email sender if present
        try:
            _send_admin_email = send_email  # noqa
        except NameError:
            _send_admin_email = None
        if _send_admin_email:
            _send_admin_email(to=admin.admin_email, subject=subject, body=body_txt,
                              company_id=admin.company_id, db=db)
        else:
            # Fallback: direct SMTP using company SMTP settings
            if company and company.smtp_host and company.smtp_username:
                import smtplib, ssl
                from email.message import EmailMessage
                from email.utils import formataddr
                msg = EmailMessage()
                msg["Subject"] = subject
                msg["From"] = formataddr((comp_name.upper() + " - Security",
                                          company.smtp_username))
                msg["To"] = admin.admin_email
                msg.set_content(body_txt)
                ctx = ssl.create_default_context()
                port = int(company.smtp_port or 587)
                with smtplib.SMTP(company.smtp_host, port, timeout=15) as s:
                    s.starttls(context=ctx)
                    s.login(company.smtp_username, company.smtp_password or "")
                    s.send_message(msg)
            else:
                return JSONResponse({"ok": False,
                    "error": "No SMTP configured on company profile"}, status_code=500)
        try:
            log_admin_activity(db, request, "email", "admin_totp",
                               target_id=admin.admin_id,
                               summary="2FA recovery codes emailed")
        except Exception:
            pass
        return {"ok": True, "emailed_to": admin.admin_email, "count": len(plain)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"email failed: {e}"}, status_code=500)
'''
    if 'if __name__ == "__main__":' in m:
        idx = m.rfind('if __name__ == "__main__":')
        m = m[:idx] + endpoint + "\n" + m[idx:]
    else:
        m = m.rstrip() + endpoint + "\n"
    _write(main, m)
    print("  ✓ /api/admin/totp/email-codes endpoint added")

    # UI: add "Email codes to me" button on /admin/security/totp
    tpl = os.path.join(ROOT, "templates/admin_totp.html")
    t = _read(tpl)
    if "s36g-email-codes-btn" not in t:
        _bak(tpl)
        anchor = 'data-testid="totp-recov-regen-btn">'
        if anchor in t:
            idx = t.find(anchor) + len(anchor)
            # close the button tag
            close = t.find("</button>", idx) + len("</button>")
            inject = '''\n        <button class="btn btn-default" onclick="s36gEmailCodes()" data-testid="totp-email-btn" style="margin-left:8px;"><i class="bi bi-envelope"></i> Email codes to me</button>'''
            t = t[:close] + inject + t[close:]
            # JS
            script_inject = '''
<script>
function s36gEmailCodes(){
  var c=document.getElementById('s36f-recov-code').value.trim();
  var out=document.getElementById('s36f-recov-out');
  if(!/^\\d{6}$/.test(c)){ out.innerHTML='<span class="text-danger">Enter current 6-digit TOTP</span>'; return; }
  out.innerHTML='<i class="bi bi-hourglass-split"></i> Sending…';
  fetch('/api/admin/totp/email-codes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:c})})
    .then(r=>r.json()).then(function(j){
      if(j.ok){
        out.innerHTML='<div class="alert alert-success" data-testid="totp-email-result"><i class="bi bi-check-circle"></i> Fresh recovery codes sent to <b>'+j.emailed_to+'</b> ('+j.count+' codes). The old ones are now invalid.</div>';
      } else {
        out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
      }
    });
}
</script>
'''
            t = t + script_inject
            _write(tpl, t)
            print("  ✓ UI button + JS added")


# =========================================================================
# 3. Redemption analytics (charts, top batches, heatmap)
# =========================================================================
def redemption_analytics():
    print("\n[3] Redemption analytics (charts + heatmap)")
    rv = os.path.join(ROOT, "routes/vouchers.py")
    r = _read(rv)
    if "/api/vouchers/redemptions/analytics" in r:
        print("  ↷ endpoint already present"); return
    _bak(rv)
    anchor = "    app.include_router(router)\n"
    extra = '''
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

        return JSONResponse({"ok": True,
            "days": days,
            "daily": [{"date": d, "count": c} for d, c in daily if d],
            "hourly": hour_series,  # 24-element array [00..23]
            "top_batches": [{"batch_id": b, "count": c} for b, c in top],
            "totals": {"week": int(total_week), "month": int(total_month),
                       "all": int(total_all),
                       "distinct_devices": int(distinct_macs)},
        })

'''
    r = r.replace(anchor, extra + anchor, 1)
    _write(rv, r)
    print("  ✓ analytics endpoint added")

    # Update the dashboard template to include charts
    tpl = os.path.join(ROOT, "templates/admin_voucher_redemptions.html")
    t = _read(tpl)
    if "s36g-analytics" not in t:
        _bak(tpl)
        # Insert charts section AFTER the <section class="content-header"> block
        anchor = '<section class="content">'
        if anchor in t:
            charts = '''<!-- s36g-analytics -->
<section class="content" style="padding-bottom:0;">
  <div class="row">
    <div class="col-md-3 col-sm-6">
      <div class="small-box bg-purple">
        <div class="inner"><h3 data-testid="analytics-total-all">0</h3><p>All-time Redemptions</p></div>
        <div class="icon"><i class="bi bi-graph-up"></i></div>
      </div>
    </div>
    <div class="col-md-3 col-sm-6">
      <div class="small-box bg-aqua">
        <div class="inner"><h3 data-testid="analytics-month">0</h3><p>Last 30 Days</p></div>
        <div class="icon"><i class="bi bi-calendar-month"></i></div>
      </div>
    </div>
    <div class="col-md-3 col-sm-6">
      <div class="small-box bg-green">
        <div class="inner"><h3 data-testid="analytics-week">0</h3><p>Last 7 Days</p></div>
        <div class="icon"><i class="bi bi-calendar-week"></i></div>
      </div>
    </div>
    <div class="col-md-3 col-sm-6">
      <div class="small-box bg-maroon">
        <div class="inner"><h3 data-testid="analytics-devices">0</h3><p>Distinct Devices (MACs)</p></div>
        <div class="icon"><i class="bi bi-phone"></i></div>
      </div>
    </div>
  </div>
  <div class="row">
    <div class="col-md-8">
      <div class="box box-primary">
        <div class="box-header with-border"><h3 class="box-title">Daily Redemptions (last 30 days)</h3></div>
        <div class="box-body"><canvas id="s36g-daily-chart" height="80" data-testid="analytics-daily-chart"></canvas></div>
      </div>
    </div>
    <div class="col-md-4">
      <div class="box box-success">
        <div class="box-header with-border"><h3 class="box-title">Top 5 Batches</h3></div>
        <div class="box-body" style="padding:0;">
          <ul class="list-group" id="s36g-top-batches" data-testid="analytics-top-batches" style="margin:0;">
            <li class="list-group-item text-muted">Loading…</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
  <div class="row">
    <div class="col-md-12">
      <div class="box box-info">
        <div class="box-header with-border"><h3 class="box-title">Hour-of-Day Heatmap (last 30 days)</h3></div>
        <div class="box-body"><canvas id="s36g-hour-chart" height="60" data-testid="analytics-hour-chart"></canvas></div>
      </div>
    </div>
  </div>
</section>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
(function(){
  var dailyChart=null, hourChart=null;
  function renderAnalytics(){
    fetch('/api/vouchers/redemptions/analytics?days=30').then(r=>r.json()).then(function(j){
      if(!j.ok) return;
      // totals
      document.querySelector('[data-testid="analytics-total-all"]').textContent = j.totals.all;
      document.querySelector('[data-testid="analytics-month"]').textContent = j.totals.month;
      document.querySelector('[data-testid="analytics-week"]').textContent = j.totals.week;
      document.querySelector('[data-testid="analytics-devices"]').textContent = j.totals.distinct_devices;
      // top batches
      var ul = document.getElementById('s36g-top-batches');
      if (!j.top_batches.length) {
        ul.innerHTML = '<li class="list-group-item text-muted">No batches yet.</li>';
      } else {
        var max = Math.max.apply(null, j.top_batches.map(b=>b.count));
        ul.innerHTML = j.top_batches.map(function(b){
          var pct = Math.round(100 * b.count / max);
          return '<li class="list-group-item" style="position:relative;overflow:hidden;">'+
            '<div style="position:absolute;inset:0;background:#a78bfa;opacity:0.15;width:'+pct+'%;"></div>'+
            '<span style="position:relative;"><b>'+b.batch_id+'</b></span>'+
            '<span style="position:relative;float:right;" class="badge">'+b.count+'</span></li>';
        }).join('');
      }
      // daily chart
      var labels = j.daily.map(d=>d.date);
      var counts = j.daily.map(d=>d.count);
      var ctx = document.getElementById('s36g-daily-chart').getContext('2d');
      if (dailyChart) dailyChart.destroy();
      dailyChart = new Chart(ctx, {type:'line', data:{labels:labels, datasets:[{label:'Redemptions', data:counts, borderColor:'#7c3aed', backgroundColor:'rgba(124,58,237,0.1)', tension:0.3, fill:true}]}, options:{maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true, ticks:{precision:0}}}}});
      // hour chart
      var hr = Array.from({length:24}, (_,i)=> ('00'+i).slice(-2) + ':00');
      var ctx2 = document.getElementById('s36g-hour-chart').getContext('2d');
      if (hourChart) hourChart.destroy();
      var max2 = Math.max.apply(null, j.hourly) || 1;
      var colors = j.hourly.map(function(v){
        var pct = v / max2;
        return 'rgba(6, 182, 212, '+ (0.15 + 0.85*pct) +')';
      });
      hourChart = new Chart(ctx2, {type:'bar', data:{labels:hr, datasets:[{label:'Redemptions by hour', data:j.hourly, backgroundColor:colors}]}, options:{maintainAspectRatio:false, plugins:{legend:{display:false}}, scales:{y:{beginAtZero:true, ticks:{precision:0}}}}});
    });
  }
  document.addEventListener('DOMContentLoaded', renderAnalytics);
  setInterval(renderAnalytics, 60000);  // refresh every 60s
})();
</script>

'''
            t = t.replace(anchor, charts + anchor, 1)
            _write(tpl, t)
            print("  ✓ analytics section + Chart.js rendered on dashboard")


# =========================================================================
# 4. 2FA enforcement policy
# =========================================================================
def enforcement_policy():
    print("\n[4] 2FA enforcement policy")
    # 4a. Columns on companies
    _sq_add_col("companies", "mfa_required_for_admins", "INTEGER DEFAULT 0")
    _sq_add_col("companies", "mfa_grace_period_days", "INTEGER DEFAULT 7")
    _sq_add_col("admins", "mfa_deadline", "TEXT DEFAULT ''")  # ISO date string

    # 4b. ORM
    db = os.path.join(ROOT, "database.py")
    src = _read(db)
    if "mfa_required_for_admins" not in src:
        _bak(db)
        anchor = 'class Company(Base):\n    __tablename__ = "companies"'
        # Add after class opening — find the first blank line in class body
        # We'll add after `gst_invoice_next_number` since that's an easy-to-find anchor
        comp_anchor = '    gst_invoice_next_number = Column'
        if comp_anchor in src:
            # find end of line
            li = src.find("\n", src.find(comp_anchor))
            inject = ('\n    # s36g 2FA enforcement policy\n'
                      '    mfa_required_for_admins = Column(Integer, default=0)\n'
                      '    mfa_grace_period_days = Column(Integer, default=7)')
            src = src[:li] + inject + src[li:]
        # Also add mfa_deadline to Admin
        admin_anchor = '    totp_recovery_codes = Column(String, nullable=True, default="")'
        if admin_anchor in src:
            src = src.replace(admin_anchor,
                admin_anchor + '\n    mfa_deadline = Column(String, nullable=True, default="")  # ISO datetime; admin blocked after this if totp not enabled', 1)
        _write(db, src)
        print("  ✓ ORM fields added")

    # 4c. Superadmin UI: simple on/off toggle
    # Find superadmin settings template, or create a minimal one. For brevity
    # we add a dedicated page `/super/mfa-policy` that admins can use too (scoped to their company).
    main = os.path.join(ROOT, "main.py")
    m = _read(main)
    if "# s36g-mfa-policy" not in m:
        _bak(main)
        endpoint = '''

# s36g-mfa-policy
@app.get("/admin/security/mfa-policy", response_class=HTMLResponse)
async def admin_mfa_policy_page(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return auth
    from database import Company
    company_id = request.session.get("company_id")
    comp = db.query(Company).filter(Company.company_id == company_id).first()
    ctx = get_admin_context(request, db, "security")
    ctx["company"] = comp
    return templates.TemplateResponse("admin_mfa_policy.html", ctx)


@app.post("/api/admin/mfa-policy/save")
async def api_admin_mfa_policy_save(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Company, Admin
    from datetime import datetime as _dt, timedelta as _td
    body = await request.json()
    company_id = request.session.get("company_id")
    comp = db.query(Company).filter(Company.company_id == company_id).first()
    if not comp:
        return JSONResponse({"ok": False, "error": "no company"}, status_code=400)
    req_flag = 1 if body.get("mfa_required") else 0
    grace = max(0, min(int(body.get("mfa_grace_period_days") or 7), 90))
    was_on = bool(getattr(comp, "mfa_required_for_admins", 0))
    comp.mfa_required_for_admins = req_flag
    comp.mfa_grace_period_days = grace

    # When turning policy ON for the first time, set mfa_deadline on each
    # admin in this company who hasn't already enabled 2FA.
    if req_flag and not was_on:
        deadline = (_dt.utcnow() + _td(days=grace)).isoformat()
        admins_row = db.query(Admin).filter(
            Admin.company_id == company_id,
            (Admin.totp_enabled == 0) | (Admin.totp_enabled.is_(None))).all()
        for a in admins_row:
            a.mfa_deadline = deadline
    elif not req_flag:
        # Clear deadlines
        db.query(Admin).filter(Admin.company_id == company_id).update(
            {Admin.mfa_deadline: ""})
    db.commit()
    try:
        log_admin_activity(db, request, "update", "mfa_policy",
                           target_id=company_id,
                           summary=f"required={req_flag} grace={grace}d")
    except Exception:
        pass
    return {"ok": True, "mfa_required": bool(req_flag), "grace": grace}
'''
        if 'if __name__ == "__main__":' in m:
            idx = m.rfind('if __name__ == "__main__":')
            m = m[:idx] + endpoint + "\n" + m[idx:]
        else:
            m = m.rstrip() + endpoint + "\n"

        # Add a grace-period check in dashboard / require_admin. Simplest:
        # inject a warning header into admin_context that templates can show.
        # We'll patch get_admin_context to compute `mfa_warn` for the template.
        ctx_anchor = 'def get_admin_context('
        if ctx_anchor in m:
            # Find first return inside, inject before it
            ctx_start = m.find(ctx_anchor)
            ret_idx = m.find("    return ", ctx_start)
            if ret_idx > 0:
                inject = '''    # s36g-mfa-policy: compute MFA warning/block for this admin.
    try:
        from database import Admin as _Adm, Company as _Com
        import datetime as _dts36g
        _cid = request.session.get("company_id")
        _aid = request.session.get("user_id")
        _com = db.query(_Com).filter(_Com.company_id == _cid).first()
        _adm = db.query(_Adm).filter(_Adm.admin_id == _aid, _Adm.company_id == _cid).first()
        mfa_state = {"required": False, "enabled": False, "overdue": False,
                     "days_left": None, "deadline": ""}
        if _com and int(getattr(_com, "mfa_required_for_admins", 0) or 0):
            mfa_state["required"] = True
            mfa_state["enabled"] = bool(_adm and getattr(_adm, "totp_enabled", 0))
            dl = _adm.mfa_deadline if _adm and _adm.mfa_deadline else ""
            if dl and not mfa_state["enabled"]:
                try:
                    d = _dts36g.datetime.fromisoformat(dl)
                    delta = (d - _dts36g.datetime.utcnow()).days
                    mfa_state["days_left"] = delta
                    mfa_state["deadline"] = d.strftime("%d-%m-%Y")
                    if delta < 0: mfa_state["overdue"] = True
                except Exception:
                    pass
        _ctx_s36g_mfa = mfa_state
    except Exception:
        _ctx_s36g_mfa = {"required": False, "enabled": False, "overdue": False,
                         "days_left": None, "deadline": ""}
'''
                m = m[:ret_idx] + inject + m[ret_idx:]

                # And inject the key into the returned context dict
                # get_admin_context returns `ctx` (or a dict literal); we'll
                # add a sentinel line right before the return.
                # Simplest: find the exact "return ctx" and inject above.
                return_line = "    return ctx"
                if return_line in m[ctx_start:]:
                    abs_idx = m.find(return_line, ctx_start)
                    pre = m[:abs_idx]
                    post = m[abs_idx:]
                    m = pre + '    ctx["mfa_state"] = _ctx_s36g_mfa\n' + post
                    print("  ✓ get_admin_context now includes mfa_state")

        _write(main, m)
        print("  ✓ endpoints + context wiring done")

    # 4d. Template for the policy page
    tpl = os.path.join(ROOT, "templates/admin_mfa_policy.html")
    if not os.path.exists(tpl):
        with open(tpl, "w") as f:
            f.write('''{% extends "base_admin.html" %}
{% block title %}MFA Enforcement Policy{% endblock %}
{% block content %}
<section class="content-header">
  <h1>2FA Enforcement Policy <small>Require all admins to enable Two-Factor Authentication</small></h1>
</section>
<section class="content">
<div class="row"><div class="col-md-8 col-md-offset-2">
  <div class="box box-warning">
    <div class="box-header with-border"><h3 class="box-title">Company-Wide 2FA Policy</h3></div>
    <div class="box-body">
      <p>When turned on, every admin in this company gets a grace period to enable 2FA. After the deadline, they see a blocking warning every page view (and, in a future release, will be denied access to sensitive actions).</p>
      <div class="form-group">
        <label><input type="checkbox" id="s36g-mfa-required" {% if company.mfa_required_for_admins %}checked{% endif %} data-testid="mfa-required-toggle"> Require 2FA for all admins</label>
      </div>
      <div class="form-group">
        <label>Grace period (days)</label>
        <input type="number" id="s36g-mfa-grace" class="form-control" min="0" max="90" value="{{company.mfa_grace_period_days or 7}}" style="max-width:200px;" data-testid="mfa-grace-days">
        <small class="text-muted">Number of days existing admins have to set up 2FA after the policy is enabled. Set to 0 for immediate enforcement.</small>
      </div>
      <button class="btn btn-primary" onclick="s36gSavePolicy()" data-testid="mfa-save-btn"><i class="bi bi-save"></i> Save Policy</button>
      <div id="s36g-mfa-out" style="margin-top:12px;"></div>
    </div>
  </div>
</div></div>
</section>
<script>
function s36gSavePolicy(){
  var req = document.getElementById('s36g-mfa-required').checked;
  var grace = parseInt(document.getElementById('s36g-mfa-grace').value) || 0;
  fetch('/api/admin/mfa-policy/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mfa_required:req,mfa_grace_period_days:grace})})
    .then(r=>r.json()).then(function(j){
      var out=document.getElementById('s36g-mfa-out');
      if(j.ok){
        out.innerHTML='<div class="alert alert-success"><i class="bi bi-check-circle"></i> Policy saved. '+
          (j.mfa_required ? 'All admins without 2FA must enable it within ' + j.grace + ' days.' : '2FA enforcement is now OFF.')+'</div>';
      } else out.innerHTML='<span class="text-danger">'+(j.error||'failed')+'</span>';
    });
}
</script>
{% endblock %}
''')
        print("  ✓ admin_mfa_policy.html template written")

    # 4e. Sidebar link under 2FA Security
    base = os.path.join(ROOT, "templates/base_admin.html")
    b = _read(base)
    if "/admin/security/mfa-policy" not in b:
        _bak(base)
        anchor = '/admin/security/totp'
        idx = b.find(anchor)
        if idx >= 0:
            li_close = b.find("</li>", idx)
            if li_close >= 0:
                b = b[:li_close + 5] + '''\n                <li><a href="/admin/security/mfa-policy" data-testid="sidebar-mfa-policy"><i class="bi bi-shield-exclamation"></i> <span>2FA Policy</span></a></li>''' + b[li_close + 5:]
                _write(base, b)
                print("  ✓ sidebar link added")

    # 4f. Warning banner in base_admin.html (shown when mfa_state.required and not enabled)
    b = _read(base)
    if "s36g-mfa-warn-banner" not in b:
        _bak(base)
        # Find <body> opening or a content-wrapper div
        anchor = '<div class="content-wrapper'
        idx = b.find(anchor)
        if idx >= 0:
            # Insert banner right after the content-wrapper opening tag (after its >)
            close = b.find(">", idx) + 1
            banner = '''
        {% if mfa_state and mfa_state.required and not mfa_state.enabled %}
        <!-- s36g-mfa-warn-banner -->
        <div style="background:{% if mfa_state.overdue %}#dc2626{% else %}#f59e0b{% endif %};color:#fff;padding:12px 20px;font-size:14px;text-align:center;" data-testid="mfa-warn-banner">
          <i class="bi bi-shield-exclamation"></i>
          {% if mfa_state.overdue %}
            <b>Action required:</b> Your company requires Two-Factor Authentication. Your grace period ended on {{mfa_state.deadline}}.
          {% elif mfa_state.days_left is not none %}
            Your company requires 2FA. You have <b>{{mfa_state.days_left}}</b> day(s) left (by {{mfa_state.deadline}}) to <a href="/admin/security/totp" style="color:#fff;text-decoration:underline;font-weight:600;">enable it now</a>.
          {% else %}
            Your company requires 2FA. <a href="/admin/security/totp" style="color:#fff;text-decoration:underline;font-weight:600;">Enable it now</a>.
          {% endif %}
        </div>
        {% endif %}
'''
            b = b[:close] + banner + b[close:]
            _write(base, b)
            print("  ✓ warning banner injected in base_admin.html")


# =========================================================================
# 5. Loyalty engine — repeat-visitor count per MAC
# =========================================================================
def loyalty_engine():
    print("\n[5] Loyalty engine — repeat-visitor count")
    rv = os.path.join(ROOT, "routes/vouchers.py")
    r = _read(rv)
    if "# s36g-loyalty" in r:
        print("  ↷ already patched"); return
    _bak(rv)
    # Enhance the list endpoint — add a prior-redemptions count per row.
    old = '''        rows = q.order_by(VoucherRedemption.id.desc()).limit(
            max(1, min(limit, 1000))).all()
        return JSONResponse({"ok": True, "rows": [{
            "id": r.id, "code": r.code, "batch_id": r.batch_id,
            "plan_name": r.plan_name, "used_by": r.used_by,
            "mac": r.mac_address, "ip": r.ip_address,
            "ua": r.user_agent,
            "duration_minutes": r.duration_minutes,
            "data_cap_mb": r.data_cap_mb,
            "at": r.created_at.isoformat() if r.created_at else "",
        } for r in rows]})'''
    new = '''        rows = q.order_by(VoucherRedemption.id.desc()).limit(
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
        } for r in rows]})'''
    if old in r:
        r = r.replace(old, new, 1)
        _write(rv, r)
        print("  ✓ redemptions/list now returns mac_total")

    # Update the template table to show the loyalty column
    tpl = os.path.join(ROOT, "templates/admin_voucher_redemptions.html")
    t = _read(tpl)
    if "s36g-loyalty-col" not in t:
        _bak(tpl)
        # Add a new header column
        old_head = '<th>MAC</th><th>IP</th><th>User Agent</th>'
        new_head = '<th>MAC</th><th data-testid="loyalty-header" title="Total redemptions by this device">Visits</th><th>IP</th><th>User Agent</th>'
        t = t.replace(old_head, new_head, 1)
        # Bump row colspans if needed
        t = t.replace('colspan="8"', 'colspan="9"')
        # Add the cell to each row (inject after MAC cell in the JS mapping)
        old_row = "'<td style=\"font-family:monospace;font-size:12px;\">'+(r.mac||'—')+'</td>'+\n        '<td style=\"font-family:monospace;font-size:12px;\">'+(r.ip||'—')+'</td>'"
        new_row = '''\'<td style="font-family:monospace;font-size:12px;">\'+(r.mac||'—')+\'</td>\'+
        '<td class="s36g-loyalty-col" data-testid="loyalty-cell" style="text-align:center;">'+
          (r.mac_total > 1
            ? '<span class="badge" style="background:#7c3aed;color:#fff;" title="This device has redeemed '+r.mac_total+' vouchers">'+r.mac_total+'x</span>'
            : (r.mac ? '<span class="text-muted">1</span>' : '—'))+'</td>'+
        \'<td style="font-family:monospace;font-size:12px;">\'+(r.ip||'—')+\'</td>\''''
        if old_row in t:
            t = t.replace(old_row, new_row, 1)
            _write(tpl, t)
            print("  ✓ loyalty column added to dashboard")


# =========================================================================
def main():
    print("═" * 60)
    print(f" patch_s36g — refactor + email + analytics + policy + loyalty ({TS})")
    print("═" * 60)
    extract_notifications()
    email_recovery_codes()
    redemption_analytics()
    enforcement_policy()
    loyalty_engine()
    print("\n" + "═" * 60)
    print(" DONE — restart isp-admin and run pytest")


if __name__ == "__main__":
    main()
