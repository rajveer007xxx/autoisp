"""
Geofencing — Round 5b.

Companies define circular geofences (lat, lng, radius_m). When an employee
posts their GPS via the mobile API, we evaluate enter / exit transitions
against each active geofence and emit alerts.

Owns:
  • SQLite tables: geofences, geofence_events
  • CRUD APIs at /api/admin/geofences
  • Live alerts feed at /api/admin/geofence-events
  • Pure-python `evaluate_position(...)` used by the mobile API hook.

Tenant isolation: every query is scoped by request.session["company_id"].
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import radians, sin, cos, sqrt, atan2
from typing import Optional, List

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from database import engine

router = APIRouter()


# ---------- Schema ----------

def _ensure_tables() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS geofences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id      TEXT NOT NULL,
                name            TEXT NOT NULL,
                latitude        REAL NOT NULL,
                longitude       REAL NOT NULL,
                radius_m        REAL NOT NULL DEFAULT 200,
                color           TEXT DEFAULT '#0ea5e9',
                is_active       INTEGER DEFAULT 1,
                notify_on_enter INTEGER DEFAULT 1,
                notify_on_exit  INTEGER DEFAULT 1,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deleted_at      TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_geofences_company "
            "ON geofences(company_id, is_active)"
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS geofence_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id   TEXT NOT NULL,
                geofence_id  INTEGER NOT NULL,
                employee_id  INTEGER NOT NULL,
                event_type   TEXT NOT NULL,
                latitude     REAL,
                longitude    REAL,
                recorded_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_gevents_company_at "
            "ON geofence_events(company_id, recorded_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_gevents_emp_geo "
            "ON geofence_events(employee_id, geofence_id, recorded_at)"
        )


_ensure_tables()


# ---------- Helpers ----------

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371000.0  # earth radius in meters
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _require_admin(request: Request):
    sess = request.session
    cid = sess.get("company_id")
    ut = (sess.get("user_type") or "").lower()
    if not cid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if ut not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return cid


# ---------- Pydantic ----------

class GeofenceIn(BaseModel):
    name: str
    latitude: float
    longitude: float
    radius_m: float = 200
    color: Optional[str] = "#0ea5e9"
    is_active: bool = True
    notify_on_enter: bool = True
    notify_on_exit: bool = True


# ---------- CRUD ----------

@router.get("/api/admin/geofences")
async def list_geofences(request: Request, only_active: bool = False):
    cid = _require_admin(request)
    sql = ("SELECT id, name, latitude, longitude, radius_m, color, "
           "is_active, notify_on_enter, notify_on_exit, created_at "
           "FROM geofences WHERE company_id=:cid AND deleted_at IS NULL")
    if only_active:
        sql += " AND is_active=1"
    sql += " ORDER BY id DESC"
    with engine.begin() as conn:
        rows = conn.execute(text(sql), {"cid": cid}).fetchall()
    items = [{
        "id": r[0], "name": r[1] or "",
        "latitude": float(r[2] or 0), "longitude": float(r[3] or 0),
        "radius_m": float(r[4] or 0), "color": r[5] or "#0ea5e9",
        "is_active": bool(r[6]), "notify_on_enter": bool(r[7]),
        "notify_on_exit": bool(r[8]),
        "created_at": str(r[9]) if r[9] else "",
    } for r in rows]
    return {"items": items, "count": len(items)}


@router.post("/api/admin/geofences")
async def create_geofence(request: Request, payload: GeofenceIn):
    cid = _require_admin(request)
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="Name required")
    if not (-90 <= payload.latitude <= 90 and -180 <= payload.longitude <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    if payload.radius_m <= 0 or payload.radius_m > 50000:
        raise HTTPException(status_code=400, detail="Radius must be 1-50000 m")
    with engine.begin() as conn:
        r = conn.execute(text(
            "INSERT INTO geofences (company_id, name, latitude, longitude, radius_m, "
            " color, is_active, notify_on_enter, notify_on_exit) "
            "VALUES (:c,:n,:la,:ln,:r,:co,:a,:e,:x)"
        ), {"c": cid, "n": payload.name.strip(), "la": payload.latitude,
            "ln": payload.longitude, "r": payload.radius_m,
            "co": payload.color or "#0ea5e9",
            "a": 1 if payload.is_active else 0,
            "e": 1 if payload.notify_on_enter else 0,
            "x": 1 if payload.notify_on_exit else 0})
    return {"success": True, "id": r.lastrowid}


@router.put("/api/admin/geofences/{gid}")
async def update_geofence(gid: int, request: Request, payload: GeofenceIn):
    cid = _require_admin(request)
    with engine.begin() as conn:
        existing = conn.execute(text(
            "SELECT id FROM geofences WHERE id=:i AND company_id=:c "
            "AND deleted_at IS NULL"), {"i": gid, "c": cid}).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Geofence not found")
        conn.execute(text(
            "UPDATE geofences SET name=:n, latitude=:la, longitude=:ln, "
            "radius_m=:r, color=:co, is_active=:a, notify_on_enter=:e, "
            "notify_on_exit=:x WHERE id=:i AND company_id=:c"
        ), {"i": gid, "c": cid, "n": payload.name.strip(),
            "la": payload.latitude, "ln": payload.longitude,
            "r": payload.radius_m, "co": payload.color or "#0ea5e9",
            "a": 1 if payload.is_active else 0,
            "e": 1 if payload.notify_on_enter else 0,
            "x": 1 if payload.notify_on_exit else 0})
    return {"success": True}


@router.delete("/api/admin/geofences/{gid}")
async def delete_geofence(gid: int, request: Request):
    cid = _require_admin(request)
    with engine.begin() as conn:
        r = conn.execute(text(
            "UPDATE geofences SET deleted_at=CURRENT_TIMESTAMP "
            "WHERE id=:i AND company_id=:c AND deleted_at IS NULL"
        ), {"i": gid, "c": cid})
        if r.rowcount == 0:
            raise HTTPException(status_code=404, detail="Geofence not found")
    return {"success": True}


# ---------- Alerts feed ----------

@router.get("/api/admin/geofence-events")
async def list_events(request: Request, since: Optional[str] = None, limit: int = 100):
    cid = _require_admin(request)
    limit = max(1, min(int(limit or 100), 500))
    sql = (
        "SELECT ge.id, ge.event_type, ge.recorded_at, ge.latitude, ge.longitude, "
        "       gf.name, gf.color, gf.id, "
        "       e.id, e.employee_code, e.employee_name "
        "FROM geofence_events ge "
        "JOIN geofences gf ON gf.id = ge.geofence_id "
        "LEFT JOIN employees e ON e.id = ge.employee_id "
        "WHERE ge.company_id=:c"
    )
    params = {"c": cid}
    if since:
        sql += " AND ge.recorded_at > :since"
        params["since"] = since
    sql += " ORDER BY ge.id DESC LIMIT :lim"
    params["lim"] = limit
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    items = [{
        "id": r[0], "event_type": r[1] or "",
        "recorded_at": str(r[2]) if r[2] else "",
        "latitude": float(r[3] or 0), "longitude": float(r[4] or 0),
        "geofence_name": r[5] or "", "geofence_color": r[6] or "#0ea5e9",
        "geofence_id": r[7],
        "employee_id": r[8], "employee_code": r[9] or "",
        "employee_name": r[10] or "",
    } for r in rows]
    return {"items": items, "count": len(items)}


# ---------- Evaluation hook (called by mobile API after a location ingest) ----------

def evaluate_position(company_id: str, employee_id: int,
                      latitude: float, longitude: float) -> int:
    """Compare the new (lat,lng) against every active geofence for the
    company. For each fence, the employee was previously inside if the LAST
    event recorded for them was 'enter'. Generates 'enter' or 'exit' rows
    only on transitions. Returns the number of new event rows written."""
    if not company_id or employee_id is None:
        return 0
    written = 0
    with engine.begin() as conn:
        fences = conn.execute(text(
            "SELECT id, latitude, longitude, radius_m, "
            "       notify_on_enter, notify_on_exit "
            "FROM geofences WHERE company_id=:c AND is_active=1 "
            "AND deleted_at IS NULL"),
            {"c": company_id}).fetchall()
        for f in fences:
            gid, fla, fln, frad, n_en, n_ex = f
            d = _haversine_m(latitude, longitude, float(fla), float(fln))
            inside_now = d <= float(frad)
            last = conn.execute(text(
                "SELECT event_type FROM geofence_events "
                "WHERE company_id=:c AND geofence_id=:g AND employee_id=:e "
                "ORDER BY id DESC LIMIT 1"),
                {"c": company_id, "g": int(gid), "e": int(employee_id)}).fetchone()
            was_inside = bool(last and (last[0] or "").lower() == "enter")
            event = None
            if inside_now and not was_inside and int(n_en or 0):
                event = "enter"
            elif (not inside_now) and was_inside and int(n_ex or 0):
                event = "exit"
            if event:
                conn.execute(text(
                    "INSERT INTO geofence_events "
                    "(company_id, geofence_id, employee_id, event_type, "
                    " latitude, longitude) VALUES (:c,:g,:e,:t,:la,:ln)"),
                    {"c": company_id, "g": int(gid), "e": int(employee_id),
                     "t": event, "la": latitude, "ln": longitude})
                written += 1
    return written
