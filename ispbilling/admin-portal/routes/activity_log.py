"""routes/activity_log.py  —  s36b9 refactor.

Extracted from main.py to demonstrate the router-module pattern.
The `log_admin_activity()` helper stays in main.py (it's called from
dozens of endpoints and we don't want a dependency cycle).
"""
from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

router = APIRouter(tags=["activity-log"])


def register(app, *, templates, require_admin, get_db, get_admin_context):
    """Bind this router to the FastAPI app. Dependencies are injected to
    avoid importing from main.py (no circular deps)."""

    @router.get("/admin/activity-log", response_class=HTMLResponse)
    async def admin_activity_log_page(request: Request, db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth:
            return auth
        ctx = get_admin_context(request, db, "activity_log")
        return templates.TemplateResponse("admin_activity_log.html", ctx)

    @router.get("/api/activity-log/list")
    async def api_activity_log_list(request: Request,
                                    limit: int = 200,
                                    target_type: str = "",
                                    db: Session = Depends(get_db)):
        auth = require_admin(request)
        if auth:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        from database import AdminActivityLog
        company_id = request.session.get("company_id", "N/A")
        q = db.query(AdminActivityLog).filter(AdminActivityLog.company_id == company_id)
        if target_type:
            q = q.filter(AdminActivityLog.target_type == target_type)
        rows = q.order_by(AdminActivityLog.id.desc()).limit(max(1, min(limit, 1000))).all()
        return JSONResponse({"ok": True, "rows": [{
            "id": r.id, "actor_name": r.actor_name, "actor_id": r.actor_id,
            "actor_type": r.actor_type, "action": r.action,
            "target_type": r.target_type, "target_id": r.target_id,
            "summary": r.summary, "ip": r.ip_address,
            "at": str(r.created_at) if r.created_at else "",
        } for r in rows]})

    app.include_router(router)
