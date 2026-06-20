"""patch_s36e.py — Follow-through on s36d

  1. Wire hotspot_login.html to POST /api/vouchers/redeem/{code} on submit
     so the webhook actually fires when customers log in.
  2. Add per-NAS walled_garden_hosts override column + NAS form UI field;
     push path: NAS override (if set) wins over CaptivePortalSettings.
  3. Auto-populate hotspot_portal_url for any CaptivePortalSettings row that
     is still empty, deriving from the request Host header or the nginx
     server_name. Also hardens save() to auto-fill if admin leaves blank.
"""
from __future__ import annotations
import os, shutil, sqlite3, re
from datetime import datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _bak(p): 
    if os.path.exists(p): shutil.copy2(p, f"{p}.bak_s36e_{TS}")


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


# =========================================================================
# 1. hotspot_login.html → /api/vouchers/redeem/{code}
# =========================================================================
def wire_redeem_on_login():
    print("\n[1] Wiring hotspot_login.html → /api/vouchers/redeem/{code}")
    path = os.path.join(ROOT, "templates/hotspot_login.html")
    t = _read(path)
    if "s36e-redeem-hook" in t:
        print("  ↷ already wired"); return
    _bak(path)

    # Replace the form submit behaviour: call redeem first, then submit to
    # MikroTik. Target the outer <form> so all three modes (voucher /
    # username-password / OTP) funnel through the same handler. For
    # voucher & OTP modes the `password` field holds the code; for
    # username_password there is both `username` and `password`.
    #
    # Strategy: intercept form.submit; if voucher mode (or any code has
    # been entered in the voucher input), POST to redeem first, and
    # display a visible status. Then always real-submit to MikroTik
    # (real-submit happens regardless of redeem result so the user can
    # still authenticate even if our webhook hiccups).
    anchor = "document.querySelectorAll('.mode-tab').forEach(function(t){"
    if anchor not in t:
        print("  ✗ anchor not found"); return

    hook = """// s36e-redeem-hook — call our server to mark the voucher used + fire webhook.
(function(){
    var form = document.querySelector('form');
    if (!form) return;

    function activeMode(){
        var tab = document.querySelector('.mode-tab.active');
        return tab ? tab.getAttribute('data-m') : 'voucher';
    }
    function currentCode(){
        var mode = activeMode();
        var body = document.getElementById('mode-' + mode);
        if (!body) return '';
        var pw = body.querySelector('input[name="password"]');
        return pw ? (pw.value || '').trim() : '';
    }

    // Status banner at the top of the panel so customer sees what's happening.
    var statusEl = document.createElement('div');
    statusEl.id = 's36e-redeem-status';
    statusEl.style.cssText = 'display:none;margin:10px 0 0;padding:10px 12px;border-radius:8px;font-size:13px;';
    var panel = document.querySelector('.panel');
    if (panel) panel.insertBefore(statusEl, panel.firstChild.nextSibling);

    function showStatus(msg, ok){
        statusEl.textContent = msg;
        statusEl.style.display = 'block';
        statusEl.style.background = ok ? '#ecfdf5' : '#fef3c7';
        statusEl.style.color = ok ? '#065f46' : '#92400e';
    }

    var submitting = false;
    form.addEventListener('submit', function(ev){
        if (submitting) return;  // allow the 2nd real submit through
        var mode = activeMode();
        var code = currentCode();
        if (mode === 'voucher' && code) {
            ev.preventDefault();
            showStatus('Validating voucher…', true);
            fetch('/api/vouchers/redeem/' + encodeURIComponent(code), {
                method: 'POST', credentials: 'same-origin',
                headers: {'X-MAC': (window.__mac || '')}
            }).then(function(r){ return r.json().then(function(j){ return {status:r.status, body:j}; }); })
              .then(function(res){
                if (res.status === 404) {
                    showStatus('✗ Voucher code not found. Please check and try again.', false);
                } else if (res.status === 400) {
                    showStatus('✗ ' + (res.body.error || 'Voucher already ' + (res.body.body && res.body.body.status || 'used/expired')), false);
                } else if (res.body && res.body.ok) {
                    showStatus(res.body.already_used
                        ? '✓ Voucher already redeemed — connecting…'
                        : '✓ Voucher accepted — connecting…', true);
                    submitting = true;
                    setTimeout(function(){ form.submit(); }, 300);
                } else {
                    // Non-ok but not 404/400 → submit anyway so customer still gets online.
                    submitting = true;
                    form.submit();
                }
              })
              .catch(function(){
                // Network error — still submit (don't punish the user).
                submitting = true;
                form.submit();
              });
        } else {
            // For username_password / OTP modes: fire-and-forget mark-used if a
            // code-looking password was typed; real-submit always happens.
            if (code && /^[A-Z0-9]{6,}$/i.test(code)) {
                try {
                    navigator.sendBeacon(
                        '/api/vouchers/redeem/' + encodeURIComponent(code),
                        new Blob([''], {type:'application/x-www-form-urlencoded'}));
                } catch(e){}
            }
            // allow native submit to proceed
        }
    });
})();
"""
    t = t.replace(anchor, hook + "\n" + anchor, 1)
    _write(path, t)
    print("  ✓ hook injected")


# =========================================================================
# 2. Per-NAS walled_garden_hosts override
# =========================================================================
def per_nas_walled_garden():
    print("\n[2] Per-NAS walled-garden override")
    # 2a. SQLite column
    _sq_add_col("nas_devices", "walled_garden_hosts", "TEXT DEFAULT ''")

    # 2b. ORM field
    rn = os.path.join(ROOT, "radius_network.py")
    src = _read(rn)
    if "walled_garden_hosts" not in src:
        _bak(rn)
        anchor = '    hotspot_pool_name = Column(String, nullable=True, default="hotspot-pool")\n'
        if anchor in src:
            src = src.replace(anchor, anchor +
                '    # s36e per-NAS walled-garden override (comma/newline separated).\n'
                '    # Empty => fall back to CaptivePortalSettings.walled_garden_hosts.\n'
                '    walled_garden_hosts = Column(String, nullable=True, default="")\n', 1)
            # Add to api_nas_create arg list
            create_old = 'hotspot_pool_name=(data.get("hotspot_pool_name") or "hotspot-pool").strip(),\n            )\n            db.add(row)'
            create_new = 'hotspot_pool_name=(data.get("hotspot_pool_name") or "hotspot-pool").strip(),\n                walled_garden_hosts=(data.get("walled_garden_hosts") or "").strip(),\n            )\n            db.add(row)'
            src = src.replace(create_old, create_new, 1)
            # Add to api_nas_update settable keys
            upd_old = '"hotspot_interface", "hotspot_dns_name", "hotspot_pool_name"):'
            upd_new = '"hotspot_interface", "hotspot_dns_name", "hotspot_pool_name",\n                  "walled_garden_hosts"):'
            src = src.replace(upd_old, upd_new, 1)
            # Expose in api_nas_list response
            # Find the list-item dict and add walled_garden_hosts if present
            list_old = '"hotspot_pool_name": getattr(n, "hotspot_pool_name", "")'
            if list_old in src:
                list_new = list_old + ', "walled_garden_hosts": getattr(n, "walled_garden_hosts", "") or ""'
                src = src.replace(list_old, list_new, 1)
            _write(rn, src)
            print("  ✓ ORM + create/update/list wired")

    # 2c. UI — add textarea to NAS Edit modal in admin_nas_devices.html
    tpl = os.path.join(ROOT, "templates/admin_nas_devices.html")
    t = _read(tpl)
    if 'id="nas_walled_garden_hosts"' not in t:
        _bak(tpl)
        # Anchor: just after the hotspot-pool-name field (hotspot config block).
        anchor = 'id="nas_hotspot_pool_name"'
        idx = t.find(anchor)
        if idx >= 0:
            # Close the enclosing col+form-group+row, then append new row.
            # Simpler: inject a new <div class="row"> right after the closing of
            # the col containing hotspot_pool_name. We'll find the next </div></div>
            # pair after our anchor.
            close1 = t.find("</div>", idx)
            close2 = t.find("</div>", close1 + 6)  # close div.form-group
            # close3 = t.find("</div>", close2 + 6)  # close col-sm-X
            ins_at = close2 + 6
            inject = '''
                            <div class="row"><div class="col-sm-12"><div class="form-group">
                                <label>Walled-Garden Hosts (per-NAS override)
                                    <small class="text-muted" style="font-weight:normal;font-size:11px;">— comma/newline separated hostnames or IPs. Overrides company-wide walled-garden list when pushing to this NAS. Leave empty to use the company default.</small>
                                </label>
                                <textarea class="form-control" id="nas_walled_garden_hosts" rows="3" placeholder="payment-gw.example.com&#10;whatsapp.com" data-testid="nas-walled-garden-hosts"></textarea>
                            </div></div></div>
                    '''
            t = t[:ins_at] + inject + t[ins_at:]

            # Wire editNas JS — read walled_garden_hosts into the textarea
            edit_old = "document.getElementById('nas_hotspot_pool_name').value = row.hotspot_pool_name || 'hotspot-pool';"
            edit_new = edit_old + "\n    document.getElementById('nas_walled_garden_hosts').value = row.walled_garden_hosts || '';"
            if edit_old in t:
                t = t.replace(edit_old, edit_new, 1)

            # Wire saveNas JS — include walled_garden_hosts in payload
            save_old = "hotspot_pool_name: document.getElementById('nas_hotspot_pool_name').value,"
            save_new = save_old + "\n        walled_garden_hosts: document.getElementById('nas_walled_garden_hosts').value,"
            if save_old in t:
                t = t.replace(save_old, save_new, 1)
            _write(tpl, t)
            print("  ✓ UI + edit/save JS wired")

    # 2d. Push-to-NAS: prefer NAS override when non-empty
    cp = os.path.join(ROOT, "routes/captive_portal.py")
    c = _read(cp)
    if "# s36e-per-nas-wg" not in c:
        _bak(cp)
        old = 'wg_hosts_raw = (portal.walled_garden_hosts or "").strip()'
        new = ('# s36e-per-nas-wg — NAS override wins over company default.\n'
               '                nas_wg = (getattr(nas, "walled_garden_hosts", "") or "").strip()\n'
               '                wg_hosts_raw = nas_wg or (portal.walled_garden_hosts or "").strip()')
        if old in c:
            c = c.replace(old, new, 1)
            _write(cp, c)
            print("  ✓ push-to-nas honours NAS override")


# =========================================================================
# 3. Auto-populate hotspot_portal_url
# =========================================================================
def auto_populate_portal_url():
    print("\n[3] Auto-populate hotspot_portal_url")

    # 3a. Determine a sensible default from nginx server_name.
    default_url = "https://www.autoispbilling.com/hotspot/login.html"
    nginx_cfg = "/etc/nginx/sites-enabled/ispbilling"
    try:
        with open(nginx_cfg) as f:
            nc = f.read()
        m = re.search(r'server_name\s+([a-zA-Z0-9.-]+(?:\s+[a-zA-Z0-9.-]+)*)\s*;', nc)
        if m:
            names = [n for n in m.group(1).split() if "." in n]
            if names:
                # Pick the one starting with www. or the first
                host = next((n for n in names if n.startswith("www.")), names[0])
                default_url = f"https://{host}/hotspot/login.html"
                print(f"  · detected default: {default_url}")
    except Exception as e:
        print(f"  · using fallback default ({default_url}): {e}")

    # 3b. Backfill any empty hotspot_portal_url in DB
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        n = cur.execute(
            "UPDATE captive_portal_settings SET hotspot_portal_url=? "
            "WHERE hotspot_portal_url IS NULL OR hotspot_portal_url=''",
            (default_url,)).rowcount
        conn.commit()
        print(f"  ✓ backfilled {n} row(s) with {default_url!r}")
    finally:
        conn.close()

    # 3c. Harden save() — auto-fill hotspot_portal_url at save-time if left empty
    cp = os.path.join(ROOT, "routes/captive_portal.py")
    c = _read(cp)
    if "# s36e-autofill-portal-url" not in c:
        _bak(cp)
        anchor = '        db.commit()\n        try:\n            log_admin_activity(db, request, "update", "captive_portal",'
        if anchor in c:
            inject = '''        # s36e-autofill-portal-url — never leave the canonical portal URL
        # empty; derive from the current request's Host header so QR codes
        # always work even if the admin forgets to set it.
        if not (row.hotspot_portal_url or "").strip():
            fwd_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
            fwd_proto = request.headers.get("x-forwarded-proto") or "https"
            if fwd_host and "127.0.0.1" not in fwd_host and "localhost" not in fwd_host:
                row.hotspot_portal_url = f"{fwd_proto}://{fwd_host}/hotspot/login.html"
        db.commit()
        try:
            log_admin_activity(db, request, "update", "captive_portal",'''
            c = c.replace(anchor, inject, 1)
            _write(cp, c)
            print("  ✓ save() auto-fills hotspot_portal_url")


# =========================================================================
def main():
    print("═" * 60)
    print(f" patch_s36e — webhook wiring + per-NAS WG + URL backfill ({TS})")
    print("═" * 60)
    wire_redeem_on_login()
    per_nas_walled_garden()
    auto_populate_portal_url()
    print("\n" + "═" * 60)
    print(" DONE — restart isp-admin and run pytest")


if __name__ == "__main__":
    main()
