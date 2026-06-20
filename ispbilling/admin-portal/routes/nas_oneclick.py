# Module: nas_oneclick endpoints
# Provides 2 SuperAdmin one-click setup endpoints:
#   POST /api/nas-devices/{id}/oneclick-pools
#   POST /api/nas-devices/{id}/oneclick-auth
#
# Both wrap routeros_provision.auto_configure() with explicit option sets
# so SuperAdmin can layer auth modes (pppoe / static / hotspot) on the same
# router without re-pushing already-configured items. Existing buttons
# (Test, Status, Edit, Delete, original Auto-Configure modal) are untouched.

import os
from typing import Any, Dict, List, Optional
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session


def register(app, *, get_db, require_admin):

    @app.post("/api/nas-devices/{nas_id}/oneclick-pools")
    async def api_nas_oneclick_pools(nas_id: int, request: Request,
                                     db: Session = Depends(get_db)):
        """Push a list of IP pools + their gateway addresses to a Mikrotik.
        Idempotent: existing pools with the same name & ranges are skipped
        by routeros_provision._do_add (it matches by name and only updates
        differing attributes)."""
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        body = await request.json()
        pools = body.get("pools") or []
        if not pools:
            return JSONResponse({"ok": False, "error": "no pools provided"},
                                status_code=400)

        from radius_network import NasDevice
        from routeros_provision import auto_configure
        nas = db.query(NasDevice).filter(NasDevice.id == nas_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas not found"},
                                status_code=404)
        if request.session.get("user_type") == "admin" and \
           nas.company_id != request.session.get("company_id"):
            return JSONResponse({"ok": False, "error": "forbidden"},
                                status_code=403)

        radius_ip = (os.environ.get("ISP_PUBLIC_IP")
                     or getattr(request.url, "hostname", None) or "127.0.0.1")
        # Normalise pool dicts to the shape sync_ip_pools_to_router wants.
        normalised: List[Dict[str, Any]] = []
        for p in pools:
            normalised.append({
                "name":        (p.get("name") or "").strip(),
                "network":     (p.get("network") or "").strip(),
                "start_ip":    (p.get("start_ip") or "").strip(),
                "end_ip":      (p.get("end_ip") or "").strip(),
                "interface":   (p.get("interface") or "").strip() or None,
                "gateway":     (p.get("gateway") or "").strip() or None,
                "comment":     "auto-isp-billing oneclick-pool",
            })

        report = auto_configure(
            nas=nas,
            options={"ip_pools": True},
            ip_pools=normalised, plans=[],
            radius_server_ip=radius_ip,
            radius_secret=(nas.secret or "testing123"),
            dry_run=False,
        )
        return JSONResponse({"ok": True, "report": report})


    @app.post("/api/nas-devices/{nas_id}/oneclick-auth")
    async def api_nas_oneclick_auth(nas_id: int, request: Request,
                                    db: Session = Depends(get_db)):
        """Layered auth-mode setup. Body: {
            "pppoe": {"enabled": bool, "interface": str, "service_name": str, "vlan_id": int},
            "static_ip": {"enabled": bool, "wan_interface": str},
            "hotspot": {"enabled": bool, "interface": str, "pool_name": str, "branded_html": bool}
           }
        Each section is optional. Sections that already match the router's
        current state are skipped by underlying _do_add (idempotent)."""
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        body = await request.json()

        from radius_network import NasDevice
        from routeros_provision import auto_configure, RouterOSClient
        nas = db.query(NasDevice).filter(NasDevice.id == nas_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas not found"},
                                status_code=404)
        if request.session.get("user_type") == "admin" and \
           nas.company_id != request.session.get("company_id"):
            return JSONResponse({"ok": False, "error": "forbidden"},
                                status_code=403)

        radius_ip = (os.environ.get("ISP_PUBLIC_IP")
                     or getattr(request.url, "hostname", None) or "127.0.0.1")

        # ---- Auto-detect WAN interface (ether1 fallback) ----
        wan_iface = "ether1"
        try:
            with RouterOSClient(nas, dry_run=False) as c:
                routes = list(c._api.path("ip/route"))
                for r in routes:
                    if r.get("dst-address") in ("0.0.0.0/0", "::/0"):
                        gw_iface = r.get("gateway") or ""
                        # gateway can be `ip%iface` or just `iface`
                        if "%" in gw_iface:
                            wan_iface = gw_iface.split("%", 1)[1]
                        elif gw_iface and not gw_iface[0].isdigit():
                            wan_iface = gw_iface
                        break
        except Exception:
            pass

        pp   = body.get("pppoe")     or {}
        st   = body.get("static_ip") or {}
        hs   = body.get("hotspot")   or {}
        do_pppoe   = bool(pp.get("enabled"))
        do_static  = bool(st.get("enabled"))
        do_hotspot = bool(hs.get("enabled"))
        if not (do_pppoe or do_static or do_hotspot):
            return JSONResponse({"ok": False,
                                 "error": "select at least one auth mode"},
                                status_code=400)

        options: Dict[str, bool] = {
            "queue_types":   True,                  # safe to (re)create
            "radius_client": True,                  # idempotent — used by all 3
            "pppoe_server":  do_pppoe,
            "hotspot":       do_hotspot,
            "parking_pool":  do_static or do_hotspot,
            "firewall_nat_baseline": True,
        }

        # ---- Auto-detect LAN subnets from existing /ip/address ----
        lan_subnets: List[str] = []
        try:
            with RouterOSClient(nas, dry_run=False) as c:
                for a in c._api.path("ip/address"):
                    if a.get("interface") == wan_iface:
                        continue
                    addr = a.get("network") or ""
                    nm = a.get("address") or ""
                    if "/" in nm and not nm.startswith("169.254") and addr:
                        lan_subnets.append(addr + "/" + nm.split("/")[1])
        except Exception:
            pass

        # Persist auth_modes on the NAS row so the rest of the app routes
        # auth correctly (matches how /quick-setup updates it).
        try:
            current = (nas.auth_modes or "").strip()
            wanted: List[str] = []
            for k, on in (("pppoe", do_pppoe), ("static_ip", do_static),
                          ("hotspot", do_hotspot)):
                if on and k not in current.split(","):
                    wanted.append(k)
            if wanted:
                merged = ",".join(filter(None, current.split(",") + wanted))
                nas.auth_modes = merged.strip(",")
                db.add(nas); db.commit()
        except Exception:
            db.rollback()

        report = auto_configure(
            nas=nas, options=options, ip_pools=[], plans=[],
            radius_server_ip=radius_ip,
            radius_secret=(nas.secret or "testing123"),
            dry_run=False,
            wan_interface=wan_iface,
            lan_subnets=lan_subnets or None,
            pppoe_interface=(pp.get("interface") or nas.pppoe_interface or "ether2"),
            pppoe_service_name=(pp.get("service_name") or nas.pppoe_service_name or "isp-pppoe"),
            pppoe_vlan_id=int(pp.get("vlan_id") or nas.pppoe_vlan_id or 0),
            enable_pppoe_server=do_pppoe,
            hotspot_interface=(hs.get("interface") or nas.hotspot_interface or "ether3"),
            enable_hotspot=do_hotspot,
            enable_ppp_use_radius=do_pppoe,
        )

        # ---- Hotspot extras (only if user opted in) ----
        # 1. Push branded captive portal HTML to Mikrotik flash
        # 2. Create user profiles FreeRADIUS expects
        # 3. Auto-bypass non-hotspot subnets so static-IP / PPPoE traffic
        #    isn't intercepted on the same physical iface.
        hs_extras: Dict[str, Any] = {"skipped": True}
        if do_hotspot:
            try:
                from scripts_runtime_hotspot_helpers import (
                    push_branded_captive_portal,
                    ensure_hotspot_user_profiles,
                    ensure_hotspot_bypass_bindings,
                )
                hs_extras = {
                    "branded_html":    push_branded_captive_portal(nas),
                    "user_profiles":   ensure_hotspot_user_profiles(nas),
                    "bypass_bindings": ensure_hotspot_bypass_bindings(nas),
                }
            except Exception as e:
                hs_extras = {"error": str(e)}

        return JSONResponse({
            "ok": True,
            "auth_modes": nas.auth_modes,
            "wan_interface": wan_iface,
            "lan_subnets":   lan_subnets,
            "report":        report,
            "hotspot_extras": hs_extras,
        })


    @app.post("/api/nas-devices/{nas_id}/push-all-customer-secrets")
    async def api_nas_push_all_customer_secrets(nas_id: int, request: Request,
                                                 db: Session = Depends(get_db)):
        """Bulk-push every PPPoE customer's /ppp/secret onto this Mikrotik.
        Idempotent: existing secrets are updated to match the password stored
        in billing DB + plan profile; missing ones are added. Hotspot/static
        customers are skipped (they don't use /ppp/secret)."""
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)

        from radius_network import NasDevice
        from database import Customer, Plan
        import routeros_provision as rp

        nas = db.query(NasDevice).filter(NasDevice.id == nas_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas not found"},
                                status_code=404)
        if request.session.get("user_type") == "admin" and \
           nas.company_id != request.session.get("company_id"):
            return JSONResponse({"ok": False, "error": "forbidden"},
                                status_code=403)

        cust_q = db.query(Customer).filter(Customer.company_id == nas.company_id)
        if hasattr(Customer, "is_deleted"):
            try:
                cust_q = cust_q.filter(Customer.is_deleted == False)  # noqa: E712
            except Exception:
                pass
        rows = cust_q.all()

        plan_cache: Dict[int, Any] = {}
        results: List[Dict[str, Any]] = []
        ok_count = 0
        skip_count = 0
        fail_count = 0
        pool_name = (getattr(nas, "pppoe_pool_name", None) or "pppoe-pool")

        for cust in rows:
            uname = (cust.username or "").strip()
            pwd   = (cust.pppoe_password or "").strip()
            atype = (getattr(cust, "auth_type", None) or "pppoe").strip().lower()
            if not uname or not pwd or atype != "pppoe":
                skip_count += 1
                results.append({"username": uname or "(blank)",
                                 "status": "skipped",
                                 "reason": "missing username/password or non-PPPoE auth_type"})
                continue
            if not cust.plan_id:
                skip_count += 1
                results.append({"username": uname, "status": "skipped",
                                 "reason": "no plan assigned"})
                continue
            plan = plan_cache.get(cust.plan_id)
            if plan is None:
                plan = (db.query(Plan)
                          .filter(Plan.id == cust.plan_id,
                                  Plan.company_id == nas.company_id).first())
                plan_cache[cust.plan_id] = plan
            if not plan or plan.service != "Broadband":
                skip_count += 1
                results.append({"username": uname, "status": "skipped",
                                 "reason": "plan missing or not Broadband"})
                continue
            plan_dict = {
                "plan_name": plan.plan_name,
                "download_speed": plan.download_speed,
                "upload_speed": plan.upload_speed,
                "priority": getattr(plan, "priority", None),
                "queue_type": getattr(plan, "queue_type", None),
            }
            try:
                res = rp.provision_customer_pppoe(
                    nas=nas,
                    customer={"username": uname},
                    plan=plan_dict,
                    password=pwd,
                    pool_name=pool_name,
                    dry_run=False,
                )
                if res.get("success"):
                    ok_count += 1
                    sec = res.get("secret_result") or {}
                    state = "updated" if sec.get("updated") else (
                            "added" if sec.get("added") else "ok")
                    results.append({"username": uname, "status": state})
                else:
                    fail_count += 1
                    results.append({"username": uname, "status": "failed",
                                     "error": res.get("message", "unknown")})
            except Exception as e:
                fail_count += 1
                results.append({"username": uname, "status": "failed",
                                 "error": str(e)})

        return JSONResponse({
            "ok": True,
            "total": len(rows),
            "succeeded": ok_count,
            "skipped": skip_count,
            "failed": fail_count,
            "results": results,
        })


    @app.post("/api/nas-devices/{nas_id}/pull-secrets-from-router")
    async def api_nas_pull_secrets_from_router(nas_id: int, request: Request,
                                                db: Session = Depends(get_db)):
        """Read /ppp/secret from the Mikrotik and write each name+password
        back into customers.pppoe_password (matching by username), then
        regenerate isp-users so FreeRADIUS knows the true passwords. Use
        when /ppp aaa use-radius=yes was flipped on but customer CPEs were
        configured with passwords different from what's in the billing DB.
        """
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        dry_run = bool(body.get("dry_run", False))

        from radius_network import NasDevice, _build_customer_radius_rows_global
        from database import Customer
        import routeros_provision as rp
        import freeradius_manager as fm

        nas = db.query(NasDevice).filter(NasDevice.id == nas_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas not found"},
                                status_code=404)
        if request.session.get("user_type") == "admin" and \
           nas.company_id != request.session.get("company_id"):
            return JSONResponse({"ok": False, "error": "forbidden"},
                                status_code=403)

        # 1. Read /ppp/secret from router
        listing = rp.list_pppoe_secrets(nas)
        if not listing.get("success"):
            return JSONResponse({"ok": False,
                                  "error": "router_unreachable",
                                  "message": listing.get("message", "")},
                                 status_code=502)
        secrets = listing.get("secrets") or []

        # 2. Index secrets by name
        router_pwd_by_name: Dict[str, str] = {}
        for row in secrets:
            nm = (row.get("name") or "").strip()
            pw = (row.get("password") or "").strip()
            if nm and pw:
                router_pwd_by_name[nm] = pw

        # 3. Match against company customers and apply
        cust_q = db.query(Customer).filter(Customer.company_id == nas.company_id)
        if hasattr(Customer, "is_deleted"):
            try:
                cust_q = cust_q.filter(Customer.is_deleted == False)  # noqa: E712
            except Exception:
                pass
        rows = cust_q.all()

        changes: List[Dict[str, Any]] = []
        unchanged = 0
        no_router_secret = 0
        no_username = 0
        for cust in rows:
            uname = (cust.username or "").strip()
            if not uname:
                no_username += 1
                continue
            new_pwd = router_pwd_by_name.get(uname)
            if not new_pwd:
                no_router_secret += 1
                continue
            old_pwd = (cust.pppoe_password or "").strip()
            if new_pwd == old_pwd:
                unchanged += 1
                continue
            changes.append({
                "username": uname,
                "old_password_masked": (old_pwd[:2] + "***" + old_pwd[-1:]) if old_pwd else "(empty)",
                "new_password_masked": new_pwd[:2] + "***" + new_pwd[-1:],
            })
            if not dry_run:
                cust.pppoe_password = new_pwd

        regen_result: Dict[str, Any] = {"skipped": True}
        if not dry_run and changes:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                return JSONResponse({"ok": False,
                                      "error": "db_commit_failed",
                                      "message": str(e)},
                                     status_code=500)
            # Regenerate isp-users for this company
            try:
                cust_rows = _build_customer_radius_rows_global(db, nas.company_id)
                regen_result = fm.sync_users_file(cust_rows, restart=True)
            except Exception as e:
                regen_result = {"success": False, "message": str(e)}

        return JSONResponse({
            "ok": True,
            "dry_run": dry_run,
            "router_secret_count": len(router_pwd_by_name),
            "company_customer_count": len(rows),
            "changes": len(changes),
            "unchanged": unchanged,
            "no_router_secret": no_router_secret,
            "no_username": no_username,
            "diffs": changes,
            "freeradius_regen": regen_result,
        })


    @app.post("/api/customers/sync-detected-passwords")
    async def api_customers_sync_detected_passwords(request: Request,
                                                     db: Session = Depends(get_db)):
        """Bulk equivalent of the per-subscriber 'Save Detected Password'
        button. Walks radpostauth for the latest password the CPE actually
        sent (within max_age_hours), per username, for the current company.
        Updates billing DB + isp-users + reloads FreeRADIUS in one shot."""
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        company_id = request.session.get("company_id") or ""
        if not company_id:
            return JSONResponse({"ok": False, "error": "no_company"},
                                status_code=400)

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        max_age_hours = int(body.get("max_age_hours", 168))   # default: 7 days
        dry_run = bool(body.get("dry_run", False))

        from database import Customer
        from radius_network import _build_customer_radius_rows_global
        import freeradius_manager as fm
        import sqlite3, time

        radacct_path = os.environ.get("RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")

        # Collect company customers (PPPoE only)
        cust_q = db.query(Customer).filter(Customer.company_id == company_id)
        if hasattr(Customer, "is_deleted"):
            try:
                cust_q = cust_q.filter(Customer.is_deleted == False)  # noqa: E712
            except Exception:
                pass
        rows = cust_q.all()
        usernames = [(c.username or "").strip() for c in rows
                     if (c.username or "").strip()]
        if not usernames:
            return JSONResponse({"ok": True, "changes": 0, "diffs": [],
                                 "message": "no PPPoE customers in DB"})

        # Pull latest password capture per username from radpostauth
        # S42J — fence by company_id column so we never inherit another
        # tenant's PPPoE secret when usernames collide.
        try:
            from radpostauth_tenant import backfill_company_id as _rp_bf_nas1
            _rp_bf_nas1(db, radacct_path=radacct_path, limit=5000)
        except Exception:
            pass
        latest_pass: Dict[str, Dict[str, Any]] = {}
        try:
            con = sqlite3.connect(f"file:{radacct_path}?mode=ro", uri=True, timeout=5)
            cur = con.cursor()
            chunk = 400  # SQLite max IN list
            for i in range(0, len(usernames), chunk):
                batch = usernames[i:i+chunk]
                placeholders = ",".join(["?"] * len(batch))
                bind = [str(company_id)] + list(batch) + [f"-{int(max_age_hours)} hours"]
                q = (f"SELECT username, pass, reply, authdate FROM radpostauth "
                     f"WHERE company_id = ? "
                     f"  AND username IN ({placeholders}) "
                     f"  AND IFNULL(pass, '') <> '' "
                     f"  AND authdate >= datetime('now', ?) "
                     f"ORDER BY authdate DESC")
                for un, pw, rep, ad in cur.execute(q, bind).fetchall():
                    if un not in latest_pass:
                        latest_pass[un] = {"pass": pw, "reply": rep, "authdate": ad}
            con.close()
        except Exception as e:
            return JSONResponse({"ok": False,
                                  "error": "radpostauth_query_failed",
                                  "message": str(e)}, status_code=500)

        # Diff vs DB
        diffs: List[Dict[str, Any]] = []
        unchanged = 0
        no_capture = 0
        for c in rows:
            un = (c.username or "").strip()
            if not un:
                continue
            cap = latest_pass.get(un)
            if not cap:
                no_capture += 1
                continue
            new_pwd = cap["pass"]
            old_pwd = (c.pppoe_password or "")
            if new_pwd == old_pwd:
                unchanged += 1
                continue
            diffs.append({
                "username": un,
                "old_masked": (old_pwd[:2] + "***" + old_pwd[-1:]) if old_pwd else "(empty)",
                "new_masked": new_pwd[:2] + "***" + new_pwd[-1:],
                "captured_at": cap["authdate"],
                "last_reply":  cap["reply"],
            })
            if not dry_run:
                c.pppoe_password = new_pwd

        regen: Dict[str, Any] = {"skipped": True}
        if not dry_run and diffs:
            try:
                db.commit()
            except Exception as e:
                db.rollback()
                return JSONResponse({"ok": False,
                                      "error": "db_commit_failed",
                                      "message": str(e)}, status_code=500)
            try:
                cust_rows = _build_customer_radius_rows_global(db, company_id)
                regen = fm.sync_users_file(cust_rows, restart=True)
            except Exception as e:
                regen = {"success": False, "message": str(e)}

        return JSONResponse({
            "ok": True,
            "dry_run": dry_run,
            "company_customer_count": len(rows),
            "captured_count": len(latest_pass),
            "changes": len(diffs),
            "unchanged": unchanged,
            "no_capture": no_capture,
            "diffs": diffs,
            "freeradius_regen": regen,
        })


    @app.get("/api/customers/sync-detected-passwords/count")
    async def api_customers_sync_count(request: Request,
                                        db: Session = Depends(get_db),
                                        max_age_hours: int = 168):
        """Fast count of customers whose latest CPE-captured password
        differs from the billing DB. Used by the User Management header
        badge to show 'X mismatch' at a glance."""
        if request.session.get("user_type") not in ("admin", "superadmin", "sub_lco", "employee"):
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        company_id = request.session.get("company_id") or ""
        if not company_id:
            return JSONResponse({"ok": False, "error": "no_company"},
                                status_code=400)

        from database import Customer
        import sqlite3

        radacct_path = os.environ.get("RADACCT_DB_PATH", "/var/lib/freeradius/radacct.db")

        cust_q = db.query(Customer).filter(Customer.company_id == company_id)
        if hasattr(Customer, "is_deleted"):
            try:
                cust_q = cust_q.filter(Customer.is_deleted == False)  # noqa: E712
            except Exception:
                pass
        rows = cust_q.all()
        names = [(c.username or "").strip() for c in rows
                 if (c.username or "").strip()]
        if not names:
            return JSONResponse({"ok": True, "mismatch_count": 0,
                                  "captured_count": 0,
                                  "company_customer_count": 0})

        # Get latest captured pass per username — S42J: tenant-fenced.
        try:
            from radpostauth_tenant import backfill_company_id as _rp_bf_nas2
            _rp_bf_nas2(db, radacct_path=radacct_path, limit=5000)
        except Exception:
            pass
        latest_pass: Dict[str, str] = {}
        try:
            con = sqlite3.connect(f"file:{radacct_path}?mode=ro", uri=True, timeout=5)
            cur = con.cursor()
            chunk = 400
            for i in range(0, len(names), chunk):
                batch = names[i:i+chunk]
                placeholders = ",".join(["?"] * len(batch))
                bind = [str(company_id)] + list(batch) + [f"-{int(max_age_hours)} hours"]
                q = (f"SELECT username, pass FROM radpostauth "
                     f"WHERE company_id = ? "
                     f"  AND username IN ({placeholders}) "
                     f"  AND IFNULL(pass, '') <> '' "
                     f"  AND authdate >= datetime('now', ?) "
                     f"ORDER BY authdate DESC")
                for un, pw in cur.execute(q, bind).fetchall():
                    if un not in latest_pass:
                        latest_pass[un] = pw
            con.close()
        except Exception as e:
            return JSONResponse({"ok": False, "error": "radpostauth_query_failed",
                                  "message": str(e)}, status_code=500)

        mismatch = 0
        for c in rows:
            un = (c.username or "").strip()
            if not un:
                continue
            cap = latest_pass.get(un)
            if not cap:
                continue
            if cap != (c.pppoe_password or ""):
                mismatch += 1

        return JSONResponse({"ok": True,
                             "company_customer_count": len(rows),
                             "captured_count": len(latest_pass),
                             "mismatch_count": mismatch})
