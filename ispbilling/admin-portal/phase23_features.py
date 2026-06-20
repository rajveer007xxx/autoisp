"""
Phase 2 + 3 Features (Feb 2026)
#2 DNS Filter, #3 TR-069 auto-provision hook, #5 Outage Detector,
#7 Self-Upgrade, #8 Referrals, #10 Lead CRM Kanban, #11 Multi-language.

ALL endpoints additive. RBAC: admin (company), sub-lco (own customers),
employee (locality + sub_lco). SMS/WA gated by Superadmin feature flags.
"""
from __future__ import annotations
import os, json, secrets, string, hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import Request, Depends, HTTPException, Form
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text


# ──────────── Feature-flag gate ────────────
def company_feature_enabled(db: Session, company_id: str, key: str,
                             default: bool = True) -> bool:
    """Check if a feature is enabled for a given company.
    default=True for new features (opt-out); default=False for SMS/WA (opt-in)."""
    if not company_id:
        return default
    row = db.execute(text(f"""SELECT {key} FROM company_feature_flags
                                WHERE company_id=:c"""), {"c": company_id}).fetchone()
    if row is None:
        return default
    return bool(row[0])


# ──────────── helpers ────────────
def _scoped_customer_filter(request: Request, db: Session, base_query):
    from database import Customer, Employee
    company_id = request.session.get("company_id")
    base_query = base_query.filter(Customer.company_id == company_id)
    utype = (request.session.get("user_type") or "").lower()
    if utype == "sub_lco":
        sub_lco_id = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
        base_query = base_query.filter(Customer.sub_lco_id == sub_lco_id)
    elif utype == "employee":
        from employee_scope import resolve_locations
        from sqlalchemy import func as _f
        emp_id = int(request.session.get("employee_id") or 0)
        locs = resolve_locations(db, company_id, emp_id)
        if locs:
            base_query = base_query.filter(_f.upper(_f.trim(Customer.locality)).in_(locs))
        else:
            base_query = base_query.filter(_f.upper(_f.trim(Customer.locality)) == "__NONE__")
        emp_row = db.query(Employee.sub_lco_id).filter(Employee.id == emp_id).first()
        emp_slco = int(emp_row[0]) if emp_row and emp_row[0] is not None else None
        if emp_slco is None:
            base_query = base_query.filter(Customer.sub_lco_id.is_(None))
        else:
            base_query = base_query.filter(Customer.sub_lco_id == emp_slco)
    return base_query


def _scope_filter_sql(request: Request, db: Session, alias: str = ""):
    """Returns (where_clause, params) snippet suitable for raw SQL on tables
    that join customers via customer_id."""
    company_id = request.session.get("company_id")
    utype = (request.session.get("user_type") or "").lower()
    p = alias + "." if alias else ""
    where = [f"{p}company_id = :_c"]
    params = {"_c": company_id}
    if utype in ("admin", "superadmin"):
        return " AND ".join(where), params
    # sub_lco / employee — must join customer
    if utype == "sub_lco":
        sub_lco_id = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
        where.append(f"{p}customer_id IN (SELECT customer_id FROM customers WHERE company_id=:_c AND sub_lco_id=:_slco)")
        params["_slco"] = sub_lco_id
    elif utype == "employee":
        from database import Employee
        emp_id = int(request.session.get("employee_id") or 0)
        from employee_scope import resolve_locations
        locs = resolve_locations(db, company_id, emp_id)
        if not locs:
            where.append("1=0")
        else:
            placeholders = ",".join([f":_loc{i}" for i in range(len(locs))])
            where.append(f"{p}customer_id IN (SELECT customer_id FROM customers WHERE company_id=:_c AND UPPER(TRIM(locality)) IN ({placeholders}))")
            for i, loc in enumerate(locs):
                params[f"_loc{i}"] = loc
    return " AND ".join(where), params


# ════════════════════════════════════════════════════════
def register(app, templates, get_db, require_auth, get_admin_context, _emp_has_perm):

    # ============================================================
    # #2 DNS Filter (per-plan & per-customer profile)
    # ============================================================
    @app.get("/admin/dns-profiles", response_class=HTMLResponse)
    async def admin_dns_profiles_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return HTMLResponse("<h3>Forbidden</h3>", 403)
        ctx = get_admin_context(request, db, active_page="dns_profiles")
        return templates.TemplateResponse("admin_dns_profiles.html", ctx)

    @app.get("/api/admin/dns-profiles")
    async def api_dns_profiles_list(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        rows = db.execute(text("""SELECT id,name,description,upstream_v4,upstream_v6,
                                          block_categories,is_default,created_at
                                     FROM dns_profiles WHERE company_id=:c
                                  ORDER BY is_default DESC, id"""),
                          {"c": company_id}).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "name": r[1], "description": r[2] or "",
             "upstream_v4": r[3] or "", "upstream_v6": r[4] or "",
             "categories": json.loads(r[5] or "[]"),
             "is_default": bool(r[6]), "created_at": str(r[7])}
            for r in rows]}

    @app.post("/api/admin/dns-profiles")
    async def api_dns_profiles_create(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        name = (data.get("name") or "").strip()
        if not name:
            return {"success": False, "message": "name required"}
        cats = data.get("categories") or []
        db.execute(text("""INSERT INTO dns_profiles
            (company_id,name,description,upstream_v4,upstream_v6,block_categories,is_default)
            VALUES (:c,:n,:d,:v4,:v6,:cat,:def)"""),
            {"c": company_id, "n": name,
             "d": data.get("description") or "",
             "v4": data.get("upstream_v4") or "1.1.1.3",
             "v6": data.get("upstream_v6") or "",
             "cat": json.dumps(cats),
             "def": 1 if data.get("is_default") else 0})
        db.commit()
        return {"success": True, "id": db.execute(text("SELECT last_insert_rowid()")).scalar()}

    @app.put("/api/admin/dns-profiles/{pid}")
    async def api_dns_profiles_update(pid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        db.execute(text("""UPDATE dns_profiles
            SET name=:n,description=:d,upstream_v4=:v4,upstream_v6=:v6,
                block_categories=:cat,is_default=:def,updated_at=datetime('now')
            WHERE id=:i AND company_id=:c"""),
            {"i": pid, "c": company_id,
             "n": data.get("name"), "d": data.get("description") or "",
             "v4": data.get("upstream_v4"), "v6": data.get("upstream_v6") or "",
             "cat": json.dumps(data.get("categories") or []),
             "def": 1 if data.get("is_default") else 0})
        db.commit()
        return {"success": True}

    @app.delete("/api/admin/dns-profiles/{pid}")
    async def api_dns_profiles_delete(pid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        db.execute(text("DELETE FROM dns_profiles WHERE id=:i AND company_id=:c"),
                   {"i": pid, "c": company_id})
        db.execute(text("UPDATE plans SET dns_profile_id=NULL WHERE dns_profile_id=:i"), {"i": pid})
        db.execute(text("UPDATE customers SET dns_profile_id=NULL WHERE dns_profile_id=:i"), {"i": pid})
        db.commit()
        return {"success": True}

    @app.post("/api/admin/dns-profiles/{pid}/push")
    async def api_dns_profiles_push(pid: int, request: Request, db: Session = Depends(get_db)):
        """Push DNS-profile config to MikroTik for all customers using this profile.
        Uses existing `nas_devices` API; falls back to dry-run if RouterOS unreachable."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        prof = db.execute(text("""SELECT name,upstream_v4,upstream_v6,block_categories
                                    FROM dns_profiles WHERE id=:i AND company_id=:c"""),
                          {"i": pid, "c": company_id}).fetchone()
        if not prof:
            return {"success": False, "message": "Profile not found"}
        # Find customers tied to this profile (direct or via plan)
        rows = db.execute(text("""SELECT customer_id, customer_name, static_ip_address
                                    FROM customers
                                   WHERE company_id=:c
                                     AND (dns_profile_id=:i
                                          OR plan_id IN (SELECT id FROM plans WHERE dns_profile_id=:i))"""),
                          {"c": company_id, "i": pid}).fetchall()
        # Push DNS to ACS-managed ONUs for each linked customer.
        # Uses the existing olt_routes._genieacs_provision helper.
        try:
            from olt_routes import _genieacs_provision as _ga_set, engine as _olt_eng
            ga_ok = True
        except Exception:
            ga_ok = False
        upstream_v4 = prof[1] or "1.1.1.3"
        upstream_v6 = prof[2] or ""
        # Standard TR-098 DNS path covers most CPE
        dns_csv = upstream_v4
        if upstream_v6: dns_csv += "," + upstream_v6
        params = {
            "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.DNSServers": dns_csv,
            "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.DNSServers": dns_csv,
        }
        pushed = 0; failed = 0
        details = []
        for r in rows:
            cust_id = r[0]
            # find first ONU linked to this customer
            onu_serial = None
            if ga_ok:
                try:
                    with _olt_eng.begin() as _c:
                        s_row = _c.exec_driver_sql(
                            "SELECT serial FROM onus WHERE company_id=? "
                            "AND customer_id=? AND COALESCE(serial,'')!='' "
                            "ORDER BY id LIMIT 1",
                            (company_id, cust_id)).fetchone()
                    if s_row: onu_serial = s_row[0]
                except Exception:
                    pass
            applied = False; err = ""
            if onu_serial:
                res = _ga_set(company_id, device_serial=onu_serial, params=params)
                if res.get("ok"):
                    applied = True; pushed += 1
                else:
                    failed += 1; err = (res.get("error") or "")[:120]
            else:
                failed += 1; err = "No ONU bound or ACS missing"
            details.append({"customer_id": cust_id, "name": r[1],
                            "onu_serial": onu_serial or "-",
                            "applied": applied, "error": err})
        return {"success": True, "profile": prof[0], "upstream_v4": upstream_v4,
                "categories": json.loads(prof[3] or "[]"),
                "pushed": pushed, "failed": failed, "total": len(rows),
                "router_online": ga_ok,
                "customers": details[:50]}

    @app.post("/api/admin/dns-profiles/bulk-assign")
    async def api_dns_profiles_bulk_assign(request: Request, db: Session = Depends(get_db)):
        """Assign (or clear) a DNS profile on N customer_ids in one shot.
        Body: {"profile_id": int|null, "customer_ids": ["...", ...]}.
        profile_id = null/0 → clears the override (falls back to plan default).
        """
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try:
            data = await request.json()
        except Exception:
            data = {}
        try:
            pid = int(data.get("profile_id") or 0) or None
        except Exception:
            pid = None
        ids = data.get("customer_ids") or []
        if not isinstance(ids, list) or not ids:
            return {"success": False, "message": "customer_ids[] is required"}
        # If pid given, ensure it belongs to this tenant
        if pid:
            row = db.execute(text("SELECT 1 FROM dns_profiles WHERE id=:i AND company_id=:c"),
                             {"i": pid, "c": company_id}).fetchone()
            if not row:
                return {"success": False, "message": "DNS profile not found for this tenant"}
        # Build a parameterised IN-clause safely
        placeholders = ",".join([f":id{i}" for i in range(len(ids))])
        params = {"c": company_id, "p": pid}
        for i, v in enumerate(ids):
            params[f"id{i}"] = str(v)
        res = db.execute(text(f"""UPDATE customers SET dns_profile_id=:p
                                    WHERE company_id=:c AND customer_id IN ({placeholders})"""),
                         params)
        db.commit()
        return {"success": True,
                "updated": res.rowcount or 0,
                "profile_id": pid,
                "action": "cleared" if pid is None else "assigned"}

    # ============================================================
    # #3 TR-069 auto-provision hook on Connection-Request approval
    # (the connection_requests workflow lives below)
    # ============================================================
    def _genieacs_provision(customer_id: str, plan_id: Optional[int],
                            db: Session, company_id: str) -> Dict[str, Any]:
        """Real TR-069 push using existing olt_routes._genieacs_auto_push.
        Loops every ONU bound to this customer."""
        try:
            from olt_routes import _genieacs_auto_push, engine as _olt_eng
            with _olt_eng.begin() as _c:
                onus = _c.exec_driver_sql(
                    "SELECT id FROM onus WHERE company_id=? AND customer_id=?",
                    (company_id, customer_id)).fetchall()
            if not onus:
                return {"success": False, "error": "No ONUs bound to this customer"}
            results = []
            ok_count = 0
            for (oid,) in onus:
                r = _genieacs_auto_push(company_id, oid,
                                        reason="lead approval auto-provision")
                results.append({"onu_id": oid, "ok": bool(r.get("ok")),
                                "skip": r.get("skip"), "error": r.get("error")})
                if r.get("ok"): ok_count += 1
            return {"success": ok_count > 0,
                    "onu_count": len(onus), "ok_count": ok_count,
                    "results": results}
        except Exception as e:
            return {"success": False, "error": f"ACS push failed: {e}"}

    # ============================================================
    # #5 Outage Detector
    # ============================================================
    @app.get("/admin/outages", response_class=HTMLResponse)
    async def admin_outages_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        ctx = get_admin_context(request, db, active_page="outages")
        return templates.TemplateResponse("admin_outages.html", ctx)

    @app.get("/api/admin/outages")
    async def api_outages_list(request: Request, db: Session = Depends(get_db),
                                status: str = "open"):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        sql = """SELECT id, olt_id, pon_port, onu_count, affected_ids, status,
                        started_at, resolved_at, complaint_id, notified
                   FROM outage_events
                  WHERE company_id=:c"""
        params = {"c": company_id}
        if status and status != "all":
            sql += " AND status=:s"; params["s"] = status
        sql += " ORDER BY id DESC LIMIT 200"
        rows = db.execute(text(sql), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "olt_id": r[1], "pon_port": r[2],
             "onu_count": r[3], "affected_ids": json.loads(r[4] or "[]"),
             "status": r[5], "started_at": str(r[6]),
             "resolved_at": str(r[7]) if r[7] else None,
             "complaint_id": r[8], "notified": bool(r[9])}
            for r in rows]}

    @app.post("/api/admin/outages/{oid}/resolve")
    async def api_outages_resolve(oid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        db.execute(text("""UPDATE outage_events SET status='resolved',
                                  resolved_at=datetime('now')
                            WHERE id=:i AND company_id=:c"""),
                   {"i": oid, "c": company_id})
        db.commit()
        return {"success": True}

    # ============================================================
    # #7 Customer Self-Service Plan Upgrade
    # ============================================================
    @app.get("/customer/upgrade-plan")
    async def customer_upgrade_page(request: Request):
        # _S40zκ_ Merged into /customer/change-plan (Phase 2.5)
        return RedirectResponse("/customer/change-plan", 302)

    @app.get("/api/customer/upgrade-options")
    async def api_customer_upgrade_options(request: Request, db: Session = Depends(get_db)):
        if (request.session.get("user_type") or "").lower() != "customer":
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        from database import Customer, Plan
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()
        if not cust:
            return {"success": False, "message": "Customer not found"}
        cur_plan = db.query(Plan).filter(Plan.id == cust.plan_id,
                                          Plan.company_id == company_id).first()
        # Higher-priced plans only
        plans = db.query(Plan).filter(Plan.company_id == company_id).all()
        opts = []
        for p in plans:
            if cur_plan and float(p.after_tax_amount or 0) <= float(cur_plan.after_tax_amount or 0):
                continue
            opts.append({"id": p.id, "name": p.plan_name,
                         "speed": p.speed or p.download_speed or "",
                         "validity": p.validity, "validity_unit": p.validity_unit or "days",
                         "amount": float(p.after_tax_amount or 0),
                         "description": p.description or ""})
        return {"success": True,
                "current": {"id": cur_plan.id if cur_plan else None,
                             "name": cur_plan.plan_name if cur_plan else "-",
                             "amount": float(cur_plan.after_tax_amount or 0) if cur_plan else 0},
                "options": opts}

    @app.post("/api/customer/upgrade/create")
    async def api_customer_upgrade_create(request: Request, db: Session = Depends(get_db)):
        if (request.session.get("user_type") or "").lower() != "customer":
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        target = int(data.get("target_plan_id") or 0)
        if not target:
            return {"success": False, "message": "target_plan_id required"}
        from database import Customer, Plan
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()
        plan = db.query(Plan).filter(Plan.id == target,
                                      Plan.company_id == company_id).first()
        if not cust or not plan:
            return {"success": False, "message": "Invalid"}
        # _v4730_UPGRADE_WITH_DUES  Charge new plan amount + outstanding
        # previous dues so the user starts the new plan with a clean ledger.
        plan_amt = float(plan.after_tax_amount or 0)
        prev_due = 0.0
        try:
            from main import compute_customer_balance as _bal
            prev_due = max(0.0, float(_bal(cust.customer_id, company_id, db) or 0.0))
        except Exception as _bex:
            print(f"[upgrade] prev-due lookup failed: {_bex}")
        amt = round(plan_amt + prev_due, 2)
        # __S44A__  Gate online payment by per-tenant gateway availability.
        # Logic:
        #   * tenant has any active payment_gateways row → online ok
        #   * else env Razorpay + companies.enable_online_payment=1 → online ok
        #   * else → ticket fallback (same WhatsApp share UX as before)
        try:
            from phase27_upgrade_pay import (
                _tenant_default_gateway as _s44a_def,
                _env_razorpay_available as _s44a_env,
                _company_online_pay_enabled as _s44a_op,
            )
            _tenant_gw = _s44a_def(db, company_id)
            _online_ok = bool(_tenant_gw) or (_s44a_env() and _s44a_op(db, company_id))
        except Exception as _e44a:
            print(f"[upgrade/create] s44a gate-check failed: {_e44a}")
            _online_ok = False
        if not _online_ok:
            import time as _t41, urllib.parse as _u41
            ticket_no = f"PCR{int(_t41.time())}"
            from datetime import datetime as _dt41, timezone as _tz41
            _now41 = _dt41.now(_tz41.utc).strftime('%Y-%m-%d %H:%M:%S')
            db.execute(text("""INSERT INTO complaints
                (company_id, customer_id, ticket_no, complaint_type,
                 subject, description, status, kind, source, target_role,
                 created_at, updated_at, priority)
                VALUES (:c,:cu,:t,'Plan Change Request',
                        :sub,:d,'Pending','Complaint','customer','admin',
                        :ca,:ca,'Medium')"""),
                {"c": company_id, "cu": cust.customer_id, "t": ticket_no,
                 "ca": _now41,
                 "sub": f"Plan upgrade request: {plan.plan_name}",
                 "d": (f"Customer requested upgrade to plan "
                       f"\"{plan.plan_name}\" (ID {plan.id}). "
                       f"Plan amount: Rs.{plan_amt:.2f}. "
                       f"Outstanding dues: Rs.{prev_due:.2f}. "
                       f"Total to collect: Rs.{amt:.2f}. "
                       f"Online payment is disabled for this account — "
                       f"please collect offline.")})
            db.commit()
            admin_phone = ""
            try:
                row_p = db.execute(text(
                    "SELECT phone FROM admins "
                    "WHERE company_id=:c AND phone IS NOT NULL AND phone!='' "
                    "ORDER BY id LIMIT 1"),
                    {"c": company_id}).fetchone()
                if row_p and row_p[0]:
                    admin_phone = str(row_p[0])
            except Exception:
                pass
            if not admin_phone:
                try:
                    row_p = db.execute(text(
                        "SELECT company_phone FROM companies "
                        "WHERE company_id=:c LIMIT 1"),
                        {"c": company_id}).fetchone()
                    if row_p and row_p[0]:
                        admin_phone = str(row_p[0])
                except Exception:
                    pass
            digits = "".join(ch for ch in (admin_phone or "") if ch.isdigit())
            if digits and len(digits) == 10:
                digits = "91" + digits
            txt = (f"Hello, I am customer *{cust.customer_name or cust.customer_id}* "
                   f"(ID: {cust.customer_id}). I would like to upgrade my plan to "
                   f"*{plan.plan_name}* (Rs.{plan_amt:.0f}). "
                   f"My ticket number is {ticket_no}.")
            fallback_wa_url = (f"https://wa.me/{digits}?text={_u41.quote(txt)}"
                               if digits else "")
            return {"success": True, "ticket_only": True,
                    "feature_disabled": True,
                    "ticket": ticket_no,
                    "fallback_wa_url": fallback_wa_url,
                    "message": ("Online payment is not enabled for your ISP. "
                                f"We created ticket {ticket_no} for your "
                                f"admin — please share it on WhatsApp.")}

        # __S44A__  Online payment IS available — save order and hand off
        # to the unified checkout page that picks the right gateway and
        # talks to the multi-tenant adapter stack (Razorpay, PayU,
        # Cashfree, PhonePe, CCAvenue, Stripe).
        oid = db.execute(text("""INSERT INTO plan_change_orders
            (company_id,customer_id,current_plan_id,target_plan_id,amount_due,
             status,rzp_order_id)
            VALUES (:c,:cu,:cp,:tp,:a,'pending',NULL)
            RETURNING id"""),
            {"c": company_id, "cu": cust_id, "cp": cust.plan_id,
             "tp": target, "a": amt}).scalar()
        db.commit()
        return {"success": True, "order_id": oid, "amount": amt,
                "plan_amount": plan_amt, "previous_dues": prev_due,
                "redirect_url": f"/customer/upgrade/{oid}/checkout"}

    @app.post("/api/customer/upgrade/{oid}/confirm")
    async def api_customer_upgrade_confirm(oid: int, request: Request, db: Session = Depends(get_db)):
        if (request.session.get("user_type") or "").lower() != "customer":
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        cust_id = request.session.get("user_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        rzp_payment_id = (data.get("razorpay_payment_id") or "").strip()
        if not rzp_payment_id:
            return {"success": False, "message": "razorpay_payment_id required"}
        row = db.execute(text("""SELECT id, target_plan_id, status
                                    FROM plan_change_orders
                                   WHERE id=:i AND company_id=:c AND customer_id=:cu"""),
                         {"i": oid, "c": company_id, "cu": cust_id}).fetchone()
        if not row:
            return {"success": False, "message": "Order not found"}
        if row[2] == "applied":
            return {"success": False, "message": "Already applied"}
        # Apply: update customer.plan_id
        db.execute(text("""UPDATE customers SET plan_id=:p,
                                 last_renewal_date=date('now')
                            WHERE customer_id=:cu AND company_id=:c"""),
                   {"p": row[1], "cu": cust_id, "c": company_id})
        db.execute(text("""UPDATE plan_change_orders
                              SET status='applied', rzp_payment_id=:rp,
                                  applied_at=datetime('now')
                            WHERE id=:i"""),
                   {"rp": rzp_payment_id, "i": oid})
        db.commit()
        return {"success": True}

    # ============================================================
    # #8 Referrals
    # ============================================================
    def _gen_code(prefix: str = "REF") -> str:
        body = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
        return f"{prefix}-{body}"

    @app.get("/admin/referrals", response_class=HTMLResponse)
    async def admin_referrals_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        ctx = get_admin_context(request, db, active_page="referrals")
        return templates.TemplateResponse("admin_referrals.html", ctx)

    @app.get("/api/admin/referrals/codes")
    async def api_ref_codes(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        where, params = _scope_filter_sql(request, db, alias="rc")
        rows = db.execute(text(f"""SELECT rc.id,rc.customer_id,rc.code,rc.uses,
                                          rc.reward_per_signup,rc.reward_balance,
                                          rc.created_at,c.customer_name,c.customer_phone
                                     FROM referral_codes rc
                                LEFT JOIN customers c ON c.customer_id = rc.customer_id
                                                       AND c.company_id = rc.company_id
                                    WHERE {where}
                                 ORDER BY rc.id DESC LIMIT 500"""), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "customer_id": r[1], "code": r[2], "uses": r[3],
             "reward_per_signup": r[4], "reward_balance": r[5],
             "created_at": str(r[6]), "name": r[7] or "", "phone": r[8] or ""}
            for r in rows]}

    @app.post("/api/admin/referrals/issue")
    async def api_ref_issue(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        cust_id = (data.get("customer_id") or "").strip()
        reward = float(data.get("reward_per_signup") or 100)
        if not cust_id:
            return {"success": False, "message": "customer_id required"}
        # scope check
        from database import Customer
        ok = _scoped_customer_filter(request, db,
              db.query(Customer.customer_id).filter(Customer.customer_id == cust_id)).first()
        if not ok:
            return {"success": False, "message": "Out of scope"}
        existing = db.execute(text("""SELECT code FROM referral_codes
                                       WHERE company_id=:c AND customer_id=:cu"""),
                              {"c": company_id, "cu": cust_id}).fetchone()
        if existing:
            return {"success": True, "code": existing[0], "existing": True}
        code = _gen_code()
        # ensure unique
        for _ in range(5):
            dup = db.execute(text("SELECT 1 FROM referral_codes WHERE code=:c"),
                             {"c": code}).fetchone()
            if not dup: break
            code = _gen_code()
        db.execute(text("""INSERT INTO referral_codes
            (company_id,customer_id,code,reward_per_signup) VALUES (:c,:cu,:co,:r)"""),
            {"c": company_id, "cu": cust_id, "co": code, "r": reward})
        db.commit()
        return {"success": True, "code": code, "existing": False}

    @app.get("/api/admin/referrals/log")
    async def api_ref_log(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        where, params = _scope_filter_sql(request, db, alias="r")
        rows = db.execute(text(f"""SELECT r.id, r.referrer_customer_id, r.referee_customer_id,
                                          r.code, r.status, r.reward_amount,
                                          r.created_at, r.rewarded_at,
                                          c1.customer_name, c2.customer_name
                                     FROM referrals r
                                LEFT JOIN customers c1 ON c1.customer_id = r.referrer_customer_id AND c1.company_id = r.company_id
                                LEFT JOIN customers c2 ON c2.customer_id = r.referee_customer_id AND c2.company_id = r.company_id
                                    WHERE {where}
                                 ORDER BY r.id DESC LIMIT 200"""), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "referrer": r[1], "referee": r[2], "code": r[3],
             "status": r[4], "amount": r[5], "created_at": str(r[6]),
             "rewarded_at": str(r[7]) if r[7] else None,
             "referrer_name": r[8] or "", "referee_name": r[9] or ""}
            for r in rows]}

    @app.post("/api/admin/referrals/{rid}/payout")
    async def api_ref_payout(rid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        db.execute(text("""UPDATE referrals SET status='paid',
                                  rewarded_at=datetime('now')
                            WHERE id=:i AND company_id=:c"""),
                   {"i": rid, "c": company_id})
        db.commit()
        return {"success": True}

    # ============================================================
    # #10 Lead/CRM Kanban for Connection Requests
    # ============================================================
    @app.get("/admin/lead-pipeline", response_class=HTMLResponse)
    async def admin_lead_pipeline_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        ctx = get_admin_context(request, db, active_page="lead_pipeline")
        return templates.TemplateResponse("admin_lead_pipeline.html", ctx)

    @app.get("/sub-lco/lead-pipeline", response_class=HTMLResponse)
    async def sublco_lead_pipeline_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        ctx = get_admin_context(request, db, active_page="lead_pipeline")
        ctx["base_template"] = "base_sub_lco.html"
        return templates.TemplateResponse("admin_lead_pipeline.html", ctx)

    @app.get("/api/admin/leads")
    async def api_leads_list(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        utype = (request.session.get("user_type") or "").lower()
        sql = """SELECT id, full_name, phone, email, address, locality,
                        preferred_plan_id, source, notes, pipeline_stage,
                        assigned_to, assigned_role, customer_id, created_at, updated_at
                   FROM connection_requests
                  WHERE company_id=:c"""
        params = {"c": company_id}
        if utype == "sub_lco":
            slco_id = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
            sql += " AND (assigned_to=:a AND assigned_role='sub_lco')"
            params["a"] = str(slco_id)
        elif utype == "employee":
            emp_id = request.session.get("employee_id")
            sql += " AND ((assigned_to=:a AND assigned_role='employee') OR assigned_to IS NULL)"
            params["a"] = str(emp_id)
        sql += " ORDER BY id DESC LIMIT 500"
        rows = db.execute(text(sql), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "full_name": r[1], "phone": r[2], "email": r[3] or "",
             "address": r[4] or "", "locality": r[5] or "",
             "preferred_plan_id": r[6], "source": r[7] or "manual",
             "notes": r[8] or "", "stage": r[9] or "new",
             "assigned_to": r[10], "assigned_role": r[11],
             "customer_id": r[12], "created_at": str(r[13]),
             "updated_at": str(r[14])}
            for r in rows]}

    @app.post("/api/admin/leads")
    async def api_leads_create(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        name = (data.get("full_name") or "").strip()
        phone = (data.get("phone") or "").strip()
        if not name or not phone:
            return {"success": False, "message": "full_name & phone required"}
        db.execute(text("""INSERT INTO connection_requests
            (company_id,full_name,phone,email,address,locality,preferred_plan_id,
             source,notes,pipeline_stage)
            VALUES (:c,:n,:p,:e,:a,:l,:pp,:s,:nt,'new')"""),
            {"c": company_id, "n": name, "p": phone,
             "e": data.get("email") or "", "a": data.get("address") or "",
             "l": data.get("locality") or "",
             "pp": data.get("preferred_plan_id"),
             "s": data.get("source") or "manual",
             "nt": data.get("notes") or ""})
        db.commit()
        return {"success": True, "id": db.execute(text("SELECT last_insert_rowid()")).scalar()}

    @app.put("/api/admin/leads/{lid}")
    async def api_leads_update(lid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        # Whitelist of mutable fields
        upd = {}
        for k in ("pipeline_stage", "assigned_to", "assigned_role", "notes",
                  "full_name", "phone", "email", "address", "locality"):
            if k in data: upd[k] = data[k]
        if not upd:
            return {"success": False, "message": "Nothing to update"}
        sets = ", ".join([f"{k}=:{k}" for k in upd.keys()])
        upd.update({"i": lid, "c": company_id})
        db.execute(text(f"UPDATE connection_requests SET {sets}, updated_at=datetime('now') "
                        f" WHERE id=:i AND company_id=:c"), upd)
        db.commit()
        return {"success": True}

    @app.post("/api/admin/leads/{lid}/approve")
    async def api_leads_approve(lid: int, request: Request, db: Session = Depends(get_db)):
        """Approval flow: marks lead approved, optionally creates customer + fires
        TR-069 auto-provision (#3)."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        row = db.execute(text("""SELECT full_name, phone, email, address, locality,
                                          preferred_plan_id, customer_id
                                     FROM connection_requests
                                    WHERE id=:i AND company_id=:c"""),
                         {"i": lid, "c": company_id}).fetchone()
        if not row:
            return {"success": False, "message": "Not found"}
        # Mark approved
        db.execute(text("""UPDATE connection_requests
                              SET pipeline_stage='approved', approved_at=datetime('now'),
                                  updated_at=datetime('now')
                            WHERE id=:i"""), {"i": lid})
        db.commit()
        provisioned = None
        if row[6]:
            provisioned = _genieacs_provision(row[6], row[5], db, company_id)
        return {"success": True, "provisioned": provisioned}

    # ============================================================
    # #11 Multi-language UI toggle
    # ============================================================
    @app.post("/api/i18n/set")
    async def api_i18n_set(request: Request, db: Session = Depends(get_db)):
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        lang = (data.get("lang") or "en").strip().lower()
        if lang not in ("en", "hi", "mr", "ta"):
            return {"success": False, "message": "Unsupported language"}
        request.session["lang"] = lang
        # persist for customer
        utype = (request.session.get("user_type") or "").lower()
        try:
            if utype == "customer":
                db.execute(text("UPDATE customers SET language_pref=:l WHERE customer_id=:i AND company_id=:c"),
                           {"l": lang, "i": request.session.get("user_id"),
                            "c": request.session.get("company_id")})
                db.commit()
            elif utype == "admin":
                db.execute(text("UPDATE admins SET language_pref=:l WHERE company_id=:c"),
                           {"l": lang, "c": request.session.get("company_id")})
                db.commit()
        except Exception: pass
        return {"success": True, "lang": lang}

    @app.get("/api/i18n/strings")
    async def api_i18n_strings(request: Request, lang: str = "en"):
        l = (lang or request.session.get("lang") or "en").lower()
        return {"success": True, "lang": l, "strings": _I18N.get(l, _I18N["en"])}

    print("[phase23_features] registered: DNS Filter, TR069 hook, Outage Detector, "
          "Self-Upgrade, Referrals, Lead CRM, Multi-language")


# ──────────── i18n strings (compact) ────────────
_I18N = {
    "en": {
        "dashboard": "Dashboard", "customers": "Customers", "plans": "Plans",
        "invoices": "Invoices", "complaints": "Complaints", "logout": "Logout",
        "search": "Search", "save": "Save", "cancel": "Cancel", "delete": "Delete",
        "upgrade_plan": "Upgrade Plan", "current_plan": "Current Plan",
        "select_new_plan": "Select New Plan", "pay_now": "Pay Now",
        "lead_pipeline": "Lead Pipeline", "new": "New", "contacted": "Contacted",
        "visit_scheduled": "Visit Scheduled", "approved": "Approved", "rejected": "Rejected",
        "outages": "Outages", "dns_profiles": "DNS Profiles", "referrals": "Referrals",
    },
    "hi": {
        "dashboard": "डैशबोर्ड", "customers": "ग्राहक", "plans": "प्लान",
        "invoices": "इनवॉइस", "complaints": "शिकायतें", "logout": "लॉग आउट",
        "search": "खोजें", "save": "सहेजें", "cancel": "रद्द करें", "delete": "हटाएँ",
        "upgrade_plan": "प्लान अपग्रेड करें", "current_plan": "वर्तमान प्लान",
        "select_new_plan": "नया प्लान चुनें", "pay_now": "अभी भुगतान करें",
        "lead_pipeline": "लीड पाइपलाइन", "new": "नया", "contacted": "संपर्क किया",
        "visit_scheduled": "मुलाकात तय", "approved": "स्वीकृत", "rejected": "अस्वीकृत",
        "outages": "ब्रेकडाउन", "dns_profiles": "डीएनएस प्रोफ़ाइल", "referrals": "रेफ़रल",
    },
    "mr": {
        "dashboard": "डॅशबोर्ड", "customers": "ग्राहक", "plans": "योजना",
        "invoices": "बिले", "complaints": "तक्रारी", "logout": "बाहेर पडा",
        "search": "शोधा", "save": "जतन करा", "cancel": "रद्द करा", "delete": "हटवा",
        "upgrade_plan": "प्लॅन सुधारा", "current_plan": "सध्याची योजना",
        "select_new_plan": "नवीन योजना निवडा", "pay_now": "आता पैसे भरा",
        "lead_pipeline": "लीड पाइपलाइन", "new": "नवीन", "contacted": "संपर्क केला",
        "visit_scheduled": "भेट निश्चित", "approved": "मंजूर", "rejected": "नाकारली",
        "outages": "खंड", "dns_profiles": "डीएनएस प्रोफाइल", "referrals": "रेफरल",
    },
    "ta": {
        "dashboard": "டாஷ்போர்டு", "customers": "வாடிக்கையாளர்கள்", "plans": "திட்டங்கள்",
        "invoices": "விலைப்பட்டியல்", "complaints": "புகார்கள்", "logout": "வெளியேறு",
        "search": "தேடு", "save": "சேமி", "cancel": "ரத்து", "delete": "நீக்கு",
        "upgrade_plan": "திட்டத்தை மேம்படுத்து", "current_plan": "தற்போதைய திட்டம்",
        "select_new_plan": "புதிய திட்டத்தை தேர்வுசெய்", "pay_now": "இப்போது செலுத்து",
        "lead_pipeline": "வாய்ப்பு குழாய்", "new": "புதிய", "contacted": "தொடர்பு கொண்டது",
        "visit_scheduled": "வருகை திட்டமிடப்பட்டது", "approved": "அனுமதிக்கப்பட்டது", "rejected": "நிராகரிக்கப்பட்டது",
        "outages": "சேவை குறுக்கீடுகள்", "dns_profiles": "DNS சுயவிவரங்கள்", "referrals": "பரிந்துரைகள்",
    },
}
