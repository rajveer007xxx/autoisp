"""Superadmin Feature Flags module — gate per-company SMS/WhatsApp
and other Phase 2/3 features."""
from fastapi import Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text


def _is_sa(request):
    return (request.session.get("user_type") or "").lower() == "superadmin"


_FLAGS = ("sms_enabled", "whatsapp_enabled", "dns_filter_enabled",
          "outage_detector_enabled", "self_upgrade_enabled",
          "referrals_enabled", "lead_crm_enabled", "multilang_enabled")


def register(app, templates, get_db):

    @app.get("/superadmin/feature-flags", response_class=HTMLResponse)
    async def sa_features_page(request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return HTMLResponse("<h3>Forbidden</h3>", 403)
        return templates.TemplateResponse("superadmin_feature_flags.html",
                                          {"request": request,
                                           "active_page": "feature_flags"})

    @app.get("/api/superadmin/feature-flags")
    async def api_sa_flags_list(request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return {"success": False, "message": "Forbidden"}
        admins = db.execute(text("""SELECT a.company_id, a.admin_name, a.admin_id
                                       FROM admins a
                                   ORDER BY a.admin_name""")).fetchall()
        flags = db.execute(text(f"""SELECT company_id, {",".join(_FLAGS)}
                                      FROM company_feature_flags""")).fetchall()
        flag_map = {r[0]: dict(zip(_FLAGS, r[1:])) for r in flags}
        items = []
        for a in admins:
            cid = a[0]
            f = flag_map.get(cid, {})
            row = {"company_id": cid,
                   "company_name": a[1] or cid,
                   "admin_username": a[2] or ""}
            for k in _FLAGS:
                # SMS+WA default off; rest default on
                default = False if k in ("sms_enabled",) else True
                row[k] = bool(f.get(k, default))
            items.append(row)
        return {"success": True, "items": items}

    @app.post("/api/superadmin/feature-flags/{company_id}")
    async def api_sa_flags_update(company_id: str, request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return {"success": False, "message": "Forbidden"}
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        actor = str(request.session.get("user_id") or "superadmin")
        # Validate keys
        clean = {k: 1 if data.get(k) else 0 for k in _FLAGS if k in data}
        if not clean:
            return {"success": False, "message": "No flags supplied"}
        # Upsert
        existing = db.execute(text("SELECT id FROM company_feature_flags WHERE company_id=:c"),
                              {"c": company_id}).fetchone()
        if existing:
            sets = ", ".join([f"{k}=:{k}" for k in clean.keys()])
            params = {**clean, "c": company_id, "u": actor}
            db.execute(text(f"""UPDATE company_feature_flags
                                  SET {sets}, updated_at=datetime('now'), updated_by=:u
                                WHERE company_id=:c"""), params)
        else:
            cols = ",".join(clean.keys())
            placeholders = ",".join([f":{k}" for k in clean.keys()])
            params = {**clean, "c": company_id, "u": actor}
            db.execute(text(f"""INSERT INTO company_feature_flags
                                (company_id, {cols}, updated_at, updated_by)
                                VALUES (:c, {placeholders}, datetime('now'), :u)"""), params)
        db.commit()
        return {"success": True}

    print("[sa_company_features] registered: /superadmin/feature-flags")
