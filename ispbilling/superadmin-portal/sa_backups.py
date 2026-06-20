"""Superadmin DB Backups module (Phase 1 #15)."""
import os, gzip, hashlib, shutil, sqlite3
from datetime import datetime
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

_DB_PATH = "/var/lib/autoispbilling/autoispbilling.db"
_BACKUP_DIR = "/var/lib/autoispbilling/backups"
os.makedirs(_BACKUP_DIR, exist_ok=True)


def create_backup(triggered_by: str = "manual", actor: str = "system"):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    raw = os.path.join(_BACKUP_DIR, f"db-{ts}.sqlite")
    gz  = raw + ".gz"
    try:
        src = sqlite3.connect(_DB_PATH)
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
        eng = sqlite3.connect(_DB_PATH)
        eng.execute("""INSERT INTO db_backups(filename, size_bytes, sha256, created_by, trigger)
                       VALUES (?, ?, ?, ?, ?)""",
                    (os.path.basename(gz), size, sha, actor, triggered_by))
        eng.commit(); eng.close()
        files = sorted([f for f in os.listdir(_BACKUP_DIR) if f.endswith(".gz")])
        for old in files[:-30]:
            try: os.remove(os.path.join(_BACKUP_DIR, old))
            except Exception: pass
        return {"success": True, "filename": os.path.basename(gz), "size": size}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _is_sa(request):
    return (request.session.get("user_type") or "").lower() == "superadmin"


def register(app, templates, get_db):
    @app.get("/superadmin/backups", response_class=HTMLResponse)
    async def sa_backups_page(request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return HTMLResponse("<h3>Forbidden — Superadmin only</h3>", 403)
        return templates.TemplateResponse("superadmin_backups.html",
                                          {"request": request, "active_page": "backups"})

    @app.get("/api/superadmin/backups")
    async def api_bk_list(request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return {"success": False, "message": "Forbidden"}
        rows = db.execute(text("""SELECT id, filename, size_bytes, sha256,
                                          created_at, created_by, trigger
                                     FROM db_backups ORDER BY id DESC LIMIT 100""")).fetchall()
        return {"success": True, "items": [
            {"id": r[0], "filename": r[1], "size": r[2],
             "sha256": (r[3] or "")[:16],
             "created_at": str(r[4]), "by": r[5] or "system",
             "trigger": r[6] or "auto"}
            for r in rows]}

    @app.post("/api/superadmin/backups/run")
    async def api_bk_run(request: Request):
        if not _is_sa(request):
            return {"success": False, "message": "Forbidden"}
        actor = str(request.session.get("user_id") or "superadmin")
        return create_backup(triggered_by="manual", actor=actor)

    @app.get("/api/superadmin/backups/{bid}/download")
    async def api_bk_download(bid: int, request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            raise HTTPException(403, "Forbidden")
        row = db.execute(text("SELECT filename FROM db_backups WHERE id=:i"),
                         {"i": bid}).fetchone()
        if not row:
            raise HTTPException(404, "Not found")
        path = os.path.join(_BACKUP_DIR, row[0])
        if not os.path.isfile(path):
            raise HTTPException(404, "File missing on disk")
        return FileResponse(path, filename=row[0], media_type="application/gzip")

    @app.delete("/api/superadmin/backups/{bid}")
    async def api_bk_delete(bid: int, request: Request, db: Session = Depends(get_db)):
        if not _is_sa(request):
            return {"success": False, "message": "Forbidden"}
        row = db.execute(text("SELECT filename FROM db_backups WHERE id=:i"),
                         {"i": bid}).fetchone()
        if row:
            try: os.remove(os.path.join(_BACKUP_DIR, row[0]))
            except Exception: pass
            db.execute(text("DELETE FROM db_backups WHERE id=:i"), {"i": bid})
            db.commit()
        return {"success": True}

    print("[sa_backups] registered: /superadmin/backups + /api/superadmin/backups/*")
