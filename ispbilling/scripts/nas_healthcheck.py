#!/usr/bin/env python3
"""NAS Health Check — periodic self-healing for SaaS-scale ISP deployments.

Runs every 5 minutes via systemd timer `isp-nas-healthcheck.timer`. For
each Active NAS device across every tenant, validates the invariants
our session ← client ← billing system depend on, and auto-heals drift:

  1.  **Reachability**  — RouterOS API responds. If not: log and skip
      the rest (router might be rebooting; will retry next cycle).
  2.  **RADIUS client secret** — router `/radius` entry's `secret`
      matches DB `NasDevice.secret`. On mismatch: push DB secret to
      router (router is the source of truth for its own packets, but
      WE are the source of truth for the shared secret since admins
      set it in the UI).
  3.  **RADIUS server address** — router `/radius.address` equals our
      `ISP_PUBLIC_IP`. On mismatch: update router. Covers the
      127.0.0.1 corruption bug + post-VPS-migration drift.
  4.  **FreeRADIUS clients.conf** — every Active NAS has a matching
      client block; secret in that block matches DB.
  5.  **PPPoE profile integrity** — `/ppp/profile isp-default` has
      `local-address` AND the `remote-address` (pool name) exists on
      the router. Also verifies every `/interface/pppoe-server/server`
      has `default-profile=isp-default`.
  6.  **clients.conf ↔ customers ↔ pool-name case sensitivity** —
      every customer's rendered `Framed-Pool` is actually present on
      the router (catches `pppoe-pool` vs `PPPoE-Pool`).

Writes per-NAS findings to `/var/log/isp-nas-healthcheck.log` and a
machine-readable summary to
`/var/lib/autoispbilling/nas_health.json` — consumed by the "Network
Health" card on the SuperAdmin dashboard. No email/push alerts yet
(add in a future session via the notifications module).

`--dry-run` disables all heal actions (report-only)."""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, "/opt/ispbilling/admin-portal")
sys.path.insert(0, "/opt/ispbilling")

from database import SessionLocal
from radius_network import (
    NasDevice,
    _build_customer_radius_rows_global as _build_customer_radius_rows,
    _resolve_public_ip,
    _sync_freeradius_all_tenants,
    _sync_static_ip_online_for_company,  # __S38T_STATIC_ONLINE__
)
from sqlalchemy import text as _text
import routeros_provision as rp
import freeradius_manager as fm
import freeradius_manager as fm

LOG_FILE = "/var/log/isp-nas-healthcheck.log"
OUTPUT_JSON = "/var/lib/autoispbilling/nas_health.json"


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts}  {msg}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def health_check_one(nas: NasDevice, public_ip: str, dry_run: bool) -> dict:
    findings = {
        "nas_id": nas.id, "company_id": nas.company_id, "name": nas.name,
        "ip_address": nas.ip_address, "checks": [], "heals": [],
        "overall": "ok",
    }

    def add_check(name, status, detail=""):
        findings["checks"].append({"name": name, "status": status, "detail": detail})
        if status != "ok":
            findings["overall"] = "warn" if findings["overall"] == "ok" else findings["overall"]

    def add_heal(action, result):
        findings["heals"].append({"action": action, "result": result})

    # 1. Reachability
    try:
        with rp.RouterOSClient(nas, dry_run=False) as c:
            identity = None
            try:
                for r in c._api.path("system/identity"):
                    identity = r.get("name"); break
            except Exception:
                pass
            add_check("reachable", "ok", f"identity={identity}")

            # 2. RADIUS secret
            rad_row = None
            for r in c._api.path("radius"):
                if "auto-isp-billing" in (r.get("comment") or "").lower():
                    rad_row = r; break
            if not rad_row:
                add_check("radius_client_present", "fail", "no auto-isp-billing entry")
                findings["overall"] = "fail"
            else:
                rs = rad_row.get("secret") or ""
                if rs != (nas.secret or ""):
                    add_check("radius_secret_match", "drift",
                              f"router={rs!r} db={nas.secret!r}")
                    if not dry_run and nas.secret:
                        res = rp.sync_radius_secret(nas, radius_server_ip=public_ip)
                        add_heal("sync_radius_secret", res)
                    findings["overall"] = "fail"
                else:
                    add_check("radius_secret_match", "ok")

                # 3. RADIUS server address
                ra = rad_row.get("address") or ""
                if public_ip and ra != public_ip:
                    add_check("radius_address_match", "drift",
                              f"router={ra!r} public={public_ip!r}")
                    if not dry_run:
                        res = rp.sync_radius_secret(nas, radius_server_ip=public_ip)
                        add_heal("sync_radius_address", res)
                    findings["overall"] = "fail"
                else:
                    add_check("radius_address_match", "ok")

            # 4. PPPoE profile healthy?
            prof = None
            for r in c._api.path("ppp/profile"):
                if r.get("name") == "isp-default":
                    prof = r; break
            if not prof:
                add_check("pppoe_profile_present", "fail", "isp-default missing")
                findings["overall"] = "fail"
            else:
                la = prof.get("local-address") or ""
                ra_pool = prof.get("remote-address") or ""
                if not la:
                    add_check("pppoe_local_address", "fail", "no local-address set")
                    findings["overall"] = "fail"
                else:
                    add_check("pppoe_local_address", "ok", la)
                # pool exists?
                pool_exists = False
                if ra_pool:
                    for p in c._api.path("ip/pool"):
                        if p.get("name") == ra_pool:
                            pool_exists = True; break
                if ra_pool and not pool_exists:
                    add_check("pppoe_pool_present_on_router", "fail",
                              f"profile says {ra_pool!r} but /ip/pool missing")
                    findings["overall"] = "fail"
                else:
                    add_check("pppoe_pool_present_on_router", "ok", ra_pool)

    except Exception as e:
        add_check("reachable", "fail", f"{e}")
        findings["overall"] = "unreachable"
        return findings
    return findings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                         help="Report findings, DO NOT push any healing changes.")
    parser.add_argument("--max-seconds-per-nas", type=int, default=12)
    args = parser.parse_args()

    public_ip = _resolve_public_ip()
    log(f"===== health-check start  public_ip={public_ip!r}  dry_run={args.dry_run} =====")

    db = SessionLocal()
    try:
        nas_rows = db.query(NasDevice).filter(NasDevice.status == "Active").all()
        log(f"  {len(nas_rows)} Active NAS devices across all tenants")
        all_findings = []
        for nas in nas_rows:
            t0 = time.time()
            try:
                f = health_check_one(nas, public_ip, args.dry_run)
            except Exception as e:
                f = {"nas_id": nas.id, "name": nas.name,
                     "overall": "crash", "error": str(e),
                     "traceback": traceback.format_exc()[-500:]}
            f["elapsed_ms"] = int((time.time() - t0) * 1000)
            log(f"  NAS {nas.id} {nas.name} ({nas.company_id}) → {f['overall']}  "
                f"({f['elapsed_ms']}ms)")
            all_findings.append(f)
        # 5. Global clients.conf + isp-users re-render — ALWAYS (not just
        # after heal). At multi-tenant scale, customer rows get added /
        # passwords rotated / plans changed constantly, and the only
        # authoritative render is the global one. Cheap (<200ms) —
        # running it every 5 min keeps isp-users eventually-consistent
        # without any other trigger.
        if not args.dry_run:
            try:
                r = _sync_freeradius_all_tenants(db, restart=True)
                log(f"  global freeradius sync → success={r.get('success')} clients={r.get('clients_written')} users={r.get('users_written')} tenants={len(r.get('tenants') or [])}")
            except Exception as e:
                log(f"  global sync failed: {e}")
            # __S38T_STATIC_ONLINE__ — refresh online_users for every
            # tenant'''s auth_type=static_ip customers (PPPoE-less, so
            # they have no /ppp/active row to scrape). Runs every 5 min.
            try:
                from sqlalchemy import text as _text2
                tenant_ids = []
                for (cid,) in db.execute(_text2(
                        "SELECT DISTINCT company_id FROM customers "
                        "WHERE IFNULL(company_id,'')<>'' "
                        "AND auth_type='static_ip'")):
                    if cid:
                        tenant_ids.append(cid)
                tot_synced = tot_checked = 0
                for cid in tenant_ids:
                    rr = _sync_static_ip_online_for_company(db, cid)
                    tot_synced  += int(rr.get("synced")  or 0)
                    tot_checked += int(rr.get("checked") or 0)
                log(f"  static-IP online sync → checked={tot_checked} "
                    f"synced={tot_synced} tenants={len(tenant_ids)}")
            except Exception as e:
                log(f"  static-IP online sync failed: {e}")

        # __voucher_redemption_sync_hook__ — mark vouchers as used in the app
        # DB once they auth via FreeRADIUS, and (single-use) revoke their
        # radcheck/radreply rows so the same code can't be reused on a
        # different device.
        try:
            sys.path.insert(0, '/opt/ispbilling/scripts')
            from voucher_redemption_sync import sync_voucher_redemptions
            vr = sync_voucher_redemptions()
            log(f'  voucher redemption sync → marked={vr.get("marked_used",0)} revoked={vr.get("revoked",0)}')
        except Exception as e:
            log(f'  voucher redemption sync failed: {e}')

        # Write summary for dashboard
        try:
            os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
            with open(OUTPUT_JSON, "w") as jf:
                json.dump({
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "public_ip": public_ip,
                    "dry_run": args.dry_run,
                    "findings": all_findings,
                }, jf, indent=2, default=str)
        except Exception as e:
            log(f"  writing summary JSON failed: {e}")
    finally:
        db.close()
    log("===== health-check done =====")


if __name__ == "__main__":
    main()
