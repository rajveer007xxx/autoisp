"""
Phase 1 Features (Feb 2026)
Adds: SMS Center (#4), Ticket SLA (#6), Bulk WhatsApp Campaigns (#12),
      IP-Pool Utilization (#14), DB Backup portal (#15).

All routes additive. RBAC: admin (company), sub-lco (own customers),
employee (locality + sub_lco). Wiring is via register(app, templates).
"""
from __future__ import annotations
import sys as _sys; _sys.path.insert(0, '/opt/ispbilling/admin-portal'); from db_compat import get_raw_conn as _compat_conn  # __s56Z2_compat__
import os, json, gzip, hashlib, shutil, time, traceback
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import Request, Depends, HTTPException, Query, Form, UploadFile, File
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text


_DB_PATH = "/var/lib/autoispbilling/autoispbilling.db"
_BACKUP_DIR = "/var/lib/autoispbilling/backups"
os.makedirs(_BACKUP_DIR, exist_ok=True)



# ──── Phase 2/3: Superadmin per-company gate ────
def _company_feat_enabled(db, company_id: str, key: str, default_off: bool = False) -> bool:
    """Check if a feature is enabled for the given company.
    Returns True if no row exists (default ON) unless default_off is True (then OFF)."""
    if not company_id:
        return not default_off
    try:
        from sqlalchemy import text as _t
        row = db.execute(_t(f"SELECT {key} FROM company_feature_flags WHERE company_id=:c"),
                         {"c": company_id}).fetchone()
        if row is None:
            return not default_off
        return bool(row[0])
    except Exception:
        return not default_off

# ─────────────── helpers reused from main ───────────────
def _scoped_customer_filter(request: Request, db: Session, base_query):
    """Apply admin/sub-lco/employee RBAC filter to a Customer query."""
    from database import Customer, Employee
    company_id = request.session.get("company_id")
    base_query = base_query.filter(Customer.company_id == company_id)
    utype = (request.session.get("user_type") or "").lower()
    if utype == "sub_lco":
        sub_lco_id = request.session.get("sub_lco_id") or request.session.get("user_id_int")
        if sub_lco_id is None:
            sub_lco_id = -1
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


def _audit_actor(request: Request) -> Dict[str, str]:
    return {
        "actor_type": (request.session.get("user_type") or "admin"),
        "actor_id":   str(request.session.get("user_id") or ""),
    }


# ─────────────── #4 SMS Center ───────────────
def _twilio_sms_send(to_phone: str, body: str) -> Dict[str, Any]:
    """__SMS_DISABLED__  SMS killed in Twilio→MSG91 migration.
    Admins should use WhatsApp campaigns instead."""
    return {"success": False, "error": "sms_disabled",
            "message": "SMS is disabled. Use WhatsApp campaigns instead."}


def _log_sms(db: Session, company_id, actor_type, actor_id,
             to_phone, to_name, customer_id, body, result, campaign_id=None):
    db.execute(text("""
        INSERT INTO sms_logs(company_id, actor_type, actor_id, to_phone, to_name,
                             customer_id, body, status, sid, error, campaign_id, created_at)
        VALUES (:c, :at, :ai, :tp, :tn, :ci, :b, :s, :sid, :err, :cmp, datetime('now'))
    """), {
        "c": company_id, "at": actor_type, "ai": actor_id,
        "tp": to_phone, "tn": to_name or "", "ci": customer_id or "",
        "b": body[:1600], "s": "sent" if result.get("success") else "failed",
        "sid": result.get("sid", ""), "err": result.get("error", "")[:300] if result.get("error") else "",
        "cmp": campaign_id,
    })
    db.commit()


# ─────────────── #15 DB Backup ───────────────
def _create_backup(triggered_by: str = "manual", actor: str = "system") -> Dict[str, Any]:
    """Online SQLite backup using sqlite3.Connection.backup() — safe with WAL mode."""
    import sqlite3
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(_BACKUP_DIR, f"db-{ts}.sqlite")
    gz  = raw + ".gz"
    try:
        src = _compat_conn()
        dst = sqlite3.connect(raw)
        with dst:
            src.backup(dst)
        src.close(); dst.close()
        with open(raw, "rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(raw)
        size = os.path.getsize(gz)
        with open(gz, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        # log into registry
        eng = _compat_conn()
        eng.execute("""INSERT INTO db_backups(filename, size_bytes, sha256, created_by, trigger)
                       VALUES (?, ?, ?, ?, ?)""",
                    (os.path.basename(gz), size, sha, actor, triggered_by))
        eng.commit(); eng.close()
        # retention: keep last 30
        files = sorted([f for f in os.listdir(_BACKUP_DIR) if f.endswith(".gz")])
        for old in files[:-30]:
            try: os.remove(os.path.join(_BACKUP_DIR, old))
            except Exception: pass
        return {"success": True, "filename": os.path.basename(gz), "size": size}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─────────────── #6 SLA helpers ───────────────
_SLA_DEFAULTS = {"low": 24*60, "medium": 8*60, "high": 4*60, "critical": 60}


def _check_and_escalate_slas() -> Dict[str, Any]:
    """Run by cron. Mark breached + bump escalation_level + send WA/SMS."""
    import sqlite3
    eng = _compat_conn()
    eng.row_factory = sqlite3.Row
    breached = 0; escalated = 0
    rows = eng.execute("""
        SELECT id, ticket_no, company_id, customer_id, priority, status,
               sla_minutes, escalation_level, created_at
          FROM complaints
         WHERE status NOT IN ('Resolved','Closed','closed','resolved')
    """).fetchall()
    now = datetime.now()
    for r in rows:
        try:
            created = datetime.fromisoformat(str(r["created_at"]).replace("Z",""))
        except Exception:
            continue
        sla = int(r["sla_minutes"] or _SLA_DEFAULTS.get((r["priority"] or "medium").lower(), 480))
        deadline = created + timedelta(minutes=sla)
        overdue_min = int((now - deadline).total_seconds() // 60)
        if overdue_min <= 0:
            continue
        breached += 1
        # escalate every 60 min over deadline (cap level at 3)
        target_level = min(3, 1 + overdue_min // 60)
        if target_level > (r["escalation_level"] or 0):
            eng.execute("""UPDATE complaints
                              SET escalation_level=?, escalated_at=datetime('now'),
                                  sla_breached=1
                            WHERE id=?""", (target_level, r["id"]))
            escalated += 1
        else:
            eng.execute("UPDATE complaints SET sla_breached=1 WHERE id=?", (r["id"],))
    eng.commit(); eng.close()
    return {"breached": breached, "escalated": escalated, "ts": now.isoformat()}


# ────────────────────────────────────────────────────────
# PUBLIC: registration into FastAPI app
# ────────────────────────────────────────────────────────
def register(app, templates, get_db, require_auth, get_admin_context, _emp_has_perm):

    # ───────── #4 SMS Center routes ─────────
    def _sms_page(request: Request, db: Session, base_template: str = "base_admin.html"):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        def _fne_ctx(title):
            # __S43ZN__ carry full admin context so banner shows real company / user
            try:
                _c = get_admin_context(request, db, active_page="sms_center") or {}
            except Exception:
                _c = {}
            _c.update({
                "request": request,
                "feature_title": title,
                "base_template": base_template,
                "user_type": (request.session.get("user_type") or "admin").lower(),
            })
            return _c
        if not _company_feat_enabled(db, request.session.get("company_id"), "sms_enabled", default_off=True):
            return templates.TemplateResponse("feature_not_enabled.html", _fne_ctx("SMS Center"), status_code=403)
        utype = (request.session.get("user_type") or "").lower()
        if utype == "employee" and not _emp_has_perm(request, db, "feat.sms"):
            return templates.TemplateResponse("feature_not_enabled.html", _fne_ctx("SMS Center"), status_code=403)
        ctx = get_admin_context(request, db, active_page="sms_center")
        ctx["base_template"] = base_template
        return templates.TemplateResponse("admin_sms_center.html", ctx)

    @app.get("/admin/sms-center", response_class=HTMLResponse)
    async def admin_sms_center(request: Request, db: Session = Depends(get_db)):
        return _sms_page(request, db, "base_admin.html")

    @app.get("/sub-lco/sms-center", response_class=HTMLResponse)
    async def sublco_sms_center(request: Request, db: Session = Depends(get_db)):
        return _sms_page(request, db, "base_sub_lco.html")

    @app.get("/employee/sms-center", response_class=HTMLResponse)
    async def employee_sms_center(request: Request, db: Session = Depends(get_db)):
        return _sms_page(request, db, "base_employee.html")

    @app.get("/api/sms/recipients")
    async def api_sms_recipients(request: Request, db: Session = Depends(get_db),
                                 q: str = "", limit: int = 200):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        from database import Customer
        from sqlalchemy import or_
        qy = db.query(Customer.customer_id, Customer.customer_name,
                      Customer.customer_phone, Customer.locality, Customer.status)
        qy = _scoped_customer_filter(request, db, qy)
        if q:
            qy = qy.filter(or_(
                Customer.customer_name.ilike(f"%{q}%"),
                Customer.customer_phone.ilike(f"%{q}%"),
                Customer.customer_id.ilike(f"%{q}%"),
            ))
        rows = qy.limit(int(limit)).all()
        return {"success": True, "items": [
            {"customer_id": r[0], "name": r[1], "phone": r[2] or "",
             "locality": r[3] or "", "status": r[4] or ""}
            for r in rows if r[2]
        ]}

    @app.post("/api/sms/send")
    async def api_sms_send(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        if not _company_feat_enabled(db, company_id, "sms_enabled", default_off=True):
            return {"success": False, "message": "SMS not enabled for your company"}
        actor = _audit_actor(request)
        try:
            payload = await request.json()
        except Exception:
            payload = dict(await request.form())
        recipients = payload.get("recipients") or []
        body = (payload.get("body") or "").strip()
        if not body or not recipients:
            return {"success": False, "message": "body & recipients required"}
        # Validate recipients are inside scope
        cust_ids = [r.get("customer_id") for r in recipients if r.get("customer_id")]
        if cust_ids:
            from database import Customer
            qy = _scoped_customer_filter(request, db,
                  db.query(Customer.customer_id, Customer.customer_phone, Customer.customer_name))
            qy = qy.filter(Customer.customer_id.in_(cust_ids))
            allowed = {r[0]: (r[1], r[2]) for r in qy.all()}
        else:
            allowed = {}
        sent = 0; failed = 0; results = []
        for r in recipients:
            cid = r.get("customer_id") or ""
            phone = r.get("phone") or ""
            name = r.get("name") or ""
            if cid and cid in allowed:
                phone = allowed[cid][0] or phone
                name = allowed[cid][1] or name
            elif cid and cid not in allowed:
                results.append({"phone": phone, "ok": False, "error": "Out of scope"})
                failed += 1
                continue
            res = _twilio_sms_send(phone, body)
            _log_sms(db, company_id, actor["actor_type"], actor["actor_id"],
                     phone, name, cid, body, res)
            results.append({"phone": phone, "ok": res.get("success"), "error": res.get("error","")})
            if res.get("success"): sent += 1
            else: failed += 1
        return {"success": True, "sent": sent, "failed": failed, "results": results}

    @app.get("/api/sms/logs")
    async def api_sms_logs(request: Request, db: Session = Depends(get_db),
                           limit: int = 200):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        utype = (request.session.get("user_type") or "").lower()
        actor_id = str(request.session.get("user_id") or "")
        sql = "SELECT id,to_phone,to_name,customer_id,body,status,error,actor_type,actor_id,created_at FROM sms_logs WHERE company_id=:c"
        params = {"c": company_id}
        if utype in ("sub_lco", "employee"):
            sql += " AND actor_id=:a"
            params["a"] = actor_id
        sql += " ORDER BY id DESC LIMIT :l"
        params["l"] = int(limit)
        rows = db.execute(text(sql), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "phone": r[1], "name": r[2], "customer_id": r[3],
             "body": r[4], "status": r[5], "error": r[6],
             "actor_type": r[7], "actor_id": r[8], "created_at": str(r[9])}
            for r in rows
        ]}

    # ───────── #6 SLA endpoints ─────────
    @app.get("/api/admin/sla/run")
    async def api_sla_run(request: Request):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        return {"success": True, "result": _check_and_escalate_slas()}

    @app.post("/api/admin/complaints/{cid}/sla")
    async def api_complaint_sla_set(cid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        sla = int(data.get("sla_minutes") or 0) or None
        if sla is None or sla < 5:
            return {"success": False, "message": "sla_minutes (>=5) required"}
        company_id = request.session.get("company_id")
        db.execute(text("UPDATE complaints SET sla_minutes=:s WHERE id=:i AND company_id=:c"),
                   {"s": sla, "i": cid, "c": company_id})
        db.commit()
        return {"success": True}

    # ───────── #12 Bulk WhatsApp Campaigns ─────────
    @app.get("/api/admin/wa-templates")
    async def api_wa_templates_list(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        rows = db.execute(text("""SELECT id,name,language,category,body_text,is_active,created_at
                                    FROM whatsapp_templates
                                   WHERE (company_id=:c OR company_id IS NULL)
                                   ORDER BY id DESC"""),
                          {"c": company_id}).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "name": r[1], "language": r[2] or "en",
             "category": r[3] or "marketing", "body_text": r[4] or "",
             "is_active": bool(r[5]), "created_at": str(r[6])}
            for r in rows
        ]}

    @app.post("/api/admin/wa-templates")
    async def api_wa_templates_create(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin", "sub_lco"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        name = (data.get("name") or "").strip()
        body = (data.get("body_text") or "").strip()
        if not name or not body:
            return {"success": False, "message": "name & body_text required"}
        actor = str(request.session.get("user_id") or "")
        try:
            db.execute(text("""INSERT INTO whatsapp_templates
                              (name,language,category,body_text,is_active,company_id,created_by,created_at,updated_at)
                              VALUES (:n,:l,:c,:b,1,:co,:a,datetime('now'),datetime('now'))"""),
                       {"n": name, "l": data.get("language") or "en",
                        "c": data.get("category") or "marketing",
                        "b": body, "co": company_id, "a": actor})
            db.commit()
        except Exception as e:
            return {"success": False, "message": f"DB error: {e}"}
        return {"success": True}

    @app.delete("/api/admin/wa-templates/{tid}")
    async def api_wa_templates_delete(tid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        db.execute(text("DELETE FROM whatsapp_templates WHERE id=:i AND (company_id=:c OR company_id IS NULL)"),
                   {"i": tid, "c": company_id})
        db.commit()
        return {"success": True}

    @app.get("/api/admin/wa-campaigns")
    async def api_wa_campaigns_list(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        utype = (request.session.get("user_type") or "").lower()
        actor_id = str(request.session.get("user_id") or "")
        sql = """SELECT c.id, c.name, c.target_type, c.status, c.total_recipients,
                        c.sent_count, c.failed_count, c.created_at, c.body, t.name
                   FROM whatsapp_campaigns c
              LEFT JOIN whatsapp_templates t ON t.id = c.template_id
                  WHERE (c.company_id=:co OR c.company_id IS NULL)"""
        params = {"co": company_id}
        if utype in ("sub_lco", "employee"):
            sql += " AND c.created_by=:a"
            params["a"] = actor_id
        sql += " ORDER BY c.id DESC LIMIT 200"
        rows = db.execute(text(sql), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "name": r[1], "target_type": r[2], "status": r[3],
             "total": r[4] or 0, "sent": r[5] or 0, "failed": r[6] or 0,
             "created_at": str(r[7]), "body": r[8] or "", "template": r[9] or ""}
            for r in rows
        ]}

    @app.post("/api/admin/wa-campaigns")
    async def api_wa_campaigns_create(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        actor = str(request.session.get("user_id") or "")
        utype = (request.session.get("user_type") or "").lower()
        if utype == "employee":
            return {"success": False, "message": "Employees cannot create campaigns"}
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        name = (data.get("name") or "").strip()
        target_type = (data.get("target_type") or "all").strip()
        body = (data.get("body") or "").strip()
        template_id = data.get("template_id") or None
        # __CAMPAIGN_FILTERS__
        service_type_filter = (data.get("service_type") or "").strip()
        locality_filter = (data.get("locality") or "").strip()
        if not name or (not body and not template_id):
            return {"success": False, "message": "name and body|template_id required"}
        # compute audience
        from database import Customer
        qy = _scoped_customer_filter(request, db,
              db.query(Customer.customer_id, Customer.customer_name, Customer.customer_phone, Customer.status))
        if target_type == "active":
            qy = qy.filter(Customer.status == "Active")
        elif target_type == "expired":
            qy = qy.filter(Customer.status == "Expired")
        elif target_type == "selected":
            ids = data.get("customer_ids") or []
            if not ids: return {"success": False, "message": "customer_ids required"}
            qy = qy.filter(Customer.customer_id.in_(ids))
        # Optional secondary filters (compose with target_type)
        if service_type_filter and service_type_filter != "all":
            qy = qy.filter(Customer.service_type == service_type_filter)
        if locality_filter and locality_filter != "all":
            qy = qy.filter(Customer.locality == locality_filter)
        rows = [r for r in qy.all() if r[2]]
        try:
            db.execute(text("""INSERT INTO whatsapp_campaigns
              (name,template_id,target_type,target_ids,status,total_recipients,
               sent_count,failed_count,created_at,created_by,company_id,body,actor_type)
              VALUES (:n,:t,:tt,:ti,'draft',:tot,0,0,datetime('now'),:a,:c,:b,:at)"""),
              {"n": name, "t": template_id,
               "tt": target_type, "ti": json.dumps([r[0] for r in rows]),
               "tot": len(rows), "a": actor, "c": company_id, "b": body, "at": utype})
            db.commit()
            cid = db.execute(text("SELECT last_insert_rowid()")).scalar()
        except Exception as e:
            return {"success": False, "message": f"DB error: {e}"}
        return {"success": True, "campaign_id": cid, "total_recipients": len(rows)}

    # __CAMPAIGN_FILTER_OPTS__
    @app.get("/api/admin/wa-campaigns/filter-options")
    async def api_wa_campaign_filter_opts(
        request: Request, db: Session = Depends(get_db),
    ):
        """Return the distinct service_type + locality values
        for the campaign form's filter dropdowns. Sub-LCO / employee
        scoping is respected via _scoped_customer_filter."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        # __FILTER_OPTS_LOCATIONS_UNION__
        from database import Customer
        company_id = request.session.get("company_id")
        try:
            # Service types + customer-derived localities (scoped).
            base = _scoped_customer_filter(request, db,
                    db.query(Customer.service_type, Customer.locality))
            svc = sorted({(r[0] or "").strip() for r in base.all()
                          if (r[0] or "").strip()})
            loc = set((r[1] or "").strip() for r in base.all()
                      if (r[1] or "").strip())
            # UNION with the master locations table so newly added
            # localities show up even before any customer is on them.
            try:
                rows = db.execute(text(
                    "SELECT name FROM locations WHERE company_id = :c "
                    "  AND (status IS NULL OR status='Active') "
                    "  AND name IS NOT NULL AND name != ''"
                ), {"c": company_id}).fetchall()
                for r in rows:
                    nm = (r[0] or "").strip()
                    if nm:
                        loc.add(nm)
            except Exception:
                pass
            loc = sorted(loc)
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}
        return {"success": True, "service_types": svc, "localities": loc}

    # __CAMPAIGN_SEED_DEFAULTS__
    @app.post("/api/admin/wa-templates/seed-defaults")
    async def api_wa_seed_defaults(
        request: Request, db: Session = Depends(get_db),
    ):
        """Idempotently seed 5 starter templates for the current
        company if they have zero templates. Safe to call repeatedly."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        cnt = db.execute(text(
            "SELECT COUNT(*) FROM whatsapp_templates WHERE company_id=:c"
        ), {"c": company_id}).scalar() or 0
        if int(cnt) > 0:
            return {"success": True, "seeded": 0, "already": int(cnt)}
        # __NUMBERED_PLACEHOLDERS__
        defaults = [
            ("Diwali Offer", "Dear {{1}}, wishing you a Happy Diwali! "
             "Get 1 month free on annual plans this Diwali. "
             "Reply YES to renew, or call us for details. - Team {{2}}"),
            ("Service Maintenance", "Hi {{1}}, our network maintenance is "
             "scheduled today {{3}} from {{4}}. Expect short outages of up "
             "to 2 hours. Sorry for the inconvenience. - Team {{2}}"),
            ("Fiber Cut Notice", "Hi {{1}}, a fiber cut has affected your "
             "area ({{5}}). Our team is on-site and ETA for restoration is "
             "{{4}}. Apologies for the disruption. - Team {{2}}"),
            ("Server Downtime", "Hi {{1}}, our core server is undergoing "
             "emergency maintenance. Service will be restored within {{4}}. "
             "Please bear with us. - Team {{2}}"),
            ("Slow Speed Resolution", "Hi {{1}}, we noticed slow speed "
             "complaints from your area. Engineers are optimising the link. "
             "Please reboot your router once. If issue persists, reply HELP. "
             "- Team {{2}}"),
        ]
        seeded = 0
        actor = str(request.session.get("user_id") or "")
        for nm, bd in defaults:
            try:
                db.execute(text(
                    "INSERT INTO whatsapp_templates "
                    "(name, body_text, created_at, created_by, company_id) "
                    "VALUES (:n, :b, datetime('now'), :u, :c)"),
                    {"n": nm, "b": bd, "u": actor, "c": company_id})
                seeded += 1
            except Exception:
                pass
        db.commit()
        return {"success": True, "seeded": seeded}

    @app.post("/api/admin/wa-campaigns/{cid}/send")
    async def api_wa_campaign_send(cid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin", "sub_lco"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        if not _company_feat_enabled(db, company_id, "whatsapp_enabled", default_off=False):
            return {"success": False, "message": "WhatsApp not enabled for your company"}
        row = db.execute(text("""SELECT name,template_id,target_ids,status,body
                                   FROM whatsapp_campaigns
                                  WHERE id=:i AND (company_id=:c OR company_id IS NULL)"""),
                         {"i": cid, "c": company_id}).fetchone()
        if not row:
            return {"success": False, "message": "Not found"}
        if row[3] in ("sending", "completed"):
            return {"success": False, "message": f"Already {row[3]}"}
        try:
            ids = json.loads(row[2] or "[]")
        except Exception:
            ids = []
        body = row[4] or ""
        if row[1]:
            tpl = db.execute(text("SELECT body_text FROM whatsapp_templates WHERE id=:i"),
                             {"i": row[1]}).fetchone()
            if tpl: body = tpl[0]
        if not body or not ids:
            return {"success": False, "message": "Empty campaign"}
        # mark started
        db.execute(text("UPDATE whatsapp_campaigns SET status='sending', started_at=datetime('now') WHERE id=:i"),
                   {"i": cid})
        db.commit()
        # __MSG91_CAMPAIGN_SEND__  Route through MSG91 campaign_broadcast
        # template (3 vars: customer_name, body_text, company_name).
        # The admin must approve a Meta template named
        # 'campaign_broadcast' with body:  Hi {{1}}, {{2}} - {{3}}
        from database import Customer, Company
        from msg91_whatsapp import _send_template as _msg91_send, normalise_phone
        comp_row = db.query(Company).filter(
            Company.company_id == company_id).first()
        cname = (comp_row.company_name if comp_row else None) or "AUTO ISP BILLING"
        sent = 0; failed = 0
        for cust_id in ids:
            c = db.query(Customer.customer_name, Customer.customer_phone,
                          Customer.locality, Customer.plan_id, Customer.end_date)\
                  .filter(Customer.customer_id == cust_id,
                          Customer.company_id == company_id).first()
            if not c or not c[1]:
                failed += 1; continue
            phone = normalise_phone(c[1])
            if not phone:
                failed += 1; continue
            cust_name = c[0] or "Customer"
            # Substitute {{1}} / {{2}} / etc. in the saved body.
            personalised = (body or "")
            personalised = personalised.replace("{{1}}", cust_name)
            personalised = personalised.replace("{{2}}", cname)
            personalised = personalised.replace("{{5}}", c[2] or "")
            personalised = personalised.replace("{{name}}", cust_name)
            personalised = personalised.replace("{{company_name}}", cname)
            personalised = personalised.replace("{{locality}}", c[2] or "")
            # Send via the MSG91 campaign_broadcast template.
            res = _msg91_send(
                "campaign_broadcast",
                [cust_name, personalised, cname],
                phone, company_id=company_id,
            )
            if res.get("success"): sent += 1
            else: failed += 1
        db.execute(text("""UPDATE whatsapp_campaigns
                              SET status='completed', sent_count=:s, failed_count=:f,
                                  completed_at=datetime('now')
                            WHERE id=:i"""),
                   {"s": sent, "f": failed, "i": cid})
        db.commit()
        return {"success": True, "sent": sent, "failed": failed}

    # ───────── SMS campaigns (mirror) ─────────
    @app.get("/api/admin/sms-campaigns")
    async def api_sms_campaigns_list(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        utype = (request.session.get("user_type") or "").lower()
        actor_id = str(request.session.get("user_id") or "")
        sql = "SELECT id,name,target_type,status,total_recipients,sent_count,failed_count,created_at,body FROM sms_campaigns WHERE company_id=:c"
        params = {"c": company_id}
        if utype in ("sub_lco", "employee"):
            sql += " AND created_by=:a"; params["a"] = actor_id
        sql += " ORDER BY id DESC LIMIT 200"
        rows = db.execute(text(sql), params).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "name": r[1], "target_type": r[2], "status": r[3],
             "total": r[4], "sent": r[5], "failed": r[6],
             "created_at": str(r[7]), "body": r[8]}
            for r in rows]}

    @app.post("/api/admin/sms-campaigns")
    async def api_sms_campaigns_create(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        actor = str(request.session.get("user_id") or "")
        utype = (request.session.get("user_type") or "").lower()
        if utype == "employee":
            return {"success": False, "message": "Employees cannot create campaigns"}
        try:
            data = await request.json()
        except Exception:
            data = dict(await request.form())
        name = (data.get("name") or "").strip()
        body = (data.get("body") or "").strip()
        target_type = (data.get("target_type") or "all").strip()
        if not name or not body:
            return {"success": False, "message": "name, body required"}
        from database import Customer
        qy = _scoped_customer_filter(request, db,
              db.query(Customer.customer_id, Customer.customer_phone, Customer.status))
        if target_type == "active":
            qy = qy.filter(Customer.status == "Active")
        elif target_type == "expired":
            qy = qy.filter(Customer.status == "Expired")
        elif target_type == "selected":
            ids = data.get("customer_ids") or []
            if not ids: return {"success": False, "message": "customer_ids required"}
            qy = qy.filter(Customer.customer_id.in_(ids))
        rows = [r for r in qy.all() if r[1]]
        db.execute(text("""INSERT INTO sms_campaigns
            (company_id,name,body,target_type,target_ids,status,total_recipients,created_by,created_at)
            VALUES (:c,:n,:b,:tt,:ti,'draft',:tot,:a,datetime('now'))"""),
            {"c": company_id, "n": name, "b": body, "tt": target_type,
             "ti": json.dumps([r[0] for r in rows]),
             "tot": len(rows), "a": actor})
        db.commit()
        cid = db.execute(text("SELECT last_insert_rowid()")).scalar()
        return {"success": True, "campaign_id": cid, "total_recipients": len(rows)}

    @app.post("/api/admin/sms-campaigns/{cid}/send")
    async def api_sms_campaign_send(cid: int, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin", "sub_lco"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        if not _company_feat_enabled(db, company_id, "sms_enabled", default_off=True):
            return {"success": False, "message": "SMS not enabled for your company"}
        actor = _audit_actor(request)
        row = db.execute(text("""SELECT name,target_ids,status,body FROM sms_campaigns
                                  WHERE id=:i AND company_id=:c"""),
                         {"i": cid, "c": company_id}).fetchone()
        if not row:
            return {"success": False, "message": "Not found"}
        if row[2] in ("sending", "completed"):
            return {"success": False, "message": f"Already {row[2]}"}
        try:
            ids = json.loads(row[1] or "[]")
        except Exception: ids = []
        body = row[3] or ""
        if not body or not ids:
            return {"success": False, "message": "Empty campaign"}
        db.execute(text("UPDATE sms_campaigns SET status='sending', started_at=datetime('now') WHERE id=:i"),
                   {"i": cid}); db.commit()
        from database import Customer
        sent = 0; failed = 0
        for cust_id in ids:
            c = db.query(Customer.customer_name, Customer.customer_phone)\
                  .filter(Customer.customer_id == cust_id,
                          Customer.company_id == company_id).first()
            if not c or not c[1]:
                failed += 1; continue
            personalised = body.replace("{{name}}", c[0] or "Customer")
            res = _twilio_sms_send(c[1], personalised)
            _log_sms(db, company_id, actor["actor_type"], actor["actor_id"],
                     c[1], c[0], cust_id, personalised, res, campaign_id=cid)
            if res.get("success"): sent += 1
            else: failed += 1
        db.execute(text("""UPDATE sms_campaigns SET status='completed',
                                  sent_count=:s, failed_count=:f, completed_at=datetime('now')
                            WHERE id=:i"""),
                   {"s": sent, "f": failed, "i": cid}); db.commit()
        return {"success": True, "sent": sent, "failed": failed}

    # ───────── #14 IP Pool utilization ─────────
    @app.get("/api/admin/ip-pools/utilization")
    async def api_ip_pool_util(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        # IpPool table has start_ip / end_ip; allocations are in nat_one_to_one_pairs,
        # nat_configs and customers with static_ip
        try:
            pools = db.execute(text("""SELECT id, name, start_ip, end_ip, gateway, type, dns
                                         FROM ip_pools WHERE company_id=:c ORDER BY id"""),
                                {"c": company_id}).fetchall()
        except Exception:
            pools = []
        def _ip2int(ip):
            try:
                p = [int(x) for x in str(ip).split(".")]
                return (p[0]<<24)|(p[1]<<16)|(p[2]<<8)|p[3]
            except Exception: return 0
        out = []
        for p in pools:
            s = _ip2int(p[2]); e = _ip2int(p[3])
            cap = max(0, e - s + 1)
            used = db.execute(text("""SELECT COUNT(1) FROM customers
                                       WHERE company_id=:c AND static_ip IS NOT NULL
                                         AND static_ip != ''
                                         AND CAST(SUBSTR(static_ip,1,INSTR(static_ip,'.')-1) AS INT) >= 0"""),
                              {"c": company_id}).scalar() or 0
            # cheap: count any static_ip lying inside the start/end (string comparison fallback)
            try:
                used = db.execute(text("""SELECT COUNT(1) FROM customers
                                           WHERE company_id=:c AND static_ip != ''
                                             AND static_ip IS NOT NULL"""),
                                  {"c": company_id}).scalar() or 0
            except Exception:
                pass
            out.append({"id": p[0], "name": p[1], "start": p[2], "end": p[3],
                        "gateway": p[4], "type": p[5], "capacity": cap,
                        "used": min(used, cap), "free": max(cap - used, 0)})
        return {"success": True, "pools": out}

    print("[phase1_features] registered: SMS Center, Ticket SLA, Bulk WhatsApp, IP-Pool Util, Backups")
