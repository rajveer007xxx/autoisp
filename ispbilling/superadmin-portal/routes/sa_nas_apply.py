"""SuperAdmin: One-click "Apply" buttons for per-NAS configuration.

Each endpoint connects DIRECTLY to the customer's MikroTik via librouteros
(using the api_username/api_password stored on the NAS row) and applies
config changes idempotently — no copy-paste, no scripts, no manual steps.

Built on every lesson learned during the 2026-05-14 outage:
  * pool selection always filters status=Active and non-empty network
  * NAT-list MASQUERADE / ALLOW for every PPPoE pool + fallback ranges
  * MSS clamp added (1452) — fixes "ping works but HTTPS won't load"
  * Address-list `parked-customers` matches dstnat redirect (not "blocked")
  * Parking pool = pool-parking (10.99.99.0/24) on router, redirected
    to /pay/captive (not /expired which 404s)
  * Anti-BF preserves the cloud IP 185.199.53.93 in allowed-mgmt
  * Each step skips-if-exists so re-running is harmless

Kinds:
  std-users      - add 'monitoring' user (read-only)
  api-admin      - add/ensure 'api-admin' user with NAS row password
  rsc-hardening  - timezone, NTP, DNS, identity, logging defaults
  universal-nat  - per-pool MASQUERADE/ALLOW + master srcnat + MSS clamp
  parking-pool   - pool + profile + filter + dstnat + proxy + /pay/captive
  anti-bruteforce- allowed-mgmt + auto-banned input chain + service hardening
  auto-config    - runs ALL of the above in sequence (idempotent)
"""
import os, sys, time, traceback
from typing import Callable, Dict
from fastapi import Request, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

CLOUD_IP = "185.199.53.93"
CAPTIVE_URL = "https://www.autoispbilling.com/pay/captive"

def _strip_captive_scheme(url: str) -> str:
    """Mikrotik /ip/proxy/access redirect-to expects a hostname[+path],
    NOT a full URL. It prepends 'http://' itself. Passing a full
    'https://...' URL produces 'http://https//www.foo.com' on the
    client side (RouterOS strips one colon). So we strip the scheme.
    """
    if not url:
        return url
    u = url.strip()
    if u.lower().startswith("https://"):
        u = u[8:]
    elif u.lower().startswith("http://"):
        u = u[7:]
    # Strip any leading slashes (defensive — admins sometimes paste //foo)
    u = u.lstrip("/")
    return u
ADMIN_LANS = ["192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12"]  # private LAN ranges to NAT


def _is_sa(request: Request) -> bool:
    try:
        return (request.session.get("user_type") or "").lower() == "superadmin"
    except Exception:
        return False


def _connect(nas):
    """Open a librouteros API session using the NAS row credentials."""
    from librouteros import connect
    from librouteros.login import plain
    return connect(host=nas.ip_address,
                   username=(nas.api_username or "admin"),
                   password=(nas.api_password or ""),
                   port=int(getattr(nas, "port", None) or 8728),
                   login_method=plain, timeout=25)


def _resolve_nas(db: Session, company_id: str, nas_id: int):
    from radius_network import NasDevice
    return db.query(NasDevice).filter(NasDevice.id == nas_id,
                                       NasDevice.company_id == company_id).first()




class _Mode:
    """Wraps an /api Path to intercept add/update/remove for dry-run."""
    def __init__(self, dry_run: bool, log: list):
        self.dry_run = dry_run
        self.log = log

    def wrap(self, path, label_root: str = ""):
        return _WrappedPath(path, self, label_root)


class _WrappedPath:
    def __init__(self, path, mode: "_Mode", label_root: str):
        self._p = path
        self._m = mode
        self._label = label_root or getattr(path, "path", "")

    def __iter__(self):
        return iter(self._p)

    def _fmt(self, op: str, kw: dict) -> str:
        bits = []
        for k in ("name", "address", "list", "chain", "action",
                   "dst-port", "comment", "to-ports", "interface"):
            if k in kw:
                bits.append(f"{k}={kw[k]!r}")
        return f"{op} {self._label} {' '.join(bits) or kw}"

    def add(self, **kw):
        lbl = self._fmt("add", kw)
        if self._m.dry_run:
            self._m.log.append(f"[plan] would {lbl}")
            return None
        try:
            r = self._p.add(**kw)
            self._m.log.append(f"[ok] {lbl}")
            return r
        except Exception as e:
            self._m.log.append(f"[ERR] {lbl}: {e}")
            return None

    def update(self, **kw):
        lbl = self._fmt("update", kw)
        if self._m.dry_run:
            self._m.log.append(f"[plan] would {lbl}")
            return None
        try:
            r = self._p.update(**kw)
            self._m.log.append(f"[ok] {lbl}")
            return r
        except Exception as e:
            self._m.log.append(f"[ERR] {lbl}: {e}")
            return None

    def remove(self, *args, **kw):
        lbl = f"remove {self._label} {args or kw}"
        if self._m.dry_run:
            self._m.log.append(f"[plan] would {lbl}")
            return None
        try:
            r = self._p.remove(*args, **kw)
            self._m.log.append(f"[ok] {lbl}")
            return r
        except Exception as e:
            self._m.log.append(f"[ERR] {lbl}: {e}")
            return None


def _path(api, p, mode):
    """Shortcut: api.path(p) wrapped through mode."""
    return mode.wrap(api.path(p), label_root=p)

# ---------------------------------------------------------------- step helpers
def _step(log: list, ok: bool, msg: str):
    log.append(f"{'[ok] ' if ok else '[skip] '}{msg}")


# ---------------------------------------------------------------- 1. std users
def _apply_std_users(api, mode, opts, **_):
    users = _path(api, "/user", mode)
    existing = {u.get("name"): u for u in users}
    if "monitoring" in existing:
        mode.log.append("[skip] user 'monitoring' already exists")
    else:
        users.add(name="monitoring",
                  password=(opts.get("monitoring_password") or "AutoISP@Monitor2026"),
                  group="read", comment="auto-isp-monitoring")


# ---------------------------------------------------------------- 2. api admin
def _apply_api_admin(api, mode, opts, nas=None, **_):
    """Ensure an 'api-admin' user with full perms exists, using the
    password stored on the NAS row."""
    pw = (getattr(nas, "api_password", "") or "").strip()
    if not pw:
        mode.log.append("[skip] NAS row has no api_password — cannot create api-admin")
        return
    users = _path(api, "/user", mode)
    existing = {u.get("name"): u for u in users}
    if "api-admin" in existing:
        mode.log.append("[skip] user 'api-admin' already exists (password not changed)")
    else:
        users.add(name="api-admin", password=pw,
                  group="full", comment="auto-isp-api-admin")
    # Enable /ip service api if disabled
    svc_path = api.path("/ip/service")
    svc_w = mode.wrap(svc_path, "/ip/service")
    for s in svc_path:
        if s.get("name") == "api" and s.get("disabled"):
            svc_w.update(**{".id": s[".id"], "disabled": "no"})


# ---------------------------------------------------------------- 3. rsc hard.
def _apply_rsc_hardening(api, mode, opts, **_):
    steps = set(opts.get("steps") or ["ntp", "dns", "logging"])
    # NTP
    if "ntp" in steps:
        ntp_path = api.path("/system/ntp/client")
        ntp_w = mode.wrap(ntp_path, "/system/ntp/client")
        for r in ntp_path:
            if not r.get("enabled"):
                ntp_w.update(**{".id": r[".id"], "enabled": "yes",
                                "primary-ntp": opts.get("ntp_primary",
                                                          "162.159.200.123"),
                                "secondary-ntp": opts.get("ntp_secondary",
                                                            "216.239.35.0")})
                break
            else:
                mode.log.append("[skip] NTP client already enabled")
    # DNS
    if "dns" in steps:
        dns_path = api.path("/ip/dns")
        dns_w = mode.wrap(dns_path, "/ip/dns")
        want = opts.get("dns_servers") or "8.8.8.8,1.1.1.1"
        for r in dns_path:
            cur = (r.get("servers") or "").strip()
            if want not in cur and "8.8.8.8" not in cur:
                dns_w.update(**{".id": r[".id"], "servers": want,
                                "allow-remote-requests": "no"})
            else:
                mode.log.append("[skip] DNS already configured")
    # Logging size
    if "logging" in steps:
        log_path = api.path("/system/logging/action")
        log_w = mode.wrap(log_path, "/system/logging/action")
        for r in log_path:
            if r.get("name") == "memory":
                try:
                    lines = int(r.get("memory-lines") or 100)
                except Exception:
                    lines = 100
                if lines < 1000:
                    log_w.update(**{".id": r[".id"], "memory-lines": "1000"})
                else:
                    mode.log.append("[skip] memory-lines already >=1000")


# ---------------------------------------------------------------- 4. univ NAT
def _apply_universal_nat(api, mode, opts, db=None, nas=None, **_):
    nets = list(opts.get("subnets") or [])
    if not nets:
        try:
            from sqlalchemy import text as _sql
            rows = db.execute(_sql(
                "SELECT network FROM ip_pools WHERE company_id=:cid "
                "AND (role IS NULL OR role IN ('PPPoE','Static','Static-IP','Hotspot')) "
                "AND (status IS NULL OR LOWER(status)='active') "
                "AND network IS NOT NULL AND network != ''"
            ), {"cid": nas.company_id}).fetchall()
            nets = [r[0] for r in rows if r and r[0]]
        except Exception as e:
            mode.log.append(f"[warn] pool DB query failed: {e}")
    if not nets:
        nets = ["172.10.10.0/24"]
        mode.log.append("[info] no Active pools — defaulting to 172.10.10.0/24")
    if opts.get("include_fallbacks", True):
        nets = list(dict.fromkeys(nets + ["10.112.121.0/24", "10.112.118.0/24"]
                                   + (ADMIN_LANS if opts.get("include_rfc1918", True)
                                      else [])))

    al_path = api.path("/ip/firewall/address-list")
    al = mode.wrap(al_path, "/ip/firewall/address-list")
    existing = {(r.get("address"), r.get("list")) for r in al_path}
    for subnet in nets:
        for lst in ("MASQUERADE", "ALLOW"):
            if (subnet, lst) in existing:
                mode.log.append(f"[skip] {subnet} already in {lst}")
            else:
                al.add(address=subnet, list=lst, comment="auto-isp-pool-nat")

    if opts.get("master_srcnat", True):
        nat_path = api.path("/ip/firewall/nat")
        nat = mode.wrap(nat_path, "/ip/firewall/nat")
        have_master = any(
            r.get("chain") == "srcnat" and r.get("action") == "masquerade"
            and r.get("src-address-list") == "MASQUERADE"
            for r in nat_path)
        if have_master:
            mode.log.append("[skip] master srcnat MASQUERADE rule already exists")
        else:
            nat.add(chain="srcnat", action="masquerade",
                    **{"src-address-list": "MASQUERADE"},
                    comment="auto-isp-master-masquerade")

    if opts.get("mss_clamp", True):
        mangle_path = api.path("/ip/firewall/mangle")
        mangle = mode.wrap(mangle_path, "/ip/firewall/mangle")
        if any(r.get("action") == "change-mss" for r in mangle_path):
            mode.log.append("[skip] MSS clamp already configured")
        else:
            mangle.add(chain="forward", protocol="tcp",
                       **{"tcp-flags": "syn", "tcp-mss": "1453-65535",
                          "action": "change-mss", "new-mss": "1452",
                          "passthrough": "yes",
                          "comment": "auto-isp-pppoe-mss-clamp"})


# ---------------------------------------------------------------- 5. parking
def _apply_parking_pool(api, mode, opts, **_):
    pool_range = opts.get("pool_range") or "10.99.99.10-10.99.99.250"
    pool_name = opts.get("pool_name") or "pool-parking"
    gw_addr = opts.get("gateway") or "10.99.99.1/24"
    profile_name = opts.get("profile_name") or "parking-prof"
    captive_url = _strip_captive_scheme(
        opts.get("captive_url") or CAPTIVE_URL)

    pool_path = api.path("/ip/pool")
    pool = mode.wrap(pool_path, "/ip/pool")
    if not any(p.get("name") == pool_name for p in pool_path):
        pool.add(name=pool_name, ranges=pool_range)
    else:
        mode.log.append(f"[skip] /ip pool {pool_name} already exists")

    addr_path = api.path("/ip/address")
    addr_w = mode.wrap(addr_path, "/ip/address")
    gw_ip = gw_addr.split("/")[0]
    have_gw = any((r.get("address") or "").startswith(gw_ip) for r in addr_path)
    if not have_gw:
        iface_name = opts.get("gateway_iface")
        if not iface_name:
            for r in api.path("/interface"):
                n = r.get("name", "")
                if n in ("ether3", "ether2", "combo1") and r.get("running"):
                    iface_name = n; break
        iface_name = iface_name or "ether3"
        addr_w.add(address=gw_addr, interface=iface_name,
                   comment="auto-isp-parking-gw")
    else:
        mode.log.append(f"[skip] {gw_ip} gateway already present")

    prof_path = api.path("/ppp/profile")
    prof = mode.wrap(prof_path, "/ppp/profile")
    if not any(p.get("name") == profile_name for p in prof_path):
        prof.add(name=profile_name,
                 **{"local-address": gw_ip,
                    "remote-address": pool_name,
                    "dns-server": gw_ip,
                    "rate-limit": "512k/512k",
                    "comment": "auto-isp-parking-profile"})
    else:
        mode.log.append(f"[skip] PPP profile {profile_name} already exists")

    al_path = api.path("/ip/firewall/address-list")
    al = mode.wrap(al_path, "/ip/firewall/address-list")
    existing = {(r.get("address"), r.get("list")) for r in al_path}
    park_subnets = ["10.99.99.0/24", "10.255.0.0/24"]
    for subnet in park_subnets:
        if (subnet, "parked-customers") not in existing:
            al.add(address=subnet, list="parked-customers",
                   comment="auto-isp-parked-cust")
        else:
            mode.log.append(f"[skip] {subnet} already in parked-customers")

    fltr_path = api.path("/ip/firewall/filter")
    fltr = mode.wrap(fltr_path, "/ip/firewall/filter")
    have_cmts = {r.get("comment", "") for r in fltr_path}
    wanted = [
        ("forward", "drop", {"src-address-list": "parked-customers",
                              "dst-address-type": "!local"},
         "auto-isp-parking-block"),
        ("forward", "accept", {"src-address-list": "parked-customers",
                                "dst-address": CLOUD_IP},
         "auto-isp-parking-allow-cloud"),
        ("forward", "accept", {"src-address-list": "parked-customers",
                                "protocol": "udp", "dst-port": "53"},
         "auto-isp-parking-allow-dns"),
    ]
    for chain, action, extra, cmt in wanted:
        if cmt in have_cmts:
            mode.log.append(f"[skip] filter '{cmt}' already exists")
        else:
            params = {"chain": chain, "action": action,
                      "comment": cmt, "place-before": "0"}
            params.update(extra)
            fltr.add(**params)

    nat_path = api.path("/ip/firewall/nat")
    nat_w = mode.wrap(nat_path, "/ip/firewall/nat")
    have_nat_cmts = {r.get("comment", "") for r in nat_path}
    # # __DSTNAT_CLOUD_EXCEPTION__
    # Skip dst-natting traffic destined to the cloud captive portal
    # itself — otherwise the browser hits the Mikrotik proxy, gets
    # 302'd back to http://www.autoispbilling.com/pay/captive, that
    # request is dst-natted again, and so on until the browser bails
    # with ERR_TOO_MANY_REDIRECTS.
    for port, cmt in [("80", "auto-isp-park-http"),
                       ("443", "auto-isp-park-https")]:
        # Always (re)write the rule to the correct shape: if a stale
        # rule exists WITHOUT the !cloud exception we must heal it.
        existing = None
        for r in nat_path:
            if r.get("comment") == cmt:
                existing = r
                break
        wanted_kwargs = {
            "src-address-list": "parked-customers",
            "dst-address": f"!{CLOUD_IP}",
            "protocol": "tcp",
            "dst-port": port,
            "to-ports": "8089",
            "place-before": "0",
        }
        if existing is not None:
            # If the existing rule is already correct, skip; otherwise
            # update in place (no flap, no place-before reshuffle).
            mismatch = False
            for k, v in wanted_kwargs.items():
                # Mikrotik stores hyphenated keys; place-before is
                # only used on add, ignore on diff.
                if k == "place-before":
                    continue
                if str(existing.get(k, "")) != str(v):
                    mismatch = True
                    break
            if mismatch:
                nat_w.update(**{".id": existing[".id"],
                                "src-address-list": "parked-customers",
                                "dst-address": f"!{CLOUD_IP}",
                                "protocol": "tcp",
                                "dst-port": port,
                                "to-ports": "8089"})
                mode.log.append(
                    f"[heal] dstnat '{cmt}' updated to skip !{CLOUD_IP}")
            else:
                mode.log.append(
                    f"[skip] dstnat '{cmt}' already correct")
        else:
            kw = dict(wanted_kwargs)
            kw["comment"] = cmt
            nat_w.add(chain="dstnat", action="redirect", **kw)

    prx_path = api.path("/ip/proxy")
    prx = mode.wrap(prx_path, "/ip/proxy")
    for r in prx_path:
        if not r.get("enabled"):
            prx.update(**{".id": r[".id"], "enabled": "yes", "port": "8089"})
        else:
            mode.log.append("[skip] /ip proxy already enabled")

    # _BUG7_LEGACY_PARK_ : migrate + drop the legacy "parking-pool"
    # pool (10.255.x.x) so disabled customers can no longer land there
    try:
        pools_now = list(api.path("/ip/pool"))
        legacy = next((p for p in pools_now
                        if p.get("name") == "parking-pool"), None)
        if legacy:
            prof_path = api.path("/ppp/profile")
            for p in list(prof_path):
                if p.get("remote-address") == "parking-pool":
                    mode.wrap(prof_path, "/ppp/profile").update(
                        **{".id": p[".id"], "remote-address": "pool-parking"})
                    mode.log.append(
                        f"[ok] migrated profile {p.get('name')} remote-address -> pool-parking")
            active_legacy = [s for s in api.path("/ppp/active")
                              if (s.get("address") or "")
                                  .startswith("10.255.")]
            if not active_legacy:
                mode.wrap(api.path("/ip/pool"), "/ip/pool").remove(
                    legacy[".id"])
                mode.log.append(
                    "[ok] removed legacy parking-pool (10.255.x.x)")
            else:
                mode.log.append(
                    f"[skip] legacy parking-pool kept — "
                    f"{len(active_legacy)} live sessions still on 10.255.x.x")
        else:
            mode.log.append(
                "[skip] legacy parking-pool (10.255.x.x) already absent")
    except Exception as _e:
        mode.log.append(f"[warn] legacy parking-pool cleanup: {_e}")

    pacc_path = api.path("/ip/proxy/access")
    pacc = mode.wrap(pacc_path, "/ip/proxy/access")
    found = False
    for r in pacc_path:
        if r.get("action") == "deny" and ("captive" in (r.get("comment", ""))
                                            or "park-redir" in (r.get("comment", ""))):
            if (r.get("redirect-to") or "") != captive_url:
                pacc.update(**{".id": r[".id"], "redirect-to": captive_url})
            else:
                mode.log.append("[skip] proxy redirect already points at captive URL")
            found = True; break
    if not found:
        pacc.add(action="deny",
                 **{"redirect-to": captive_url,
                    "comment": "auto-isp-park-redir-captive"})


# ---------------------------------------------------------------- 6. anti-BF
def _apply_anti_bruteforce(api, mode, opts, **_):
    al_path = api.path("/ip/firewall/address-list")
    al = mode.wrap(al_path, "/ip/firewall/address-list")
    existing = {(r.get("address"), r.get("list")) for r in al_path}

    mgmt_ips = list(opts.get("allowed_mgmt_ips") or ([CLOUD_IP] + ADMIN_LANS))
    for addr in mgmt_ips:
        if (addr, "allowed-mgmt") not in existing:
            al.add(address=addr, list="allowed-mgmt",
                   comment="auto-isp-mgmt-allow")
            existing.add((addr, "allowed-mgmt"))
        else:
            mode.log.append(f"[skip] {addr} already in allowed-mgmt")

    if opts.get("detect_live_admins", True):
        import re as _re
        detected = set()
        try:
            for u in api.path("/user/active"):
                a = u.get("address") or ""
                if _re.fullmatch(r"\d+\.\d+\.\d+\.\d+", a) \
                   and not a.startswith("127."):
                    detected.add(a)
        except Exception:
            pass
        for ip in detected:
            if (ip, "allowed-mgmt") not in existing:
                al.add(address=ip, list="allowed-mgmt",
                       comment="auto-isp-detected-admin-ip")
                existing.add((ip, "allowed-mgmt"))
        opts["_detected"] = detected
    else:
        opts["_detected"] = set()

    if opts.get("wan_list", True):
        ilst_path = api.path("/interface/list")
        ilst = mode.wrap(ilst_path, "/interface/list")
        if not any(r.get("name") == "WAN" for r in ilst_path):
            ilst.add(name="WAN", comment="auto-isp-wan-list")
            members = mode.wrap(api.path("/interface/list/member"),
                                "/interface/list/member")
            for cand in ("combo1", "ether1"):
                try:
                    members.add(list="WAN", interface=cand); break
                except Exception:
                    pass
        else:
            mode.log.append("[skip] interface-list WAN already exists")

    if opts.get("auto_ban_rules", True):
        fltr_path = api.path("/ip/firewall/filter")
        fltr = mode.wrap(fltr_path, "/ip/firewall/filter")
        have_cmts = {r.get("comment", "") for r in fltr_path}
        for chain, action, extra, cmt in [
            ("input", "add-src-to-address-list",
             {"in-interface-list": "WAN",
              "src-address-list": "!allowed-mgmt",
              "protocol": "tcp",
              "dst-port": "22,80,443,8291,8728,8729",
              "address-list": "auto-banned",
              "address-list-timeout": "1d"},
             "auto-isp-bf-add"),
            ("input", "drop",
             {"in-interface-list": "WAN",
              "src-address-list": "auto-banned"},
             "auto-isp-bf-drop"),
        ]:
            if cmt in have_cmts:
                mode.log.append(f"[skip] filter '{cmt}' already exists")
            else:
                params = {"chain": chain, "action": action,
                          "place-before": "0", "comment": cmt}
                params.update(extra)
                fltr.add(**params)

    if opts.get("lockdown_services", False):
        detected = opts.get("_detected") or set()
        if not detected and not opts.get("force_lockdown"):
            mode.log.append("[skip] no live IPv4 admin sessions — "
                            "skipping /ip service lockdown to avoid lockout")
        else:
            svc_path = api.path("/ip/service")
            svc = mode.wrap(svc_path, "/ip/service")
            locked = ",".join([CLOUD_IP + "/32"] + ADMIN_LANS
                              + [ip + "/32" for ip in detected])
            for s in svc_path:
                n = s.get("name")
                if n in ("api", "api-ssl", "winbox", "ssh"):
                    if s.get("address") != locked:
                        svc.update(**{".id": s[".id"], "address": locked})
                    else:
                        mode.log.append(f"[skip] /ip service {n} already restricted")

    if opts.get("disable_insecure", True):
        svc_path = api.path("/ip/service")
        svc = mode.wrap(svc_path, "/ip/service")
        for s in svc_path:
            n = s.get("name")
            if n in ("telnet", "ftp", "www", "www-ssl"):
                if not s.get("disabled"):
                    svc.update(**{".id": s[".id"], "disabled": "yes"})
                else:
                    mode.log.append(f"[skip] /ip service {n} already disabled")


# ---------------------------------------------------------------- registry
KINDS: Dict[str, Callable] = {
    "std-users":      _apply_std_users,
    "api-admin":      _apply_api_admin,
    "rsc-hardening":  _apply_rsc_hardening,
    "universal-nat":  _apply_universal_nat,
    "parking-pool":   _apply_parking_pool,
    "anti-bruteforce": _apply_anti_bruteforce,
}


def register(app, *, get_db):

    @app.post("/api/superadmin/admins/{company_id}/nas/{nas_id}/apply/{kind}")
    async def apply_kind(company_id: str, nas_id: int, kind: str,
                          request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return JSONResponse({"success": False, "message": "auth"},
                                status_code=401)
        nas = _resolve_nas(db, company_id, nas_id)
        if not nas:
            return JSONResponse({"success": False, "message": "NAS not found"},
                                status_code=404)

        try:
            body = await request.json()
        except Exception:
            body = {}
        dry_run = bool(body.get("dry_run", True))
        opts = body.get("opts") or {}

        if kind == "auto-config":
            run_list = ["std-users", "api-admin", "rsc-hardening",
                         "universal-nat", "parking-pool", "anti-bruteforce"]
            skip_subs = set((opts or {}).get("__skip_subs__") or [])
            if skip_subs:
                run_list = [s for s in run_list if s not in skip_subs]
        else:
            if kind not in KINDS:
                return JSONResponse({"success": False,
                                      "message": f"unknown kind '{kind}'"},
                                     status_code=400)
            run_list = [kind]

        log: list = []
        if dry_run:
            log.append("[mode] DRY-RUN — no changes will be written to the router")
        else:
            log.append("[mode] APPLY — writing changes live to the router")

        api = None
        try:
            api = _connect(nas)
            log.append(f"[connected] {nas.ip_address}:{nas.port or 8728} "
                       f"as {nas.api_username or 'admin'}")
        except Exception as e:
            return JSONResponse({"success": False,
                                  "message": f"connect failed: {e}",
                                  "log": log}, status_code=502)

        mode = _Mode(dry_run=dry_run, log=log)
        for sub in run_list:
            log.append(f"--- {sub} ---")
            sub_opts = (opts.get(sub) if isinstance(opts.get(sub), dict)
                         else opts) or {}
            try:
                KINDS[sub](api=api, mode=mode, opts=sub_opts,
                            db=db, nas=nas)
            except Exception as e:
                log.append(f"[ERR] {sub}: {e}")
                log.append(traceback.format_exc(limit=2))

        try:
            api.close()
        except Exception:
            pass

        plans = sum(1 for ln in log if ln.startswith("[plan]"))
        oks   = sum(1 for ln in log if ln.startswith("[ok]"))
        skips = sum(1 for ln in log if ln.startswith("[skip]"))
        errs  = sum(1 for ln in log if ln.startswith("[ERR]"))
        summary = (f"DRY-RUN — would make {plans} changes, "
                   f"{skips} already-present" if dry_run
                   else f"APPLIED — {oks} new, {skips} already-present, "
                        f"{errs} errors")

        return {"success": True, "kind": kind, "applied": run_list,
                "log": log, "summary": summary, "dry_run": dry_run,
                "counts": {"plans": plans, "oks": oks,
                            "skips": skips, "errs": errs},
                "nas_name": nas.name, "nas_ip": nas.ip_address}

    print("[sa_nas_apply] superadmin apply router wired -> /api/superadmin/admins/{cid}/nas/{nid}/apply/{kind}")
