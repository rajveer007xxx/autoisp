"""patch_s36h.py — Bug fix + UX surface for existing features:

  1. Fix voucher list rendering (JS used `j.vouchers`, API returns `j.rows`)
  2. Add sidebar link for Public IPs page (feature exists but was hidden)
  3. Add "Setup Hotspot" button on NAS Edit modal that runs configure_hotspot
     + firewall-NAT baseline on the router.
"""
from __future__ import annotations
import os, shutil, datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _bak(p):
    if os.path.exists(p): shutil.copy2(p, f"{p}.bak_s36h_{TS}")


def _read(p):
    with open(p) as f: return f.read()


def _write(p, c):
    with open(p, "w") as f: f.write(c)


# =========================================================================
# 1. Fix voucher list render
# =========================================================================
def fix_voucher_list():
    print("\n[1] Fix voucher list rendering (j.vouchers → j.rows)")
    p = os.path.join(ROOT, "templates/admin_vouchers.html")
    t = _read(p)
    if "j.vouchers" not in t:
        print("  ↷ already fixed"); return
    _bak(p)
    t = t.replace("(j.vouchers || [])", "(j.rows || j.vouchers || [])")
    t = t.replace("(j.vouchers||[])", "(j.rows || j.vouchers || [])")
    _write(p, t)
    print("  ✓ JS now accepts j.rows (or legacy j.vouchers) and renders correctly")


# =========================================================================
# 2. Sidebar link for Public IPs
# =========================================================================
def sidebar_public_ips():
    print("\n[2] Add Public IPs sidebar link")
    p = os.path.join(ROOT, "templates/base_admin.html")
    t = _read(p)
    if "/admin/public-ips" in t:
        print("  ↷ link already present"); return
    _bak(p)
    # Insert under IP Pools entry
    anchor = "active_page == 'ip_pools'"
    idx = t.find(anchor)
    if idx < 0:
        print("  ✗ anchor not found"); return
    # Find the </li> closing the IP Pools item
    li_close = t.find("</li>", idx) + len("</li>")
    link = '''\n                    <li class="{% if active_page == 'public_ips' %}active{% endif %}">
                        <a href="/admin/public-ips" data-testid="sidebar-public-ips"><i class="bi bi-globe"></i> <span>Public IPs (Static)</span></a>
                    </li>'''
    t = t[:li_close] + link + t[li_close:]
    _write(p, t)
    print("  ✓ sidebar link added under IP Pools")


# =========================================================================
# 3. "Setup Hotspot" button on NAS edit modal
# =========================================================================
def setup_hotspot_button():
    print("\n[3] Add 'Setup Hotspot' button on NAS devices page")

    # 3a. Backend endpoint (append to main.py tail)
    main = os.path.join(ROOT, "main.py")
    m = _read(main)
    if "# s36h-setup-hotspot" in m:
        print("  ↷ already patched"); return
    _bak(main)
    endpoint = '''

# s36h-setup-hotspot
@app.post("/api/nas-devices/{nas_id}/setup-hotspot")
async def api_nas_setup_hotspot(nas_id: int, request: Request,
                                  db: Session = Depends(get_db)):
    """Run configure_hotspot + firewall_nat_baseline on the NAS. This sets
    up the Hotspot server, DHCP pool, RADIUS-driven profile, walled-garden
    + masquerade NAT — everything needed for the captive portal to work."""
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from radius_network import NasDevice
    company_id = request.session.get("company_id")
    nas = db.query(NasDevice).filter(
        NasDevice.id == nas_id, NasDevice.company_id == company_id).first()
    if not nas:
        return JSONResponse({"ok": False, "error": "NAS not found"}, status_code=404)
    body = await request.json()
    interface = (body.get("interface") or "ether3").strip()
    address = (body.get("address") or "10.10.10.1/24").strip()
    pool_name = (body.get("pool_name") or "hotspot-pool").strip()
    pool_ranges = (body.get("pool_ranges") or "10.10.10.10-10.10.10.250").strip()
    wan_interface = (body.get("wan_interface") or "ether1").strip()
    dry_run = bool(body.get("dry_run", False))
    enable = bool(body.get("enable", True))

    try:
        from routeros_provision import RouterOSClient
        with RouterOSClient(nas, dry_run=dry_run) as cli:
            hs_cmds = cli.configure_hotspot(
                interface=interface, address=address,
                pool_name=pool_name, pool_ranges=pool_ranges,
                use_radius=True, disabled=not enable)
            nat_cmds = cli.configure_firewall_nat_baseline(
                wan_interface=wan_interface)
            commands = cli.commands
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    try:
        log_admin_activity(db, request, "configure", "nas_hotspot",
                           target_id=str(nas_id),
                           summary=f"setup hotspot on {nas.name}: {interface} {address}")
    except Exception:
        pass
    return {"ok": True, "nas": nas.name, "dry_run": dry_run,
            "hotspot_commands": hs_cmds, "nat_commands": nat_cmds,
            "all_commands": commands}
'''
    if 'if __name__ == "__main__":' in m:
        idx = m.rfind('if __name__ == "__main__":')
        m = m[:idx] + endpoint + "\n" + m[idx:]
    else:
        m = m.rstrip() + endpoint + "\n"
    _write(main, m)
    print("  ✓ /api/nas-devices/{nas_id}/setup-hotspot endpoint added")

    # 3b. UI button on NAS edit modal
    tpl = os.path.join(ROOT, "templates/admin_nas_devices.html")
    t = _read(tpl)
    if "s36h-setup-hotspot-btn" not in t:
        _bak(tpl)
        # Anchor: the save NAS button area (bottom of modal)
        # Use the walled-garden textarea we added in s36e as anchor since it's in the hotspot config block.
        anchor = 'id="nas_walled_garden_hosts"'
        idx = t.find(anchor)
        if idx >= 0:
            # Find end of the enclosing div.row
            close1 = t.find("</div></div></div>", idx)
            if close1 >= 0:
                ins = close1 + len("</div></div></div>")
                inject = '''
                            <!-- s36h-setup-hotspot-btn -->
                            <div class="row"><div class="col-sm-12">
                                <div class="alert alert-info" style="margin-top:8px;padding:10px 14px;">
                                    <b><i class="bi bi-wifi"></i> One-Click Hotspot Setup</b>
                                    <p style="margin:6px 0 0;font-size:12px;">Creates the Hotspot server, DHCP pool, RADIUS-driven login profile, walled-garden entries AND the outbound masquerade NAT rule on the NAS — the full working captive-portal stack in one push. Safe to re-run; existing entries are preserved.</p>
                                    <div class="row" style="margin-top:10px;">
                                        <div class="col-sm-4"><label style="font-size:11px;">Hotspot Interface</label><input id="s36h_hs_iface" class="form-control input-sm" value="ether3" data-testid="setup-hs-iface"></div>
                                        <div class="col-sm-4"><label style="font-size:11px;">Hotspot Gateway</label><input id="s36h_hs_addr" class="form-control input-sm" value="10.10.10.1/24" data-testid="setup-hs-addr"></div>
                                        <div class="col-sm-4"><label style="font-size:11px;">WAN Interface (for NAT)</label><input id="s36h_wan_iface" class="form-control input-sm" value="ether1" data-testid="setup-hs-wan"></div>
                                    </div>
                                    <div style="margin-top:10px;">
                                        <button type="button" class="btn btn-warning btn-sm" onclick="s36hSetupHotspot(true)" data-testid="setup-hs-dry-btn"><i class="bi bi-eye"></i> Preview (dry-run)</button>
                                        <button type="button" class="btn btn-success btn-sm" onclick="s36hSetupHotspot(false)" data-testid="setup-hs-btn"><i class="bi bi-play-circle"></i> Run Setup on NAS</button>
                                        <div id="s36h_setup_out" style="margin-top:8px;font-size:12px;"></div>
                                    </div>
                                </div></div>
                            </div>
                '''
                t = t[:ins] + inject + t[ins:]
                # JS function
                js_inject = '''
<script>
var s36h_current_nas_id = null;
(function(){
  // Track which NAS is being edited so Setup-Hotspot knows the target.
  var orig = window.editNas;
  if (typeof orig === 'function') {
    window.editNas = function(row){
      s36h_current_nas_id = row && row.id;
      return orig.apply(this, arguments);
    };
  }
})();
function s36hSetupHotspot(dry){
  var out = document.getElementById('s36h_setup_out');
  var nid = s36h_current_nas_id;
  if (!nid) { out.innerHTML = '<span class="text-danger">Open a NAS via Edit first</span>'; return; }
  var payload = {
    interface: document.getElementById('s36h_hs_iface').value.trim() || 'ether3',
    address:   document.getElementById('s36h_hs_addr').value.trim()  || '10.10.10.1/24',
    wan_interface: document.getElementById('s36h_wan_iface').value.trim() || 'ether1',
    dry_run: dry, enable: true
  };
  out.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + (dry ? 'Previewing…' : 'Running setup on NAS…');
  fetch('/api/nas-devices/' + nid + '/setup-hotspot', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)})
    .then(r=>r.json()).then(function(j){
      if (j.ok) {
        var cmds = (j.all_commands || []).slice(0, 30).map(function(c){ return '<code style="display:block;padding:4px 8px;background:#f3f4f6;margin:2px 0;">'+c+'</code>'; }).join('');
        out.innerHTML = '<div class="alert alert-success"><b>' + (j.dry_run?'Preview':'Setup complete') + ' on ' + j.nas + '</b><br>' +
          '<div style="max-height:200px;overflow:auto;margin-top:6px;">' + cmds + '</div>' +
          (j.dry_run ? '<button class="btn btn-xs btn-success" onclick="s36hSetupHotspot(false)">Now run for real</button>' : '') +
          '</div>';
      } else {
        out.innerHTML = '<div class="alert alert-danger">' + (j.error || 'failed') + '</div>';
      }
    })
    .catch(function(e){ out.innerHTML = '<span class="text-danger">' + e + '</span>'; });
}
</script>
'''
                t = t + js_inject
                _write(tpl, t)
                print("  ✓ Setup Hotspot UI + JS added to NAS edit modal")


def main():
    print("═" * 60)
    print(f" patch_s36h — voucher fix + public IPs link + hotspot setup ({TS})")
    print("═" * 60)
    fix_voucher_list()
    sidebar_public_ips()
    setup_hotspot_button()
    print("\n" + "═" * 60)
    print(" DONE")


if __name__ == "__main__":
    main()
