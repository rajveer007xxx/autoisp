"""s56V — MDU / Building Hierarchy (Phase B FiberMap-parity gap closer)

Adds building → floor → unit → customer mapping on top of the existing
`network_map_routes.py` GIS layer. Reuses the same Leaflet map page —
units inherit lat/lng from their parent building.

Tables (idempotent):
  * `mdu_buildings`   — top-level building (lat/lng, address, owner type)
  * `mdu_floors`      — building → floor (number, label)
  * `mdu_units`       — floor → unit (e.g. apartment 3B), pinned to a network_hardware ONU optionally
  * `mdu_unit_customers` — unit → customer link (a unit can have a current + history of subscribers)

REST:
  GET    /api/admin/mdu/buildings
  POST   /api/admin/mdu/buildings
  PATCH  /api/admin/mdu/buildings/{id}
  DELETE /api/admin/mdu/buildings/{id}
  GET    /api/admin/mdu/buildings/{id}/tree
  POST   /api/admin/mdu/buildings/{id}/floors
  PATCH  /api/admin/mdu/floors/{id}
  DELETE /api/admin/mdu/floors/{id}
  POST   /api/admin/mdu/floors/{id}/units
  PATCH  /api/admin/mdu/units/{id}
  DELETE /api/admin/mdu/units/{id}
  POST   /api/admin/mdu/units/{id}/link-customer
  DELETE /api/admin/mdu/units/{id}/link-customer

UI:
  /admin/mdu             list of buildings + tree view per building
"""
from __future__ import annotations

import sqlite3
from db_compat import get_raw_conn as _compat_conn  # __s56Z_compat__
from typing import Optional, List, Any
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from olt_routes import _require_scope, _portal_context, templates  # type: ignore

DB_PATH = "/var/lib/autoispbilling/autoispbilling.db"
router = APIRouter()


def _ensure_schema() -> None:
    with _compat_conn(timeout=10) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS mdu_buildings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  TEXT NOT NULL,
            name        TEXT NOT NULL,
            address     TEXT,
            lat         REAL,
            lng         REAL,
            owner_type  TEXT DEFAULT 'residential', -- residential|commercial|mixed
            floors_count INTEGER DEFAULT 1,
            units_per_floor INTEGER DEFAULT 1,
            ref_hw_id   INTEGER,                    -- optional link to network_hardware pin
            notes       TEXT,
            created_by  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mdu_b_company ON mdu_buildings(company_id);

        CREATE TABLE IF NOT EXISTS mdu_floors (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id   TEXT NOT NULL,
            building_id  INTEGER NOT NULL,
            floor_number INTEGER NOT NULL,
            label        TEXT,                      -- "G", "1", "Mezzanine"
            UNIQUE(building_id, floor_number)
        );
        CREATE INDEX IF NOT EXISTS idx_mdu_f_building ON mdu_floors(building_id);

        CREATE TABLE IF NOT EXISTS mdu_units (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id   TEXT NOT NULL,
            floor_id     INTEGER NOT NULL,
            unit_label   TEXT NOT NULL,             -- "3B", "Shop-12"
            unit_type    TEXT DEFAULT 'apartment',  -- apartment|shop|office|villa
            ref_onu_id   INTEGER,                   -- live ONU id (if installed)
            ref_hw_id    INTEGER,                   -- network_hardware pin
            notes        TEXT,
            UNIQUE(floor_id, unit_label)
        );
        CREATE INDEX IF NOT EXISTS idx_mdu_u_floor ON mdu_units(floor_id);

        CREATE TABLE IF NOT EXISTS mdu_unit_customers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  TEXT NOT NULL,
            unit_id     INTEGER NOT NULL,
            customer_id TEXT NOT NULL,
            active      INTEGER DEFAULT 1,
            linked_at   TEXT DEFAULT (datetime('now')),
            ended_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mdu_uc_unit ON mdu_unit_customers(unit_id);
        CREATE INDEX IF NOT EXISTS idx_mdu_uc_cust ON mdu_unit_customers(customer_id);
        """)
_ensure_schema()


# ──────────────────────────────── Pydantic ────────────────────────────────
class BuildingIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    owner_type: Optional[str] = "residential"
    floors_count: Optional[int] = 1
    units_per_floor: Optional[int] = 1
    ref_hw_id: Optional[int] = None
    notes: Optional[str] = None

class FloorIn(BaseModel):
    floor_number: int
    label: Optional[str] = None

class UnitIn(BaseModel):
    unit_label: str = Field(..., min_length=1, max_length=40)
    unit_type: Optional[str] = "apartment"
    ref_onu_id: Optional[int] = None
    ref_hw_id: Optional[int] = None
    notes: Optional[str] = None

class LinkCustomerIn(BaseModel):
    customer_id: str = Field(..., min_length=1)


# ──────────────────────────────── Helpers ────────────────────────────────
def _own_building(con, cid: str, bid: int) -> dict:
    r = con.execute(
        "SELECT * FROM mdu_buildings WHERE id=? AND company_id=?",
        (bid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Building not found")
    return dict(r)

def _own_floor(con, cid: str, fid: int) -> dict:
    r = con.execute(
        "SELECT * FROM mdu_floors WHERE id=? AND company_id=?",
        (fid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Floor not found")
    return dict(r)

def _own_unit(con, cid: str, uid: int) -> dict:
    r = con.execute(
        "SELECT * FROM mdu_units WHERE id=? AND company_id=?",
        (uid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Unit not found")
    return dict(r)


# ──────────────────────────────── Endpoints ────────────────────────────────
@router.get("/api/admin/mdu/buildings")
def list_buildings(request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT b.*, "
            "(SELECT COUNT(*) FROM mdu_floors WHERE building_id=b.id) AS floors_n, "
            "(SELECT COUNT(*) FROM mdu_units u JOIN mdu_floors f ON f.id=u.floor_id "
            " WHERE f.building_id=b.id) AS units_n, "
            "(SELECT COUNT(*) FROM mdu_unit_customers uc JOIN mdu_units u "
            " ON u.id=uc.unit_id JOIN mdu_floors f ON f.id=u.floor_id "
            " WHERE f.building_id=b.id AND uc.active=1) AS active_subs "
            "FROM mdu_buildings b WHERE b.company_id=? "
            "ORDER BY b.name COLLATE NOCASE",
            (sc["company_id"],)).fetchall()
    return {"ok": True, "buildings": [dict(r) for r in rows]}


@router.post("/api/admin/mdu/buildings")
def create_building(body: BuildingIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        cur = con.execute(
            "INSERT INTO mdu_buildings (company_id, name, address, lat, lng, owner_type, "
            "floors_count, units_per_floor, ref_hw_id, notes, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sc["company_id"], body.name, body.address, body.lat, body.lng,
             body.owner_type, body.floors_count or 1,
             body.units_per_floor or 1, body.ref_hw_id, body.notes, sc["actor"]))
        bid = cur.lastrowid
        # Auto-create floors + units based on counts (operator can edit later).
        for fn in range(1, (body.floors_count or 1) + 1):
            con.execute(
                "INSERT INTO mdu_floors (company_id, building_id, floor_number, label) "
                "VALUES (?,?,?,?)",
                (sc["company_id"], bid, fn, str(fn)))
            fid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            for u in range(1, (body.units_per_floor or 1) + 1):
                con.execute(
                    "INSERT INTO mdu_units (company_id, floor_id, unit_label, unit_type) "
                    "VALUES (?,?,?,?)",
                    (sc["company_id"], fid, f"{fn}-{u}", "apartment"))
    return {"ok": True, "id": bid}


@router.patch("/api/admin/mdu/buildings/{bid}")
def update_building(bid: int, body: BuildingIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_building(con, sc["company_id"], bid)
        con.execute(
            "UPDATE mdu_buildings SET name=?, address=?, lat=?, lng=?, "
            "owner_type=?, floors_count=?, units_per_floor=?, ref_hw_id=?, notes=? "
            "WHERE id=? AND company_id=?",
            (body.name, body.address, body.lat, body.lng, body.owner_type,
             body.floors_count, body.units_per_floor, body.ref_hw_id,
             body.notes, bid, sc["company_id"]))
    return {"ok": True, "id": bid}


@router.delete("/api/admin/mdu/buildings/{bid}")
def delete_building(bid: int, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_building(con, sc["company_id"], bid)
        # Cascade
        floor_ids = [r[0] for r in con.execute(
            "SELECT id FROM mdu_floors WHERE building_id=?", (bid,)).fetchall()]
        if floor_ids:
            qmarks = ",".join("?" * len(floor_ids))
            unit_ids = [r[0] for r in con.execute(
                f"SELECT id FROM mdu_units WHERE floor_id IN ({qmarks})",
                tuple(floor_ids)).fetchall()]
            if unit_ids:
                qm2 = ",".join("?" * len(unit_ids))
                con.execute(f"DELETE FROM mdu_unit_customers WHERE unit_id IN ({qm2})", tuple(unit_ids))
                con.execute(f"DELETE FROM mdu_units WHERE id IN ({qm2})", tuple(unit_ids))
            con.execute(f"DELETE FROM mdu_floors WHERE id IN ({qmarks})", tuple(floor_ids))
        con.execute("DELETE FROM mdu_buildings WHERE id=? AND company_id=?",
                    (bid, sc["company_id"]))
    return {"ok": True, "deleted": bid}


@router.get("/api/admin/mdu/buildings/{bid}/tree")
def building_tree(bid: int, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        b = _own_building(con, sc["company_id"], bid)
        floors = [dict(r) for r in con.execute(
            "SELECT * FROM mdu_floors WHERE building_id=? "
            "ORDER BY floor_number ASC", (bid,)).fetchall()]
        for f in floors:
            f["units"] = [dict(r) for r in con.execute(
                "SELECT u.*, "
                "(SELECT uc.customer_id FROM mdu_unit_customers uc "
                " WHERE uc.unit_id=u.id AND uc.active=1 ORDER BY uc.linked_at DESC LIMIT 1) "
                "AS active_customer_id, "
                "(SELECT c.customer_name FROM mdu_unit_customers uc "
                " JOIN customers c ON c.customer_id=uc.customer_id AND c.company_id=u.company_id "
                " WHERE uc.unit_id=u.id AND uc.active=1 ORDER BY uc.linked_at DESC LIMIT 1) "
                "AS active_customer_name, "
                "(SELECT o.serial FROM onus o WHERE o.id=u.ref_onu_id) AS onu_serial, "
                "(SELECT o.status FROM onus o WHERE o.id=u.ref_onu_id) AS onu_status "
                "FROM mdu_units u WHERE u.floor_id=? ORDER BY u.unit_label ASC",
                (f["id"],)).fetchall()]
    return {"ok": True, "building": b, "floors": floors}


@router.post("/api/admin/mdu/buildings/{bid}/floors")
def add_floor(bid: int, body: FloorIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_building(con, sc["company_id"], bid)
        cur = con.execute(
            "INSERT INTO mdu_floors (company_id, building_id, floor_number, label) "
            "VALUES (?,?,?,?)",
            (sc["company_id"], bid, body.floor_number,
             body.label or str(body.floor_number)))
        return {"ok": True, "id": cur.lastrowid}


@router.patch("/api/admin/mdu/floors/{fid}")
def update_floor(fid: int, body: FloorIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_floor(con, sc["company_id"], fid)
        con.execute(
            "UPDATE mdu_floors SET floor_number=?, label=? WHERE id=? AND company_id=?",
            (body.floor_number, body.label, fid, sc["company_id"]))
    return {"ok": True, "id": fid}


@router.delete("/api/admin/mdu/floors/{fid}")
def delete_floor(fid: int, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_floor(con, sc["company_id"], fid)
        unit_ids = [r[0] for r in con.execute(
            "SELECT id FROM mdu_units WHERE floor_id=?", (fid,)).fetchall()]
        if unit_ids:
            qm = ",".join("?" * len(unit_ids))
            con.execute(f"DELETE FROM mdu_unit_customers WHERE unit_id IN ({qm})", tuple(unit_ids))
            con.execute(f"DELETE FROM mdu_units WHERE id IN ({qm})", tuple(unit_ids))
        con.execute("DELETE FROM mdu_floors WHERE id=? AND company_id=?", (fid, sc["company_id"]))
    return {"ok": True, "deleted": fid}


@router.post("/api/admin/mdu/floors/{fid}/units")
def add_unit(fid: int, body: UnitIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_floor(con, sc["company_id"], fid)
        cur = con.execute(
            "INSERT INTO mdu_units (company_id, floor_id, unit_label, unit_type, "
            "ref_onu_id, ref_hw_id, notes) VALUES (?,?,?,?,?,?,?)",
            (sc["company_id"], fid, body.unit_label, body.unit_type or "apartment",
             body.ref_onu_id, body.ref_hw_id, body.notes))
        return {"ok": True, "id": cur.lastrowid}


@router.patch("/api/admin/mdu/units/{uid}")
def update_unit(uid: int, body: UnitIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_unit(con, sc["company_id"], uid)
        con.execute(
            "UPDATE mdu_units SET unit_label=?, unit_type=?, ref_onu_id=?, "
            "ref_hw_id=?, notes=? WHERE id=? AND company_id=?",
            (body.unit_label, body.unit_type, body.ref_onu_id, body.ref_hw_id,
             body.notes, uid, sc["company_id"]))
    return {"ok": True, "id": uid}


@router.delete("/api/admin/mdu/units/{uid}")
def delete_unit(uid: int, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_unit(con, sc["company_id"], uid)
        con.execute("DELETE FROM mdu_unit_customers WHERE unit_id=?", (uid,))
        con.execute("DELETE FROM mdu_units WHERE id=? AND company_id=?",
                    (uid, sc["company_id"]))
    return {"ok": True, "deleted": uid}


@router.post("/api/admin/mdu/units/{uid}/link-customer")
def link_unit_customer(uid: int, body: LinkCustomerIn, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_unit(con, sc["company_id"], uid)
        cust = con.execute(
            "SELECT customer_id FROM customers WHERE customer_id=? AND company_id=?",
            (body.customer_id, sc["company_id"])).fetchone()
        if not cust:
            raise HTTPException(404, "Customer not found in this company")
        # Close any active links on this unit first.
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "UPDATE mdu_unit_customers SET active=0, ended_at=? "
            "WHERE unit_id=? AND active=1", (now, uid))
        con.execute(
            "INSERT INTO mdu_unit_customers (company_id, unit_id, customer_id) "
            "VALUES (?,?,?)", (sc["company_id"], uid, body.customer_id))
    return {"ok": True}


@router.delete("/api/admin/mdu/units/{uid}/link-customer")
def unlink_unit_customer(uid: int, request: Request):
    sc = _require_scope(request)
    with _compat_conn(timeout=10) as con:
        con.row_factory = sqlite3.Row
        _own_unit(con, sc["company_id"], uid)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "UPDATE mdu_unit_customers SET active=0, ended_at=? "
            "WHERE unit_id=? AND active=1", (now, uid))
    return {"ok": True}


@router.get("/admin/mdu", response_class=HTMLResponse)
def mdu_page(request: Request):
    sc = _require_scope(request)
    ctx = _portal_context(request, sc, "mdu")
    return templates.TemplateResponse("admin_mdu.html", ctx)


def register(app, **_):
    app.include_router(router)
    print("[mdu_routes] router wired — s56V Phase B MDU")
