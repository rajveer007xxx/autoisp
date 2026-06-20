"""
patch_s36b9.py — Completes the final backlog items:

  P0  (1) RouterOSClient.upload_file_sftp()  — push arbitrary files (e.g.
           captive-portal HTML) to a MikroTik NAS over SFTP.
       (2) /api/captive-portal/push-to-nas/{nas_id} endpoint + UI button
           on the captive-portal designer page.

  P1  (3) Hotspot dashboard widgets: active hotspot sessions, vouchers sold
           today, vouchers redeemed today, expired-pending-cleanup.

  P2  (4) Extract /admin/activity-log + /api/activity-log/list into
           routes/activity_log.py (demonstrates the router-extraction
           pattern). The `log_admin_activity()` helper stays in main.py
           because it's called from dozens of endpoints.

Idempotent: re-running is safe — each insertion is guarded by a marker.
"""
from __future__ import annotations
import os, shutil, re
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def _bak(path: str, tag: str) -> None:
    if os.path.exists(path):
        bk = f"{path}.bak_{tag}_{TS}"
        shutil.copy2(path, bk)
        print(f"  ✓ backup: {bk}")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _inject_once(path: str, marker: str, payload: str, anchor: str,
                 after: bool = True, tag: str = "s36b9") -> bool:
    src = _read(path)
    if marker in src:
        print(f"  ↷ already patched (marker present): {path}")
        return False
    if anchor not in src:
        print(f"  ✗ anchor not found in {path}: {anchor!r}")
        return False
    _bak(path, tag)
    if after:
        src = src.replace(anchor, anchor + payload, 1)
    else:
        src = src.replace(anchor, payload + anchor, 1)
    _write(path, src)
    print(f"  ✓ patched {path}")
    return True


# =========================================================================
# P0.1 — RouterOSClient.upload_file_sftp
# =========================================================================
def patch_routeros_provision() -> None:
    print("\n[P0.1] Patching routeros_provision.py — add upload_file_sftp()")
    path = os.path.join(ROOT, "routeros_provision.py")

    # Insert new method right before __exit__ of RouterOSClient
    marker = "# s36b9-upload-file-sftp"
    anchor = "    def __exit__(self, *_):\n        if self._api:\n            self._api.close()\n        if self._ssh:\n            self._ssh.close()\n"
    payload = '''
    # s36b9-upload-file-sftp ------------------------------------------------
    def upload_file_sftp(self, remote_name: str, content: bytes) -> Dict:
        """Upload arbitrary bytes to the RouterOS file-system over SFTP.

        `remote_name` is the path as RouterOS sees it, e.g.
        `hotspot/login.html` or `isp-billing/cp-login.html`. Any parent
        directories are auto-created.

        Works only with SSH-enabled NAS (paramiko SFTP). For API-only
        devices, the caller should enable SSH on the NAS (RouterOS always
        bundles SSH alongside API).
        """
        if self.dry_run:
            self.commands.append(f"/tool fetch (SFTP upload {remote_name}, {len(content)} bytes)")
            return {"dry_run": True, "remote": remote_name, "size": len(content)}

        # We need a SSH client. Prefer existing _ssh transport; otherwise
        # spin up a temporary paramiko connection just for this upload.
        ssh_client = None
        temp_ssh = False
        try:
            if self._ssh is not None:
                ssh_client = self._ssh._client  # reuse
            else:
                if not _PARAMIKO_OK:
                    return {"success": False, "error": "paramiko not installed"}
                ssh_client = paramiko.SSHClient()
                ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh_client.connect(
                    hostname=self.nas.ip_address,
                    port=int(self.nas.ssh_port or 22),
                    username=self.nas.api_username or "admin",
                    password=self.nas.api_password or "",
                    timeout=30, allow_agent=False, look_for_keys=False,
                )
                temp_ssh = True

            sftp = ssh_client.open_sftp()
            try:
                # Ensure parent dirs exist (RouterOS supports `mkdir` over SFTP).
                parts = remote_name.strip("/").split("/")
                if len(parts) > 1:
                    cur = ""
                    for seg in parts[:-1]:
                        cur = f"{cur}/{seg}" if cur else seg
                        try:
                            sftp.stat(cur)
                        except IOError:
                            try:
                                sftp.mkdir(cur)
                            except IOError:
                                pass  # best-effort
                with sftp.open(remote_name, "wb") as fh:
                    fh.write(content)
                size = len(content)
                self.commands.append(f"sftp put {remote_name} ({size} bytes)")
                return {"success": True, "remote": remote_name, "size": size}
            finally:
                sftp.close()
        except Exception as e:
            return {"success": False, "error": f"sftp upload failed: {e}"}
        finally:
            if temp_ssh and ssh_client is not None:
                try:
                    ssh_client.close()
                except Exception:
                    pass
    # --------------------------------------------------------------------

'''
    _inject_once(path, marker, payload, anchor, after=False)


# =========================================================================
# P0.2 — /api/captive-portal/push-to-nas/{nas_id} + UI button
# =========================================================================
def patch_captive_portal_push() -> None:
    print("\n[P0.2] Adding /api/captive-portal/push-to-nas/{nas_id}")
    path = os.path.join(ROOT, "main.py")

    marker = "# s36b9-captive-portal-push-to-nas"
    # Anchor: right after existing captive-portal save endpoint
    # We'll append just before /hotspot/login.html preview (so they stay grouped).
    anchor_text = '@app.get("/hotspot/login.html", response_class=HTMLResponse)'
    payload = f'''# s36b9-captive-portal-push-to-nas --------------------------------------
@app.post("/api/captive-portal/push-to-nas/{{nas_id}}")
async def api_captive_portal_push_to_nas(nas_id: int, request: Request,
                                          db: Session = Depends(get_db)):
    """Render the current captive-portal login HTML and SFTP-upload it to
    the selected NAS. Target path on the router is `hotspot/login.html`
    which overrides the default MikroTik captive page."""
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    company_id = request.session.get("company_id")

    from database import CaptivePortalSettings
    from radius_network import NasDevice

    nas = db.query(NasDevice).filter(
        NasDevice.id == nas_id,
        NasDevice.company_id == company_id).first()
    if not nas:
        return JSONResponse({{"success": False, "message": "NAS not found"}}, status_code=404)

    portal = db.query(CaptivePortalSettings).filter(
        CaptivePortalSettings.company_id == company_id).first()
    if not portal:
        return JSONResponse({{"success": False,
            "message": "Captive portal not configured yet"}}, status_code=400)

    # Render the same template used by /hotspot/login.html
    try:
        tpl = templates.get_template("hotspot_login.html")
        html = tpl.render({{"portal": portal, "request": request}})
    except Exception as e:
        return JSONResponse({{"success": False,
            "message": f"render failed: {{e}}"}}, status_code=500)

    # Push via SFTP
    try:
        from routeros_provision import RouterOSClient
        with RouterOSClient(nas) as cli:
            res = cli.upload_file_sftp("hotspot/login.html", html.encode("utf-8"))
    except Exception as e:
        return JSONResponse({{"success": False,
            "message": f"connection failed: {{e}}"}}, status_code=500)

    # Audit trail
    try:
        log_admin_activity(db, request, "push", "captive_portal",
                           target_id=str(nas_id),
                           note=f"pushed to NAS {{nas.name}} ({{nas.ip_address}})")
    except Exception:
        pass

    if not res.get("success") and not res.get("dry_run"):
        return JSONResponse({{"success": False,
            "message": res.get("error") or "upload failed"}}, status_code=502)
    return {{"success": True, "nas": nas.name, "size": res.get("size"),
            "remote": res.get("remote"), "html_bytes": len(html)}}


# /api/captive-portal/nas-list — small helper for the designer UI.
@app.get("/api/captive-portal/nas-list")
async def api_captive_portal_nas_list(request: Request, db: Session = Depends(get_db)):
    auth_check = require_admin(request)
    if auth_check:
        return auth_check
    company_id = request.session.get("company_id")
    from radius_network import NasDevice
    rows = db.query(NasDevice).filter(
        NasDevice.company_id == company_id,
        NasDevice.status == "Active").order_by(NasDevice.name).all()
    return {{"success": True, "rows": [
        {{"id": r.id, "name": r.name, "ip": r.ip_address,
         "use_ssh": bool(getattr(r, "use_ssh", False))}}
        for r in rows]}}


'''
    _inject_once(path, marker, payload, anchor_text, after=False)


# =========================================================================
# P0.2b — UI block on admin_captive_portal.html
# =========================================================================
def patch_captive_portal_ui() -> None:
    print("\n[P0.2b] Adding Push-to-NAS UI on admin_captive_portal.html")
    path = os.path.join(ROOT, "templates/admin_captive_portal.html")

    marker = "s36b9-push-to-nas-block"
    # Find a good anchor: the Save button inside the design panel.
    src = _read(path)
    if marker in src:
        print("  ↷ already patched"); return

    anchor = "{% endblock %}\n"  # end of content block (we'll append just before)
    if anchor not in src:
        print(f"  ✗ anchor not found"); return

    payload = '''
<!-- s36b9-push-to-nas-block -->
<section class="content" style="padding-top:0;">
  <div class="box box-warning" style="border-top-color:#f59e0b;">
    <div class="box-header with-border">
      <h3 class="box-title"><i class="bi bi-router"></i> Push to MikroTik NAS</h3>
    </div>
    <div class="box-body">
      <p style="color:#64748b;font-size:13px;margin:0 0 10px;">
        Upload the current captive-portal design to a MikroTik router as
        <code>hotspot/login.html</code>. The NAS must have SSH enabled.
      </p>
      <div class="row">
        <div class="col-md-6">
          <select id="s36b9_nas_select" class="form-control" data-testid="cp-nas-select">
            <option value="">— loading NAS devices —</option>
          </select>
        </div>
        <div class="col-md-6">
          <button id="s36b9_push_btn" class="btn btn-warning"
                  data-testid="cp-push-btn" disabled>
            <i class="bi bi-cloud-upload"></i> Push Captive Portal to selected NAS
          </button>
        </div>
      </div>
      <div id="s36b9_push_result" style="margin-top:10px;font-size:13px;"></div>
    </div>
  </div>
</section>
<script>
(function(){
  var sel = document.getElementById('s36b9_nas_select');
  var btn = document.getElementById('s36b9_push_btn');
  var out = document.getElementById('s36b9_push_result');
  fetch('/api/captive-portal/nas-list').then(r=>r.json()).then(function(j){
    sel.innerHTML = '';
    if (!j.success || !j.rows || !j.rows.length) {
      sel.innerHTML = '<option value="">(no active NAS devices)</option>';
      return;
    }
    j.rows.forEach(function(n){
      var opt = document.createElement('option');
      opt.value = n.id;
      opt.textContent = n.name + ' (' + n.ip + (n.use_ssh?'  · SSH':'  · API') + ')';
      sel.appendChild(opt);
    });
    btn.disabled = false;
  });
  btn.addEventListener('click', function(){
    var id = sel.value;
    if (!id) { out.innerHTML = '<span class="text-danger">Select a NAS first</span>'; return; }
    btn.disabled = true;
    out.innerHTML = '<i class="bi bi-hourglass-split"></i> Uploading…';
    fetch('/api/captive-portal/push-to-nas/' + id, {method: 'POST'})
      .then(r=>r.json())
      .then(function(j){
        btn.disabled = false;
        if (j.success) {
          out.innerHTML = '<span class="text-success"><i class="bi bi-check-circle"></i> '+
            'Uploaded '+(j.size||j.html_bytes||'?')+' bytes to '+j.nas+' ('+j.remote+')</span>';
        } else {
          out.innerHTML = '<span class="text-danger"><i class="bi bi-x-circle"></i> '+
            (j.message||'failed')+'</span>';
        }
      })
      .catch(function(e){
        btn.disabled = false;
        out.innerHTML = '<span class="text-danger">network error: '+e+'</span>';
      });
  });
})();
</script>
'''
    _bak(path, "s36b9")
    # Insert right before the first {% endblock %} that closes 'content'
    # (there may be multiple endblocks; we target content specifically).
    new_src = src.replace("{% endblock %}\n", payload + "\n{% endblock %}\n", 1)
    _write(path, new_src)
    print("  ✓ UI block inserted")


# =========================================================================
# P1 — Hotspot dashboard widgets
# =========================================================================
def patch_dashboard_widgets_backend() -> None:
    print("\n[P1] Adding Hotspot widgets to dashboard stats dict")
    path = os.path.join(ROOT, "main.py")

    marker = "# s36b9-hotspot-widgets-stats"
    src = _read(path)
    if marker in src:
        print("  ↷ already patched"); return

    # Anchor: the stats dict literal in admin_dashboard.  We insert our new
    # keys just before the closing brace of that dict.
    anchor = '            "today_recharged": int(today_recharged),\n        },\n    })\n    return templates.TemplateResponse("admin_dashboard.html", context)\n'
    if anchor not in src:
        print("  ✗ stats-dict anchor not found"); return

    # Code block: compute the 4 hotspot metrics BEFORE the dict literal.
    # We'll inject this right before `context.update({`.
    compute_block = '''
    # s36b9-hotspot-widgets-stats ----------------------------------------
    try:
        from database import HotspotVoucher
        hs_today_sold = db.query(func.count(HotspotVoucher.id)).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.created_at >= today_start_dt,
            HotspotVoucher.created_at <= today_end_dt,
        ).scalar() or 0
        hs_today_used = db.query(func.count(HotspotVoucher.id)).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.status == "used",
            HotspotVoucher.used_at >= today_start_dt,
            HotspotVoucher.used_at <= today_end_dt,
        ).scalar() or 0
        hs_expired_pending = db.query(func.count(HotspotVoucher.id)).filter(
            HotspotVoucher.company_id == company_id,
            HotspotVoucher.status == "expired",
        ).scalar() or 0
    except Exception:
        hs_today_sold = hs_today_used = hs_expired_pending = 0

    # Active hotspot sessions = hotspot-auth customers currently Online.
    try:
        from radius_network import OnlineUser
        from database import Customer as _Cust
        hotspot_usernames = [row[0] for row in db.query(_Cust.username).filter(
            _Cust.company_id == company_id,
            _Cust.auth_type == "hotspot",
        ).all() if row[0]]
        if hotspot_usernames:
            hs_active_sessions = db.query(func.count(OnlineUser.id)).filter(
                OnlineUser.company_id == company_id,
                OnlineUser.status == "Online",
                OnlineUser.username.in_(hotspot_usernames),
            ).scalar() or 0
        else:
            hs_active_sessions = 0
    except Exception:
        hs_active_sessions = 0
    # -------------------------------------------------------------------

'''
    dict_anchor = "    context.update({\n        \"stats\": {\n"
    if dict_anchor not in src:
        print("  ✗ context.update anchor not found"); return

    _bak(path, "s36b9")
    src = src.replace(dict_anchor, compute_block + dict_anchor, 1)

    # Add four new keys into the dict.
    new_keys = '''            "today_recharged": int(today_recharged),
            # s36b9 hotspot widgets
            "hs_active_sessions":    int(hs_active_sessions),
            "hs_vouchers_sold_today":     int(hs_today_sold),
            "hs_vouchers_redeemed_today": int(hs_today_used),
            "hs_vouchers_expired_pending": int(hs_expired_pending),
'''
    src = src.replace('            "today_recharged": int(today_recharged),\n',
                      new_keys, 1)
    _write(path, src)
    print("  ✓ Hotspot widget stats injected")


def patch_dashboard_widgets_template() -> None:
    print("\n[P1b] Adding Hotspot widget cards to admin_dashboard.html")
    path = os.path.join(ROOT, "templates/admin_dashboard.html")

    marker = "s36b9-hotspot-widgets-row"
    src = _read(path)
    if marker in src:
        print("  ↷ already patched"); return

    # Anchor: insert AFTER Row 2 closing div.  We'll find the "Today's Expiry"
    # card row's closing </div>\n    </div>\n\n and append after it. Simpler:
    # insert just before the `<!-- Row 2: operational stats -->` line.
    #
    # Actually — append a new row after the existing Row 2. Let's find the end
    # of Row 2 (which ends with "Today Recharged / Invoices" card.). We put our
    # new row right before the next main section.

    # Use a unique anchor: the comment block after Row 2.
    anchor = '    </div>\n\n    <!-- Row'
    count = src.count(anchor)
    if count < 2:
        # fall back: inject before the first `<div class="col-lg-8 col-md-12">`
        # which is the start of chart row.
        anchor2 = '        <div class="col-lg-8 col-md-12">'
        if anchor2 not in src:
            print("  ✗ dashboard anchor not found"); return
    payload = '''
    <!-- s36b9-hotspot-widgets-row -->
    <div class="row">
        <div class="col-lg-3 col-md-6 col-sm-6">
            <div class="small-box bg-purple">
                <div class="inner">
                    <h3 data-testid="hs-active-sessions">{{ stats.hs_active_sessions|default(0) }}</h3>
                    <p>Hotspot — Active Sessions</p>
                    <div class="stat-sub">
                        <span class="chip">Currently online</span>
                    </div>
                </div>
                <div class="icon"><i class="bi bi-wifi"></i></div>
                <a href="/admin/users?auth=hotspot" class="small-box-footer">View Users <i class="bi bi-arrow-right"></i></a>
            </div>
        </div>
        <div class="col-lg-3 col-md-6 col-sm-6">
            <div class="small-box bg-aqua">
                <div class="inner">
                    <h3 data-testid="hs-vouchers-sold-today">{{ stats.hs_vouchers_sold_today|default(0) }}</h3>
                    <p>Vouchers Sold Today</p>
                    <div class="stat-sub">
                        <span class="chip success">Generated</span>
                    </div>
                </div>
                <div class="icon"><i class="bi bi-ticket-perforated"></i></div>
                <a href="/admin/vouchers" class="small-box-footer">View Batches <i class="bi bi-arrow-right"></i></a>
            </div>
        </div>
        <div class="col-lg-3 col-md-6 col-sm-6">
            <div class="small-box bg-green">
                <div class="inner">
                    <h3 data-testid="hs-vouchers-redeemed-today">{{ stats.hs_vouchers_redeemed_today|default(0) }}</h3>
                    <p>Vouchers Redeemed Today</p>
                    <div class="stat-sub">
                        <span class="chip success">Used</span>
                    </div>
                </div>
                <div class="icon"><i class="bi bi-check2-circle"></i></div>
                <a href="/admin/vouchers" class="small-box-footer">View Usage <i class="bi bi-arrow-right"></i></a>
            </div>
        </div>
        <div class="col-lg-3 col-md-6 col-sm-6">
            <div class="small-box bg-maroon">
                <div class="inner">
                    <h3 data-testid="hs-vouchers-expired-pending">{{ stats.hs_vouchers_expired_pending|default(0) }}</h3>
                    <p>Expired Vouchers (pending cleanup)</p>
                    <div class="stat-sub">
                        <span class="chip danger">Auto-expired</span>
                    </div>
                </div>
                <div class="icon"><i class="bi bi-hourglass-bottom"></i></div>
                <a href="/admin/vouchers" class="small-box-footer">Clean Up <i class="bi bi-arrow-right"></i></a>
            </div>
        </div>
    </div>

'''
    _bak(path, "s36b9")
    # Place BEFORE the first `<div class="col-lg-8 col-md-12">` (start of charts row)
    anchor_pt = '        <div class="col-lg-8 col-md-12">'
    if anchor_pt in src:
        src = src.replace(anchor_pt, payload + anchor_pt, 1)
        _write(path, src)
        print("  ✓ Hotspot widget cards appended")
    else:
        print("  ✗ anchor not found; skipped")


# =========================================================================
# P2 — Extract activity-log HTTP routes into routes/activity_log.py
# =========================================================================
def patch_extract_activity_log_routes() -> None:
    print("\n[P2] Extracting activity-log HTTP routes into routes/activity_log.py")
    routes_dir = os.path.join(ROOT, "routes")
    target = os.path.join(routes_dir, "activity_log.py")

    if os.path.exists(target):
        print(f"  ↷ {target} already exists; skipping extraction")
        return

    content = '''"""routes/activity_log.py  —  s36b9 refactor.

Extracted from main.py to demonstrate the router-module pattern.
The `log_admin_activity()` helper stays in main.py (it's called from
dozens of endpoints and we don't want a dependency cycle).
"""
from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

router = APIRouter(tags=["activity-log"])


def register(app, *, templates, require_admin, get_db, get_admin_context):
    """Bind this router to the FastAPI app. We receive the shared
    dependencies via keyword args to avoid importing from main.py."""

    @router.get("/admin/activity-log", response_class=HTMLResponse)
    async def admin_activity_log_page(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth:
            return auth
        ctx = get_admin_context(request, db, "activity_log")
        return templates.TemplateResponse("admin_activity_log.html", ctx)

    @router.get("/api/activity-log/list")
    async def api_activity_log_list(request: Request,
                                    target_type: str = "",
                                    limit: int = 200,
                                    db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth:
            return auth
        company_id = request.session.get("company_id")
        from database import AdminActivityLog
        q = db.query(AdminActivityLog).filter(AdminActivityLog.company_id == company_id)
        if target_type:
            q = q.filter(AdminActivityLog.target_type == target_type)
        rows = q.order_by(AdminActivityLog.id.desc()).limit(max(1, min(limit, 1000))).all()
        return {"ok": True, "rows": [
            {"id": r.id, "ts": r.created_at.isoformat() if r.created_at else "",
             "actor": r.actor_name or r.actor_id, "action": r.action,
             "target_type": r.target_type, "target_id": r.target_id,
             "note": r.note or ""} for r in rows]}

    app.include_router(router)
'''
    _bak(routes_dir + "/__init__.py", "s36b9")
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  ✓ created {target}")

    # Now we need to:
    #   (a) remove the two extracted endpoints from main.py
    #   (b) wire `routes.activity_log.register(...)` near the top of main.py.
    main_path = os.path.join(ROOT, "main.py")
    src = _read(main_path)
    marker = "# s36b9-activity-log-router-wired"
    if marker in src:
        print("  ↷ main.py already wired"); return

    # Find and delete the two endpoints.  We'll match by function definition
    # and strip from @app.get/@app.post header to the next top-level route.
    def _strip_route(src: str, route_head: str, func_name: str) -> str:
        idx = src.find(route_head)
        if idx < 0:
            return src
        # Find end: next line starting with @app. or another top-level decorator
        # or an @app-less top-level def at col 0 (not common inside main.py which
        # is almost all decorated routes).
        # Start search after function body.
        end = src.find("\n@app.", idx + len(route_head))
        if end < 0:
            end = len(src)
        return src[:idx] + src[end:]

    _bak(main_path, "s36b9")

    # Remove /admin/activity-log
    src = _strip_route(src,
        '@app.get("/admin/activity-log", response_class=HTMLResponse)',
        "admin_activity_log_page")
    # Remove /api/activity-log/list
    src = _strip_route(src,
        '@app.get("/api/activity-log/list")',
        "api_activity_log_list")

    # Wire the router after app is created. Search for the earliest `app = FastAPI(`.
    wire_anchor = "app.add_middleware(SessionMiddleware"
    if wire_anchor not in src:
        # Fallback
        wire_anchor = "app = FastAPI("
    # We need to register AFTER templates, get_db, require_admin and
    # get_admin_context are all defined.  Safest: append at the very end of
    # main.py (after all routes) so those symbols are resolved.
    wire = '''
# s36b9-activity-log-router-wired
try:
    from routes import activity_log as _s36b9_activity_log_mod
    _s36b9_activity_log_mod.register(
        app,
        templates=templates,
        require_admin=require_admin,
        get_db=get_db,
        get_admin_context=get_admin_context,
    )
except Exception as _e:
    import logging
    logging.getLogger("main").exception("activity_log router wire failed: %s", _e)
'''
    if src.rstrip().endswith('if __name__ == "__main__":\n    uvicorn.run(app, host="0.0.0.0", port=8001)'):
        # Inject before the uvicorn main
        idx = src.find('if __name__ == "__main__":')
        src = src[:idx] + wire + "\n\n" + src[idx:]
    else:
        src = src + "\n" + wire + "\n"

    _write(main_path, src)
    print("  ✓ extracted endpoints stripped and router wired in main.py")


# =========================================================================
# Driver
# =========================================================================
def main() -> None:
    print("═" * 60)
    print(" patch_s36b9 — final backlog (P0+P1+P2)")
    print(f" root: {ROOT}")
    print(f" ts:   {TS}")
    print("═" * 60)

    patch_routeros_provision()          # P0.1
    patch_captive_portal_push()         # P0.2
    patch_captive_portal_ui()           # P0.2b
    patch_dashboard_widgets_backend()   # P1a
    patch_dashboard_widgets_template()  # P1b
    patch_extract_activity_log_routes() # P2

    print("\n" + "═" * 60)
    print(" DONE — review, restart isp-admin, run pytest tests/")
    print("═" * 60)


if __name__ == "__main__":
    main()
