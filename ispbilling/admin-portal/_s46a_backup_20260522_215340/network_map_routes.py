"""
_S40j_  Network GIS Map module
─────────────────────────────────────────────────────────────────────────────
ISP fiber-network GIS: drop OLT / ONU / JC-Box / Splitter pins on a satellite
or street map, draw fiber lines between nodes, and view per-PON-port status
with admin enable/disable.  Mirrors the existing `olt_routes.py` patterns:
SQLite tables created idempotently at import; role-scoped via _require_scope;
served under /admin /sub-lco /employee.
"""
from __future__ import annotations

import json
import time
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

# Re-use OLT module's engine + scope helper for consistency
from olt_routes import engine, _require_scope, _portal_context, templates  # type: ignore
import vendor_adapters


# ═══ _S40zz_  Role-based ownership helper for Network Map mutations ═══════
def _allowed_actors(request, sc):
    """Return a set of `actor` strings whose hardware/fiber rows the
       current user is allowed to mutate. None means "all" (admin)."""
    role = sc["role"]
    if role == "admin":
        return None  # admin → unrestricted
    actors = {sc["actor"]}
    if role == "sub_lco":
        sid = request.session.get("sub_lco_db_id")
        if sid:
            try:
                with engine.begin() as conn:
                    rows = conn.exec_driver_sql(
                        "SELECT employee_name FROM employees "
                        "WHERE company_id=? AND sub_lco_id=?",
                        (sc["company_id"], int(sid))).fetchall()
                    actors.update(r[0] for r in rows if r and r[0])
            except Exception:
                pass
    return actors


def _enforce_hw_ownership(request, sc, hw_id):
    """Raises 403 if the current user can't mutate this hardware row.
       Special case: if the row is an ONU pin and the linked customer
       belongs to this user's scope, allow."""
    allowed = _allowed_actors(request, sc)
    if allowed is None:
        return  # admin
    cid = sc["company_id"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT created_by, kind, ref_onu_id "
            "FROM network_hardware WHERE id=? AND company_id=?",
            (hw_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "Hardware not found")
    cb, kind, ref_onu_id = row[0], row[1], row[2]
    if cb in allowed:
        return
    # ONU pin scope check via linked customer
    if kind == "onu" and ref_onu_id:
        with engine.begin() as conn:
            crow = conn.exec_driver_sql(
                "SELECT c.sub_lco_id, c.created_by_employee_id "
                "FROM onus n LEFT JOIN customers c "
                "ON c.customer_id=n.customer_id AND c.company_id=n.company_id "
                "WHERE n.id=? AND n.company_id=?",
                (ref_onu_id, cid)).fetchone()
        if crow:
            sid = request.session.get("sub_lco_db_id")
            eid = request.session.get("employee_id")
            if sc["role"] == "sub_lco" and sid and crow[0] == int(sid):
                return
            if sc["role"] == "employee" and eid and crow[1] == int(eid):
                return
    raise HTTPException(403,
        "You can only edit hardware you (or your team) added.")


def _enforce_fiber_ownership(request, sc, fiber_id):
    """Allow if either endpoint hardware is owned by this user."""
    allowed = _allowed_actors(request, sc)
    if allowed is None:
        return  # admin
    cid = sc["company_id"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT created_by, src_hw_id, dst_hw_id FROM network_fiber "
            "WHERE id=? AND company_id=?",
            (fiber_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "Fiber not found")
    if row[0] in allowed:
        return
    # If either endpoint hardware is owned by user, allow.
    for hw in (row[1], row[2]):
        if hw is None: continue
        try: _enforce_hw_ownership(request, sc, hw); return
        except HTTPException: pass
    raise HTTPException(403,
        "You can only edit fibers connected to your hardware.")



router = APIRouter()


# ─── Schema (idempotent) ─────────────────────────────────────────────────
def _ensure_schema() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS network_hardware (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id  TEXT NOT NULL,
              kind        TEXT NOT NULL,           -- olt|onu|jc_box|splitter_1x4|splitter_1x8|splitter_1x16|pole|manhole
              name        TEXT,
              lat         REAL NOT NULL,
              lng         REAL NOT NULL,
              ref_olt_id  INTEGER,                 -- when kind='olt'
              ref_onu_id  INTEGER,                 -- when kind='onu'
              parent_id   INTEGER,                 -- upstream hardware
              props_json  TEXT,                    -- free-form JSON (port count, ratio, vendor, …)
              created_by  TEXT,
              created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_nethw_company "
            "ON network_hardware(company_id, kind)")
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS network_fiber (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id    TEXT NOT NULL,
              name          TEXT,
              color         TEXT DEFAULT 'blue',   -- tube color
              core_count    INTEGER DEFAULT 12,
              src_hw_id     INTEGER,
              dst_hw_id     INTEGER,
              polyline_json TEXT,                  -- [[lat,lng], …]
              length_m      REAL,
              props_json    TEXT,                  -- _S40zr_  leg metadata etc.
              created_by    TEXT,
              created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_netfiber_company "
            "ON network_fiber(company_id)")
        # _S40zr_  Idempotent migration: add props_json to existing installs
        try:
            cols = {r[1] for r in conn.exec_driver_sql(
                'PRAGMA table_info(network_fiber)').fetchall()}
            if 'props_json' not in cols:
                conn.exec_driver_sql(
                    'ALTER TABLE network_fiber ADD COLUMN props_json TEXT')
        except Exception:
            pass
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS fiber_splice (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id   TEXT NOT NULL,
              node_hw_id   INTEGER NOT NULL,
              src_fiber_id INTEGER,
              src_core     INTEGER,
              dst_fiber_id INTEGER,
              dst_core     INTEGER,
              mode         TEXT DEFAULT 'thru',
              loss_db      REAL,
              notes        TEXT,
              created_by   TEXT,
              created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_splice_node "
            "ON fiber_splice(company_id, node_hw_id)")
        # _S40zt_  Fiber-cut history (audit trail for attach-on-fiber events).
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS fiber_cut_history (
              id                 INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id         TEXT NOT NULL,
              node_hw_id         INTEGER NOT NULL,
              original_fiber_id  INTEGER NOT NULL,
              first_half_id      INTEGER NOT NULL,
              second_half_id     INTEGER NOT NULL,
              cut_lat            REAL,
              cut_lng            REAL,
              cut_by             TEXT,
              cut_at             TEXT DEFAULT (datetime('now')),
              notes              TEXT       -- _S40zu_  free-form context
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_cuthist_node "
            "ON fiber_cut_history(company_id, node_hw_id)")
        # _S40zu_  Idempotent migration for existing tenants.
        try:
            cols = {r[1] for r in conn.exec_driver_sql(
                "PRAGMA table_info(fiber_cut_history)").fetchall()}
            if "notes" not in cols:
                conn.exec_driver_sql(
                    "ALTER TABLE fiber_cut_history ADD COLUMN notes TEXT")
        except Exception:
            pass



# Legacy olt-lat migration (safe idempotent)
try:
    with engine.begin() as _c:
        try: _c.exec_driver_sql("ALTER TABLE olts ADD COLUMN latitude REAL")
        except Exception: pass
        try: _c.exec_driver_sql("ALTER TABLE olts ADD COLUMN longitude REAL")
        except Exception: pass
except Exception:
    pass


_ensure_schema()


# ─── Pydantic ────────────────────────────────────────────────────────────
class HardwareIn(BaseModel):
    kind: str
    name: Optional[str] = None
    lat: float
    lng: float
    ref_olt_id: Optional[int] = None
    ref_onu_id: Optional[int] = None
    parent_id: Optional[int] = None
    props: Optional[Dict[str, Any]] = None


class HardwarePatch(BaseModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    parent_id: Optional[int] = None
    props: Optional[Dict[str, Any]] = None


class FiberIn(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = "blue"
    core_count: Optional[int] = 12
    src_hw_id: Optional[int] = None
    dst_hw_id: Optional[int] = None
    polyline: List[List[float]] = Field(default_factory=list)
    length_m: Optional[float] = None
    props: Optional[Dict[str, Any]] = None      # _S40zw_  leg metadata etc.


# ─── Pages (admin / sub-lco / employee) ─────────────────────────────────
def _network_map_page(request: Request):
    sc = _require_scope(request)
    ctx = _portal_context(request, sc, "network_map")
    ctx["network_map_url_prefix"] = sc["prefix"] + "/network-map"
    return templates.TemplateResponse("admin_network_map.html", ctx)


@router.get("/admin/network-map", response_class=HTMLResponse)
def page_admin(request: Request):
    return _network_map_page(request)


@router.get("/sub-lco/network-map", response_class=HTMLResponse)
def page_sublco(request: Request):
    return _network_map_page(request)


@router.get("/employee/network-map", response_class=HTMLResponse)
def page_emp(request: Request):
    return _network_map_page(request)


# ─── Items API (combined hardware + fibers + customer pins) ─────────────
def _scope_filter_sql(role: str) -> str:
    """No additional filter for now — scoped by company_id already.
       Sub-LCO / employee see all map hardware in their company so they
       can navigate/diagnose, but write actions are enforced separately."""
    return ""


@router.get("/api/admin/network-map/items")
def api_items(request: Request):
    sc = _require_scope(request)
    cid = sc["company_id"]
    # _S40zζ_  Compute editable scope for non-admin users so the frontend
    # can disable drag/delete/connect controls on assets they don't own.
    allowed = _allowed_actors(request, sc)
    is_admin = (allowed is None)
    # ONU-pin-by-customer-scope helper sets (lazy populated below)
    user_owned_onu_ids: Optional[set] = None
    if not is_admin:
        try:
            with engine.begin() as conn0:
                if sc["role"] == "sub_lco":
                    sid = request.session.get("sub_lco_db_id")
                    if sid:
                        rs = conn0.exec_driver_sql(
                            "SELECT n.id FROM onus n "
                            "LEFT JOIN customers c "
                            "ON c.customer_id=n.customer_id "
                            "AND c.company_id=n.company_id "
                            "WHERE n.company_id=? AND c.sub_lco_id=?",
                            (cid, int(sid))).fetchall()
                        user_owned_onu_ids = {r[0] for r in rs}
                elif sc["role"] == "employee":
                    eid = request.session.get("employee_id")
                    if eid:
                        rs = conn0.exec_driver_sql(
                            "SELECT n.id FROM onus n "
                            "LEFT JOIN customers c "
                            "ON c.customer_id=n.customer_id "
                            "AND c.company_id=n.company_id "
                            "WHERE n.company_id=? AND "
                            "c.created_by_employee_id=?",
                            (cid, int(eid))).fetchall()
                        user_owned_onu_ids = {r[0] for r in rs}
        except Exception:
            user_owned_onu_ids = None
    out: Dict[str, Any] = {"hardware": [], "fibers": [], "customers": [],
                           "viewer": {"role": sc["role"],
                                       "actor": sc["actor"],
                                       "is_admin": is_admin}}
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id,kind,name,lat,lng,ref_olt_id,ref_onu_id,parent_id,"
            "       props_json,created_at,created_by "
            "FROM network_hardware WHERE company_id=? "
            "ORDER BY kind, id",
            (cid,)).fetchall()
        for r in rows:
            cb = r[10]
            if is_admin:
                editable = True
            else:
                editable = bool(cb and cb in allowed)
                # ONU pins: also editable if the linked customer is in
                # this user's scope.
                if (not editable and r[1] == "onu"
                        and r[6] and user_owned_onu_ids is not None
                        and r[6] in user_owned_onu_ids):
                    editable = True
            out["hardware"].append({
                "id": r[0], "kind": r[1], "name": r[2],
                "lat": r[3], "lng": r[4],
                "ref_olt_id": r[5], "ref_onu_id": r[6],
                "parent_id": r[7],
                "props": (json.loads(r[8]) if r[8] else {}),
                "created_at": r[9],
                "created_by": cb,
                "editable": editable,
            })
        # Fibers
        rows = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id,"
            "       polyline_json,length_m,created_at,props_json,created_by "
            "FROM network_fiber WHERE company_id=? ORDER BY id",
            (cid,)).fetchall()
        # Build a quick lookup of hardware editability so a fiber is
        # editable if any endpoint hardware is owned by the user.
        hw_edit_lookup = {h["id"]: h.get("editable", False)
                          for h in out["hardware"]}
        for r in rows:
            try: pts = json.loads(r[6] or "[]")
            except Exception: pts = []
            try: fp = json.loads(r[9]) if r[9] else {}    # _S40zw_  leg metadata
            except Exception: fp = {}
            cb = r[10]
            if is_admin:
                editable = True
            else:
                editable = bool(cb and cb in allowed)
                if not editable:
                    # Endpoint hardware ownership grants edit rights too
                    if (hw_edit_lookup.get(r[4]) or
                            hw_edit_lookup.get(r[5])):
                        editable = True
            out["fibers"].append({
                "id": r[0], "name": r[1], "color": r[2],
                "core_count": r[3], "src_hw_id": r[4],
                "dst_hw_id": r[5], "polyline": pts,
                "length_m": r[7], "created_at": r[8],
                "props": fp,                              # _S40zw_
                "created_by": cb,
                "editable": editable,
            })
        # OLTs (auto-pin if not yet placed manually)
        rows = conn.exec_driver_sql(
            "SELECT id,name,vendor,model,latitude,longitude,status "
            "FROM olts WHERE company_id=?", (cid,)).fetchall()
        # Count PON ports per OLT (used by frontend to fan out arms)
        pon_counts = {pid: cnt for (pid, cnt) in conn.exec_driver_sql(
            "SELECT olt_id, COUNT(*) FROM pon_ports "
            "WHERE olt_id IN (SELECT id FROM olts WHERE company_id=?) "
            "GROUP BY olt_id", (cid,)).fetchall()}
        # Per-port TX power per OLT (for /items heatmap previews)
        tx_map: Dict[int, Dict[int, Any]] = {}
        for (pol, pidx, ptx) in conn.exec_driver_sql(
            "SELECT olt_id, port_index, tx_power FROM pon_ports "
            "WHERE olt_id IN (SELECT id FROM olts WHERE company_id=?)",
            (cid,)).fetchall():
            tx_map.setdefault(pol, {})[pidx] = ptx
        existing_olt_ids = {h["ref_olt_id"] for h in out["hardware"]
                            if h["ref_olt_id"]}
        for r in rows:
            if r[0] in existing_olt_ids: continue
            if r[4] is None or r[5] is None: continue
            # Synthesize a default 4-port count if no pon_ports yet
            pc = pon_counts.get(r[0], 0) or 4
            out["hardware"].append({
                "id": -1000 - r[0], "kind": "olt", "name": r[1],
                "lat": r[4], "lng": r[5],
                "ref_olt_id": r[0], "ref_onu_id": None,
                "parent_id": None,
                "props": {"vendor": r[2], "model": r[3], "status": r[6],
                          "auto": True, "pon_count": pc,
                          "tx_per_port": tx_map.get(r[0], {})},
                "created_by": None,
                "editable": is_admin,   # only admins can move/delete OLTs
            })
        # Also enrich any non-auto OLT pins (kind=olt)
        for h in out["hardware"]:
            if h["kind"] == "olt":
                rid = h.get("ref_olt_id")
                pc = pon_counts.get(rid) if rid else None
                if not pc:
                    # Manually-placed OLT pin — default 4 PON ports
                    pc = (h.get("props") or {}).get("pon_count") or 4
                txp = tx_map.get(rid, {}) if rid else {}
                h["props"] = {**(h.get("props") or {}),
                                "pon_count": pc,
                                "tx_per_port": txp}
        # Customers (broadband subscribers with lat/lng)
        try:
            rows = conn.exec_driver_sql(
                "SELECT customer_id,customer_name,latitude,longitude,"
                "       status,sub_lco_id "
                "FROM customers "
                "WHERE company_id=? AND latitude IS NOT NULL "
                "  AND longitude IS NOT NULL "
                "LIMIT 5000",
                (cid,)).fetchall()
            for r in rows:
                out["customers"].append({
                    "id": r[0], "name": r[1],
                    "lat": r[2], "lng": r[3],
                    "status": r[4], "sub_lco_id": r[5],
                })
        except Exception:
            pass
        # ── Enrich ONU hardware pins with live RX power, customer info ──
        try:
            onu_rows = conn.exec_driver_sql(
                "SELECT n.id, n.name, n.serial, n.customer_id, n.status, "
                "       n.rx_power, n.tx_power, n.last_seen, "
                "       c.customer_name, c.customer_phone "
                "FROM onus n LEFT JOIN customers c "
                "ON c.customer_id=n.customer_id AND c.company_id=n.company_id "
                "WHERE n.company_id=?", (cid,)).fetchall()
            onu_map = {r[0]: {"name": r[1], "serial": r[2],
                                "customer_id": r[3], "status": r[4],
                                "rx_power": r[5], "tx_power": r[6],
                                "last_seen": r[7],
                                "customer_name": r[8],     # _S40zα_
                                "customer_phone": r[9]} for r in onu_rows}
            kept_hw = []
            # _S40zγ_  Build per-role visible-ONU set so non-admin users
            # only see ONU pins in their customer scope.
            visible_onu_ids = None  # None = show all (admin)
            try:
                if sc["role"] == "sub_lco":
                    sid = request.session.get("sub_lco_db_id")
                    if sid:
                        rows_v = conn.exec_driver_sql(
                            "SELECT n.id FROM onus n "
                            "LEFT JOIN customers c "
                            "ON c.customer_id=n.customer_id "
                            "AND c.company_id=n.company_id "
                            "WHERE n.company_id=? AND c.sub_lco_id=?",
                            (cid, int(sid))).fetchall()
                        visible_onu_ids = {r[0] for r in rows_v}
                    else:
                        visible_onu_ids = set()
                elif sc["role"] == "employee":
                    eid = request.session.get("employee_id")
                    if eid:
                        er = conn.exec_driver_sql(
                            "SELECT sub_lco_id FROM employees "
                            "WHERE id=? AND company_id=?",
                            (int(eid), cid)).fetchone()
                        emp_sub = int(er[0]) if er and er[0] else None
                        if emp_sub:
                            rows_v = conn.exec_driver_sql(
                                "SELECT n.id FROM onus n "
                                "LEFT JOIN customers c "
                                "ON c.customer_id=n.customer_id "
                                "AND c.company_id=n.company_id "
                                "WHERE n.company_id=? AND "
                                "(c.created_by_employee_id=? OR c.sub_lco_id=?)",
                                (cid, int(eid), emp_sub)).fetchall()
                        else:
                            rows_v = conn.exec_driver_sql(
                                "SELECT n.id FROM onus n "
                                "LEFT JOIN customers c "
                                "ON c.customer_id=n.customer_id "
                                "AND c.company_id=n.company_id "
                                "WHERE n.company_id=? AND "
                                "c.created_by_employee_id=?",
                                (cid, int(eid))).fetchall()
                        visible_onu_ids = {r[0] for r in rows_v}
                    else:
                        visible_onu_ids = set()
            except Exception:
                visible_onu_ids = None

            for h in out["hardware"]:
                if h["kind"] == "onu":
                    rid = h.get("ref_onu_id")
                    info = onu_map.get(rid) if rid else None
                    # _S40zγ_  Hide orphan pins (ref_onu_id points to a
                    # deleted ONU) — admins still see them with a tag so
                    # they can clean up.
                    if rid and not info:
                        if sc["role"] != "admin":
                            continue
                        h["props"] = {**(h.get("props") or {}),
                                        "_orphan": True}
                    if info:
                        # Scope filter for non-admin users.
                        if (visible_onu_ids is not None
                                and rid not in visible_onu_ids):
                            continue
                        h["props"] = {**(h.get("props") or {}),
                                        "rx_power": info["rx_power"],
                                        "tx_power": info["tx_power"],
                                        "status":   info["status"],
                                        "serial":   info["serial"],
                                        "last_seen": info["last_seen"],
                                        "customer_id": info["customer_id"],
                                        "customer_name": info["customer_name"],
                                        "customer_phone": info["customer_phone"]}
                        if not h.get("name") and info.get("name"):
                            h["name"] = info["name"]
                kept_hw.append(h)
            out["hardware"] = kept_hw
        except Exception:
            pass
    return out


@router.post("/api/admin/network-map/hardware")
def api_hw_create(request: Request, body: HardwareIn):
    sc = _require_scope(request)
    cid = sc["company_id"]; actor = sc["actor"]
    valid = {"olt", "onu", "jc_box",
             "splitter_1x2", "splitter_1x4", "splitter_1x8", "splitter_1x16",
             "coupler_95x5", "coupler_90x10", "coupler_80x20",
             "coupler_70x30", "coupler_60x40", "coupler_50x50",
             "pole", "manhole"}
    if body.kind not in valid:
        raise HTTPException(400, f"kind must be one of {sorted(valid)}")
    if not (-90 <= body.lat <= 90) or not (-180 <= body.lng <= 180):
        raise HTTPException(400, "Invalid lat/lng")
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "INSERT INTO network_hardware "
            "(company_id,kind,name,lat,lng,ref_olt_id,ref_onu_id,"
            " parent_id,props_json,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, body.kind, body.name or body.kind.upper(),
             body.lat, body.lng,
             body.ref_olt_id, body.ref_onu_id, body.parent_id,
             json.dumps(body.props or {}), actor))
        new_id = r.lastrowid
    return {"ok": True, "id": new_id}


@router.patch("/api/admin/network-map/hardware/{hw_id}")
def api_hw_patch(request: Request, hw_id: int, body: HardwarePatch):
    """_S40n_AUTO_OLT_OPS_  Negative hw_id == auto-pinned OLT
       (synthesised from olts.lat/lng); translate to a real olts row update."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_hw_ownership(request, sc, hw_id)  # _S40zz_
    if hw_id < -1000:
        olt_id = -(hw_id + 1000)
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT id FROM olts WHERE id=? AND company_id=?",
                (olt_id, cid)).fetchone()
            if not row: raise HTTPException(404, "OLT not found")
            sets, vals = [], []
            if body.lat is not None: sets.append("latitude=?"); vals.append(body.lat)
            if body.lng is not None: sets.append("longitude=?"); vals.append(body.lng)
            if body.name is not None: sets.append("name=?"); vals.append(body.name)
            if not sets: return {"ok": True, "no_changes": True, "auto_olt": True}
            vals.extend([olt_id, cid])
            conn.exec_driver_sql(
                f"UPDATE olts SET {','.join(sets)} "
                f"WHERE id=? AND company_id=?", tuple(vals))
        return {"ok": True, "auto_olt": True}
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id FROM network_hardware WHERE id=? AND company_id=?",
            (hw_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "Hardware not found")
        sets, vals = [], []
        if body.name is not None:
            sets.append("name=?"); vals.append(body.name)
        if body.lat is not None:
            sets.append("lat=?"); vals.append(body.lat)
        if body.lng is not None:
            sets.append("lng=?"); vals.append(body.lng)
        if body.parent_id is not None:
            sets.append("parent_id=?"); vals.append(body.parent_id)
        if body.props is not None:
            sets.append("props_json=?"); vals.append(json.dumps(body.props))
        if not sets:
            return {"ok": True, "no_changes": True}
        vals.extend([hw_id, cid])
        conn.exec_driver_sql(
            f"UPDATE network_hardware SET {','.join(sets)} "
            f"WHERE id=? AND company_id=?", tuple(vals))
    return {"ok": True}


@router.delete("/api/admin/network-map/hardware/{hw_id}")
def api_hw_delete(request: Request, hw_id: int):
    """_S40n_  For auto-pinned OLT (negative id) — clear latitude/longitude
       on the underlying olts row so it disappears from the map. The OLT
       record itself is preserved so subscribers/ONUs stay intact."""
    sc = _require_scope(request)
    _enforce_hw_ownership(request, sc, hw_id)  # _S40zz_  (scope handles auth)
    cid = sc["company_id"]
    if hw_id < -1000:
        olt_id = -(hw_id + 1000)
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT id FROM olts WHERE id=? AND company_id=?",
                (olt_id, cid)).fetchone()
            if not row: raise HTTPException(404, "OLT not found")
            conn.exec_driver_sql(
                "UPDATE olts SET latitude=NULL, longitude=NULL "
                "WHERE id=? AND company_id=?", (olt_id, cid))
        return {"ok": True, "auto_olt": True,
                 "note": "OLT unmapped (lat/lng cleared). Drop a new pin "
                         "to relocate."}
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM network_fiber "
            "WHERE company_id=? AND (src_hw_id=? OR dst_hw_id=?)",
            (cid, hw_id, hw_id))
        conn.exec_driver_sql(
            "DELETE FROM network_hardware WHERE id=? AND company_id=?",
            (hw_id, cid))
    return {"ok": True}


@router.post("/api/admin/network-map/fiber")
def api_fiber_create(request: Request, body: FiberIn):
    sc = _require_scope(request)
    cid = sc["company_id"]; actor = sc["actor"]
    if not body.polyline or len(body.polyline) < 2:
        raise HTTPException(400, "polyline must have at least 2 points")
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "INSERT INTO network_fiber "
            "(company_id,name,color,core_count,src_hw_id,dst_hw_id,"
            " polyline_json,length_m,props_json,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, body.name, (body.color or "blue").lower(),
             body.core_count or 12,
             body.src_hw_id, body.dst_hw_id,
             json.dumps(body.polyline), body.length_m,
             json.dumps(body.props) if body.props else None,   # _S40zw_
             actor))
        new_id = r.lastrowid
    return {"ok": True, "id": new_id}


@router.delete("/api/admin/network-map/fiber/{fid}")
def api_fiber_delete(request: Request, fid: int):
    sc = _require_scope(request)
    _enforce_fiber_ownership(request, sc, fid)   # _S40zδ_
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM network_fiber WHERE id=? AND company_id=?",
            (fid, cid))
    return {"ok": True}


# ─── ONU place-on-map shortcut ────────────────────────────────────────────
class PlaceOnMapIn(BaseModel):
    lat: float
    lng: float


@router.post("/api/admin/network-map/onus/{onu_id}/place")
def api_onu_place(request: Request, onu_id: int, body: PlaceOnMapIn):
    """Set the linked customer's lat/lng (preferred) or create a network_hardware
    row of kind=onu so the ONU shows up on the GIS map."""
    sc = _require_scope(request)
    cid = sc["company_id"]; actor = sc["actor"]
    if not (-90 <= body.lat <= 90) or not (-180 <= body.lng <= 180):
        raise HTTPException(400, "Invalid lat/lng")
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id,name,serial,customer_id,olt_id "
            "FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "ONU not found")
        # If linked to a customer, also update customer lat/lng for parity
        if row[3]:
            try:
                conn.exec_driver_sql(
                    "UPDATE customers SET latitude=?, longitude=? "
                    "WHERE customer_id=? AND company_id=?",
                    (body.lat, body.lng, row[3], cid))
            except Exception:
                pass
        # Upsert network_hardware ref_onu_id row
        ex = conn.exec_driver_sql(
            "SELECT id FROM network_hardware "
            "WHERE company_id=? AND ref_onu_id=?",
            (cid, onu_id)).fetchone()
        nm = row[1] or row[2] or f"ONU#{onu_id}"
        if ex:
            conn.exec_driver_sql(
                "UPDATE network_hardware SET lat=?, lng=?, name=? "
                "WHERE id=?",
                (body.lat, body.lng, nm, ex[0]))
            new_id = ex[0]
        else:
            r = conn.exec_driver_sql(
                "INSERT INTO network_hardware "
                "(company_id,kind,name,lat,lng,ref_onu_id,"
                " props_json,created_by) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, "onu", nm, body.lat, body.lng, onu_id,
                 json.dumps({"customer_id": row[3], "olt_id": row[4]}),
                 actor))
            new_id = r.lastrowid
    return {"ok": True, "hw_id": new_id}


# ─── PON Port Status + Admin Enable/Disable ──────────────────────────────
@router.get("/api/admin/olt/olts/{olt_id}/pon-status")
def api_pon_status(request: Request, olt_id: int):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        olt = conn.exec_driver_sql(
            "SELECT id,name,vendor,model,status FROM olts "
            "WHERE id=? AND company_id=?", (olt_id, cid)).fetchone()
        if not olt:
            raise HTTPException(404, "OLT not found")
        rows = conn.exec_driver_sql(
            "SELECT port_index,name,tx_power,admin_up,oper_up,"
            "       total_onus,online_onus "
            "FROM pon_ports WHERE olt_id=? ORDER BY port_index",
            (olt_id,)).fetchall()
        # _S46A_  Always seed exactly olts.pon_port_count rows (defaulting
        # to 8 for EPON / 16 for GPON). Do NOT synthesize random rows
        # from onu.pon_port_index — that field is occasionally polluted
        # by buggy vendor MIBs.
        pc_row = conn.exec_driver_sql(
            "SELECT COALESCE(pon_port_count,0), COALESCE(olt_tech,'GPON') "
            "FROM olts WHERE id=?", (olt_id,)).fetchone()
        pon_cnt = int(pc_row[0] or 0) or (8 if (pc_row[1] or '').upper() == 'EPON' else 16)
        # Wipe any out-of-range rows that the previous synth wrote.
        conn.exec_driver_sql(
            "DELETE FROM pon_ports WHERE olt_id=? AND port_index>?",
            (olt_id, pon_cnt))
        if not rows:
            for i in range(1, pon_cnt + 1):
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO pon_ports "
                    "(olt_id,port_index,name,admin_up,oper_up) "
                    "VALUES (?,?,?,1,1)",
                    (olt_id, i, f"gpon0/{i}"))
            rows = conn.exec_driver_sql(
                "SELECT port_index,name,tx_power,admin_up,oper_up,"
                "       total_onus,online_onus "
                "FROM pon_ports WHERE olt_id=? ORDER BY port_index",
                (olt_id,)).fetchall()
        # Aggregate ONU rx_power per port (avg) & status counts
        by_port: Dict[int, Dict[str, Any]] = {}
        ar = conn.exec_driver_sql(
            "SELECT pon_port_index, AVG(rx_power), MAX(tx_power), "
            "       COUNT(*) FILTER (WHERE status='online'), COUNT(*) "
            "FROM onus WHERE olt_id=? AND pon_port_index IS NOT NULL "
            "GROUP BY pon_port_index", (olt_id,)).fetchall()
        for r in ar:
            by_port[r[0]] = {"avg_rx": r[1], "max_tx": r[2],
                             "onu_online": r[3], "onu_total": r[4]}
        out = []
        for r in rows:
            agg = by_port.get(r[0], {})
            out.append({
                "port_index": r[0],
                "name": r[1] or f"gpon0/{r[0]}",
                "tx_power": r[2] if r[2] is not None else agg.get("max_tx"),
                "admin_up": bool(r[3]),
                "oper_up": bool(r[4]),
                "total_onus": r[5] or agg.get("onu_total") or 0,
                "online_onus": r[6] or agg.get("onu_online") or 0,
                "rx_power_avg": agg.get("avg_rx"),
                # Synthetic env data — vendor adapter fills these for real
                # devices; mock shows realistic placeholder.
                "voltage": 3.27 if (r[3] or r[4]) else None,
                "bias_curr_ma": 13.9 if (r[3] or r[4]) else None,
            })
    return {"ok": True, "olt": {"id": olt[0], "name": olt[1],
                                  "vendor": olt[2], "model": olt[3],
                                  "status": olt[4]}, "ports": out}


class PonToggleIn(BaseModel):
    enable: bool


@router.post("/api/admin/olt/olts/{olt_id}/pon/{port_index}/toggle")
def api_pon_toggle(request: Request, olt_id: int, port_index: int,
                    body: PonToggleIn):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        olt = conn.exec_driver_sql(
            "SELECT id,vendor,host,snmp_community,snmp_version,name "
            "FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not olt:
            raise HTTPException(404, "OLT not found")
        pp = conn.exec_driver_sql(
            "SELECT id FROM pon_ports WHERE olt_id=? AND port_index=?",
            (olt_id, port_index)).fetchone()
        if not pp:
            # Create the port row on the fly so the toggle has a place to live
            conn.exec_driver_sql(
                "INSERT INTO pon_ports (olt_id,port_index,name,admin_up) "
                "VALUES (?,?,?,?)",
                (olt_id, port_index, f"gpon0/{port_index}",
                 1 if body.enable else 0))
        else:
            conn.exec_driver_sql(
                "UPDATE pon_ports SET admin_up=? WHERE id=?",
                (1 if body.enable else 0, pp[0]))
        # Force ONUs on this port offline if disabling (for visual fidelity)
        if not body.enable:
            conn.exec_driver_sql(
                "UPDATE onus SET status='offline', "
                "                 offline_reason='PON port admin-down' "
                "WHERE olt_id=? AND pon_port_index=?",
                (olt_id, port_index))
        # Audit alert
        conn.exec_driver_sql(
            "INSERT INTO olt_alerts "
            "(company_id,olt_id,kind,level,title,message,acked) "
            "VALUES (?,?,?,?,?,?,1)",
            (cid, olt_id, "pon_admin_change", "info",
             f"PON {port_index} {'enabled' if body.enable else 'disabled'}",
             f"By {sc['actor']}"))
    # Vendor-specific dispatch via vendor_adapters.dispatch_pon_toggle
    olt_row: Dict[str, Any] = {}
    try:
        with engine.begin() as c2:
            row = c2.exec_driver_sql(
                "SELECT * FROM olts WHERE id=? AND company_id=?",
                (olt_id, cid)).mappings().first()
            if row: olt_row = dict(row)
    except Exception: pass
    dispatch = vendor_adapters.dispatch_pon_toggle(
        olt_row, port_index, body.enable)
    msg = (f"DB flipped. Vendor dispatch [{dispatch.get('mode')}]: "
             f"{'OK' if dispatch.get('ok') else 'FAILED'} — "
             f"{(dispatch.get('details') or '')[:300]}")
    return {"ok": True, "admin_up": body.enable, "message": msg,
             "vendor_dispatch": dispatch}


class PonTxPowerIn(BaseModel):
    tx_power: float


# _v466_PON_TX_SET — set TX optical power on a PON port. Vendor-agnostic via
# vendor_adapters.dispatch_pon_set_tx (falls back to DB-only if no live SSH).
@router.post("/api/admin/olt/olts/{olt_id}/pon/{port_index}/tx-power")
def api_pon_set_tx(request: Request, olt_id: int, port_index: int,
                   body: PonTxPowerIn):
    sc = _require_scope(request); cid = sc["company_id"]
    if not (-10.0 <= body.tx_power <= 10.0):
        raise HTTPException(400, "TX power must be between -10 and +10 dBm")
    with engine.begin() as conn:
        olt = conn.exec_driver_sql(
            "SELECT id,vendor,host,name FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not olt:
            raise HTTPException(404, "OLT not found")
        pp = conn.exec_driver_sql(
            "SELECT id FROM pon_ports WHERE olt_id=? AND port_index=?",
            (olt_id, port_index)).fetchone()
        if not pp:
            conn.exec_driver_sql(
                "INSERT INTO pon_ports (olt_id,port_index,name,admin_up,tx_power) "
                "VALUES (?,?,?,1,?)",
                (olt_id, port_index, f"gpon0/{port_index}", body.tx_power))
        else:
            conn.exec_driver_sql(
                "UPDATE pon_ports SET tx_power=? WHERE id=?",
                (body.tx_power, pp[0]))
        conn.exec_driver_sql(
            "INSERT INTO olt_alerts (company_id,olt_id,kind,level,title,message,acked) "
            "VALUES (?,?,?,?,?,?,1)",
            (cid, olt_id, "pon_tx_power_change", "info",
             f"PON {port_index} TX power = {body.tx_power} dBm",
             f"By {sc['actor']}"))
    # Vendor dispatch (best-effort)
    olt_row: Dict[str, Any] = {}
    try:
        with engine.begin() as c2:
            row = c2.exec_driver_sql(
                "SELECT * FROM olts WHERE id=? AND company_id=?",
                (olt_id, cid)).mappings().first()
            if row: olt_row = dict(row)
    except Exception: pass
    dispatch = {"mode": "db_only", "ok": True, "details": "DB updated"}
    try:
        if hasattr(vendor_adapters, "dispatch_pon_set_tx"):
            dispatch = vendor_adapters.dispatch_pon_set_tx(
                olt_row, port_index, body.tx_power)
    except Exception as e:
        dispatch = {"mode": "error", "ok": False, "details": str(e)}
    msg = (f"DB updated (TX={body.tx_power} dBm). "
           f"Vendor [{dispatch.get('mode')}]: "
           f"{'OK' if dispatch.get('ok') else 'FAILED'} — "
           f"{(dispatch.get('details') or '')[:300]}")
    return {"ok": True, "tx_power": body.tx_power, "message": msg,
            "vendor_dispatch": dispatch}


# ─── Ping endpoint for the mobile app's WebView ──────────────────────────
@router.get("/api/admin/network-map/health")
def api_health(request: Request):
    return {"ok": True, "ts": int(time.time())}


# ═══ _S40l_ Phase-2 endpoints ═══════════════════════════════════════════
class FiberPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    core_count: Optional[int] = None
    polyline: Optional[List[List[float]]] = None
    src_hw_id: Optional[int] = None
    dst_hw_id: Optional[int] = None


@router.patch("/api/admin/network-map/fiber/{fid}")
def api_fiber_patch(request: Request, fid: int, body: FiberPatch):
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_fiber_ownership(request, sc, fid)   # _S40zδ_
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id FROM network_fiber WHERE id=? AND company_id=?",
            (fid, cid)).fetchone()
        if not row: raise HTTPException(404, "Fiber not found")
        sets, vals = [], []
        if body.name is not None: sets.append("name=?"); vals.append(body.name)
        if body.color is not None: sets.append("color=?"); vals.append(body.color.lower())
        if body.core_count is not None:
            sets.append("core_count=?"); vals.append(body.core_count)
        if body.src_hw_id is not None:
            sets.append("src_hw_id=?"); vals.append(body.src_hw_id)
        if body.dst_hw_id is not None:
            sets.append("dst_hw_id=?"); vals.append(body.dst_hw_id)
        if body.polyline is not None:
            if len(body.polyline) < 2:
                raise HTTPException(400, "polyline must have >=2 points")
            sets.append("polyline_json=?"); vals.append(json.dumps(body.polyline))
        if not sets: return {"ok": True, "no_changes": True}
        vals.extend([fid, cid])
        conn.exec_driver_sql(
            f"UPDATE network_fiber SET {','.join(sets)} "
            f"WHERE id=? AND company_id=?", tuple(vals))
    return {"ok": True}


class SpliceIn(BaseModel):
    node_hw_id: int
    src_fiber_id: Optional[int] = None
    src_core: Optional[int] = None
    dst_fiber_id: Optional[int] = None
    dst_core: Optional[int] = None
    mode: Optional[str] = "thru"
    loss_db: Optional[float] = None
    notes: Optional[str] = None


@router.get("/api/admin/network-map/splice/{hw_id}")
def api_splice_list(request: Request, hw_id: int):
    """_S40s_NEARBY_  Lists splices PLUS:
       (a) fibers attached to this node (src or dst) — labelled side='in'/'out'
       (b) fibers whose polyline passes within 150 m of this node — labelled
           side='nearby' so the user can pick them in the splice tray even
           though they aren't formally attached yet."""
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        node = conn.exec_driver_sql(
            "SELECT id,kind,name,props_json,lat,lng FROM network_hardware "
            "WHERE id=? AND company_id=?", (hw_id, cid)).fetchone()
        if not node: raise HTTPException(404, "Node not found")
        node_lat, node_lng = node[4], node[5]
        attached = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id,polyline_json "
            "FROM network_fiber WHERE company_id=? "
            "  AND (src_hw_id=? OR dst_hw_id=?) ORDER BY id",
            (cid, hw_id, hw_id)).fetchall()
        attached_ids = {r[0] for r in attached}
        nearby = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id,polyline_json "
            "FROM network_fiber WHERE company_id=? "
            "  AND id NOT IN (" + (",".join(str(i) for i in attached_ids) or "0") + ") "
            "ORDER BY id", (cid,)).fetchall()
        # Compute nearest-distance for each non-attached fiber
        def _hav(a, b, c, d):
            import math
            R = 6371000.0
            la1, la2 = math.radians(a), math.radians(c)
            dla = math.radians(c - a); dlo = math.radians(d - b)
            h = math.sin(dla/2)**2 + math.cos(la1)*math.cos(la2)*math.sin(dlo/2)**2
            return 2*R*math.asin(math.sqrt(h))
        nearby_filtered = []
        if node_lat is not None and node_lng is not None:
            for r in nearby:
                try: poly = json.loads(r[6] or "[]")
                except Exception: poly = []
                if not poly: continue
                d_min = min(_hav(node_lat, node_lng, p[0], p[1]) for p in poly)
                if d_min <= 150.0:
                    nearby_filtered.append((r, d_min))
        # Compose final fibers list
        fibers_out = []
        for r in attached:
            fibers_out.append({"id":r[0],"name":r[1],"color":r[2],
                                "core_count":r[3],"src_hw_id":r[4],
                                "dst_hw_id":r[5],
                                "side": "in" if r[5]==hw_id else "out"})
        for r, dmin in nearby_filtered:
            fibers_out.append({"id":r[0],"name":r[1],"color":r[2],
                                "core_count":r[3],"src_hw_id":r[4],
                                "dst_hw_id":r[5], "side": "nearby",
                                "distance_m": round(dmin, 1)})
        splices = conn.exec_driver_sql(
            "SELECT id,src_fiber_id,src_core,dst_fiber_id,dst_core,"
            "       mode,loss_db,notes,created_at "
            "FROM fiber_splice WHERE company_id=? AND node_hw_id=? "
            "ORDER BY id", (cid, hw_id)).fetchall()
        splices = conn.exec_driver_sql(
            "SELECT id,src_fiber_id,src_core,dst_fiber_id,dst_core,"
            "       mode,loss_db,notes,created_at "
            "FROM fiber_splice WHERE company_id=? AND node_hw_id=? "
            "ORDER BY id", (cid, hw_id)).fetchall()
    return {
        "ok": True,
        "node": {"id": node[0], "kind": node[1], "name": node[2],
                  "props": json.loads(node[3]) if node[3] else {}},
        "fibers": fibers_out,
        "splices": [{"id":s[0],"src_fiber_id":s[1],"src_core":s[2],
                      "dst_fiber_id":s[3],"dst_core":s[4],"mode":s[5],
                      "loss_db":s[6],"notes":s[7],"created_at":s[8]}
                     for s in splices],
    }


@router.post("/api/admin/network-map/splice")
def api_splice_create(request: Request, body: SpliceIn):
    sc = _require_scope(request); cid = sc["company_id"]; actor = sc["actor"]
    with engine.begin() as conn:
        node = conn.exec_driver_sql(
            "SELECT id FROM network_hardware WHERE id=? AND company_id=?",
            (body.node_hw_id, cid)).fetchone()
        if not node: raise HTTPException(404, "Node not found")
        r = conn.exec_driver_sql(
            "INSERT INTO fiber_splice "
            "(company_id,node_hw_id,src_fiber_id,src_core,dst_fiber_id,"
            " dst_core,mode,loss_db,notes,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, body.node_hw_id, body.src_fiber_id, body.src_core,
             body.dst_fiber_id, body.dst_core, body.mode or "thru",
             body.loss_db, body.notes, actor))
    return {"ok": True, "id": r.lastrowid}


@router.delete("/api/admin/network-map/splice/{splice_id}")
def api_splice_delete(request: Request, splice_id: int):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM fiber_splice WHERE id=? AND company_id=?",
            (splice_id, cid))
    return {"ok": True}


from fastapi import UploadFile, File


def _kind_from_props(p: dict) -> str:
    kind = (p or {}).get("kind") or (p or {}).get("type")
    if not kind: return "jc_box"
    k = str(kind).lower().replace("-", "_").replace(" ", "_")
    valid = {"olt","onu","jc_box",
             "splitter_1x2","splitter_1x4","splitter_1x8","splitter_1x16",
             "coupler_95x5","coupler_90x10","coupler_80x20",
             "coupler_70x30","coupler_60x40","coupler_50x50",
             "pole","manhole"}
    return k if k in valid else "jc_box"


def _parse_geojson(text: str) -> dict:
    data = json.loads(text)
    feats = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    return {"features": feats or []}


def _parse_kml(text: str) -> dict:
    import xml.etree.ElementTree as ET
    ns = "{http://www.opengis.net/kml/2.2}"
    root = ET.fromstring(text)
    feats = []
    for pm in root.iter(ns + "Placemark"):
        name_el = pm.find(ns + "name")
        name = name_el.text if name_el is not None else None
        props = {"name": name}
        ed = pm.find(ns + "ExtendedData")
        if ed is not None:
            for dat in ed.iter(ns + "Data"):
                key = dat.attrib.get("name")
                val_el = dat.find(ns + "value")
                if key and val_el is not None:
                    props[key] = val_el.text
        pt = pm.find(".//" + ns + "Point/" + ns + "coordinates")
        if pt is not None and pt.text:
            parts = pt.text.strip().split(",")
            lng, lat = float(parts[0]), float(parts[1])
            feats.append({"type":"Feature","properties":props,
                           "geometry":{"type":"Point",
                                       "coordinates":[lng, lat]}})
            continue
        ls = pm.find(".//" + ns + "LineString/" + ns + "coordinates")
        if ls is not None and ls.text:
            pts = []
            for token in ls.text.strip().split():
                p = token.strip().split(",")
                if len(p) >= 2: pts.append([float(p[0]), float(p[1])])
            if len(pts) >= 2:
                feats.append({"type":"Feature","properties":props,
                               "geometry":{"type":"LineString",
                                           "coordinates":pts}})
    return {"features": feats}


@router.post("/api/admin/network-map/import")
async def api_import(request: Request, file: UploadFile = File(...)):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]; actor = sc["actor"]
    blob = await file.read()
    try: text = blob.decode("utf-8", errors="replace")
    except Exception: text = ""
    fn = (file.filename or "").lower()
    if fn.endswith(".kml"):
        try: data = _parse_kml(text)
        except Exception as e: raise HTTPException(400, f"KML parse failed: {e}")
    else:
        try: data = _parse_geojson(text)
        except Exception as e: raise HTTPException(400, f"GeoJSON parse failed: {e}")
    hw_count = fb_count = 0
    with engine.begin() as conn:
        for ft in data.get("features", []):
            props = ft.get("properties") or {}
            geom = ft.get("geometry") or {}
            if geom.get("type") == "Point":
                coords = geom.get("coordinates") or [None, None]
                lng, lat = coords[0], coords[1]
                if lat is None or lng is None: continue
                kind = _kind_from_props(props)
                name = props.get("name") or kind.upper()
                conn.exec_driver_sql(
                    "INSERT INTO network_hardware "
                    "(company_id,kind,name,lat,lng,props_json,created_by) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (cid, kind, name, lat, lng,
                     json.dumps(props), actor))
                hw_count += 1
            elif geom.get("type") == "LineString":
                coords = geom.get("coordinates") or []
                pts = [[c[1], c[0]] for c in coords if len(c) >= 2]
                if len(pts) < 2: continue
                color = (props.get("color") or "blue").lower()
                cores = int(props.get("core_count") or 12)
                conn.exec_driver_sql(
                    "INSERT INTO network_fiber "
                    "(company_id,name,color,core_count,polyline_json,"
                    " created_by) "
                    "VALUES (?,?,?,?,?,?)",
                    (cid, props.get("name"), color, cores,
                     json.dumps(pts), actor))
                fb_count += 1
    return {"ok": True, "hardware_imported": hw_count,
             "fibers_imported": fb_count}



# ═══ _S40m_  Power Budget ═══════════════════════════════════════════════
def _haversine_m(a_lat, a_lng, b_lat, b_lng) -> float:
    """Great-circle distance in metres (good enough for fibre lengths)."""
    import math
    R = 6371000.0
    la1, la2 = math.radians(a_lat), math.radians(b_lat)
    dla = math.radians(b_lat - a_lat)
    dlo = math.radians(b_lng - a_lng)
    h = (math.sin(dla/2)**2
         + math.cos(la1) * math.cos(la2) * math.sin(dlo/2)**2)
    return 2 * R * math.asin(math.sqrt(h))


def _polyline_length_m(poly) -> float:
    t = 0.0
    for i in range(1, len(poly or [])):
        t += _haversine_m(poly[i-1][0], poly[i-1][1],
                           poly[i][0],   poly[i][1])
    return t


# Loss constants — overridable via OLT props in future
# _S40zm_
FIBER_LOSS_DB_PER_KM = 0.35    # SMF 1310 nm
SPLICE_LOSS_DB       = 0.20    # per fusion splice
CONNECTOR_LOSS_DB    = 0.50    # per connector (patch panel / ONU)
SPLITTER_LOSS = {2: 3.5, 4: 7.0, 8: 10.5, 16: 14.0, 32: 17.0, 64: 20.5}


@router.get("/api/admin/network-map/power-budget")
def api_power_budget(request: Request):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        # Nodes + fibers
        hw = conn.exec_driver_sql(
            "SELECT id,kind,name,lat,lng,ref_olt_id,ref_onu_id,"
            "       props_json FROM network_hardware WHERE company_id=?",
            (cid,)).fetchall()
        fbs = conn.exec_driver_sql(
            "SELECT id,color,core_count,src_hw_id,dst_hw_id,polyline_json,"
            "       length_m,props_json FROM network_fiber WHERE company_id=?",
            (cid,)).fetchall()
        # Auto-inserted OLT pins may not have a row in network_hardware
        # (returned with id = -1000 - olt_id by /items). Include them here
        # so power budget can root a graph at every OLT with a lat/lng.
        olts = conn.exec_driver_sql(
            "SELECT id,name,vendor,model,latitude,longitude "
            "FROM olts WHERE company_id=?", (cid,)).fetchall()
        # PON port TX power per OLT
        pons = conn.exec_driver_sql(
            "SELECT olt_id,port_index,tx_power FROM pon_ports "
            "WHERE olt_id IN (SELECT id FROM olts WHERE company_id=?)",
            (cid,)).fetchall()
        # Live ONU RX power
        onus = conn.exec_driver_sql(
            "SELECT id,rx_power,pon_port_index,olt_id FROM onus "
            "WHERE company_id=?", (cid,)).fetchall()

    # Build node dictionary
    nodes = {}
    existing_olt_refs = set()
    for r in hw:
        nodes[r[0]] = {"id": r[0], "kind": r[1], "name": r[2],
                        "lat": r[3], "lng": r[4],
                        "ref_olt_id": r[5], "ref_onu_id": r[6],
                        "props": (json.loads(r[7]) if r[7] else {})}
        if r[5]: existing_olt_refs.add(r[5])
    # Synthesize auto-pinned OLT nodes (negative ids, matches /items)
    for r in olts:
        if r[0] in existing_olt_refs: continue
        if r[4] is None or r[5] is None: continue
        syn_id = -1000 - r[0]
        nodes[syn_id] = {"id": syn_id, "kind": "olt", "name": r[1],
                          "lat": r[4], "lng": r[5],
                          "ref_olt_id": r[0], "ref_onu_id": None,
                          "props": {"vendor": r[2], "model": r[3], "auto": True}}

    # Adjacency: edge -> {loss_db, fiber_id, len_m}
    adj = {nid: [] for nid in nodes}
    def _fiber_loss(length_m):
        return (length_m / 1000.0) * FIBER_LOSS_DB_PER_KM
    for f in fbs:
        fid, color, cores, src, dst, poly_s, cached_len, fib_props_s = f
        # Any endpoint of the fiber may be None (dangling) — skip those
        if src is None or dst is None: continue
        if src not in nodes or dst not in nodes: continue
        try: poly = json.loads(poly_s) if poly_s else []
        except Exception: poly = []
        length_m = cached_len if (cached_len and cached_len > 0) \
                                else _polyline_length_m(poly)
        loss = _fiber_loss(length_m) + CONNECTOR_LOSS_DB  # 1 connector/splice
        # _S40zv_  Per-leg metadata for coupler-aware power routing
        try: fp_z = json.loads(fib_props_s) if fib_props_s else {}
        except Exception: fp_z = {}
        src_leg_z = fp_z.get("src_leg_idx")
        dst_leg_z = fp_z.get("dst_leg_idx")
        adj[src].append({"to": dst, "fiber": fid, "length_m": length_m,
                          "loss_db": loss,
                          "u_leg_idx": src_leg_z, "v_leg_idx": dst_leg_z})
        adj[dst].append({"to": src, "fiber": fid, "length_m": length_m,
                          "loss_db": loss,
                          "u_leg_idx": dst_leg_z, "v_leg_idx": src_leg_z})

    def _node_insertion_loss(n):
        if n["kind"] == "jc_box":
            return SPLICE_LOSS_DB
        if n["kind"].startswith("splitter_"):
            out = (n.get("props") or {}).get("output_count") \
                    or int(n["kind"].rsplit("x", 1)[-1] or 8)
            return SPLITTER_LOSS.get(out, SPLITTER_LOSS.get(8, 10.5))
        if n["kind"].startswith("coupler_"):
            # _S40zv_  Coupler transit loss is leg-dependent. Returning 0
            # here means the BFS doesn't double-count entry; the actual
            # transit loss is added in _coupler_leg_loss() when the path
            # leaves the coupler via a specific output leg.
            return 0.0
        return 0.0

    import math as _math_zv
    def _coupler_leg_loss(node, leg_idx):
        """_S40zv_  Insertion loss (dB) when traversing a coupler from
           input to output leg `leg_idx` (0=through, 1=tap typically).
           Formula: -10*log10(pct/100) + 0.5 dB excess."""
        try:
            if leg_idx is None or leg_idx < 0:
                return 0.5  # input arm — connector only
            ratio = (node.get("props") or {}).get("ratio") \
                      or node["kind"].split("_", 1)[1]
            a, b = [int(x) for x in ratio.replace(":", "x").split("x")]
            pct = a if leg_idx == 0 else (b if leg_idx == 1 else 50)
            return round(-10.0 * _math_zv.log10(pct / 100.0) + 0.5, 2)
        except Exception:
            return 3.7

    # TX power lookup: ref_olt_id -> dict[port_index -> tx_power]
    tx_lookup: Dict[int, Dict[int, Any]] = {}
    for p_olt_id, p_idx, p_tx in pons:
        tx_lookup.setdefault(p_olt_id, {})[p_idx] = p_tx
    # Default TX: most Huawei/ZTE OLTs default +5 dBm per port
    DEFAULT_OLT_TX_DBM = 5.0

    # Live RX + port index per ONU
    onu_live: Dict[int, Dict[str, Any]] = {
        r[0]: {"rx": r[1], "pon": r[2], "olt": r[3]} for r in onus}

    # BFS from every OLT node
    out_rows = []
    for olt_id, olt_node in nodes.items():
        if olt_node["kind"] != "olt": continue
        # Use OLT's first PON port TX, else default
        ref_olt = olt_node["ref_olt_id"]
        tx_map = tx_lookup.get(ref_olt, {})
        tx_tuple = next(iter(tx_map.values()), None)
        tx = tx_tuple if tx_tuple is not None else DEFAULT_OLT_TX_DBM

        # Dijkstra (priority by accumulated dB loss)
        import heapq
        pq = [(0.0, olt_id, [olt_id])]
        best: Dict[int, float] = {olt_id: 0.0}
        path_of: Dict[int, List[int]] = {olt_id: [olt_id]}
        while pq:
            loss, u, path = heapq.heappop(pq)
            if loss > best.get(u, float("inf")): continue
            for edge in adj.get(u, []):
                v = edge["to"]
                add = edge["loss_db"] + _node_insertion_loss(nodes[v])
                u_node_z = nodes.get(u, {})
                if u_node_z.get("kind", "").startswith("coupler_"):
                    add += _coupler_leg_loss(u_node_z, edge.get("u_leg_idx"))
                nl = loss + add
                if nl < best.get(v, float("inf")):
                    best[v] = nl
                    path_of[v] = path + [v]
                    heapq.heappush(pq, (nl, v, path + [v]))
        # For every ONU reachable, compute expected RX
        for nid, n in nodes.items():
            if n["kind"] != "onu": continue
            if nid not in best: continue
            exp_rx = tx - best[nid] - CONNECTOR_LOSS_DB  # ONU connector
            actual = None; delta = None
            if n.get("ref_onu_id"):
                live = onu_live.get(n["ref_onu_id"])
                if live and live.get("rx") is not None:
                    actual = float(live["rx"])
                    delta = actual - exp_rx
            out_rows.append({
                "onu_hw_id": nid, "onu_name": n["name"],
                "ref_onu_id": n.get("ref_onu_id"),
                "olt_hw_id": olt_id, "olt_name": olt_node["name"],
                "olt_tx_dbm": tx,
                "path_loss_db": round(best[nid], 2),
                "expected_rx_dbm": round(exp_rx, 2),
                "actual_rx_dbm": actual,
                "delta_db": (round(delta, 2) if delta is not None else None),
                "path_node_ids": path_of[nid],
            })
    # Per-node expected dBm (using minimum-loss path from any OLT)
    # Maintain a global best across all OLTs since multiple OLTs are rare
    # but supported.
    node_exp: Dict[int, float] = {}
    # Re-walk: find each node's best expected dBm given any OLT origin
    # We approximate with the last computed `best` for the loop's last olt;
    # if multiple OLTs exist, take the maximum (best path).
    for olt_id_iter, olt_node_iter in nodes.items():
        if olt_node_iter["kind"] != "olt": continue
        ref = olt_node_iter["ref_olt_id"]
        tx_map_iter = tx_lookup.get(ref, {})
        tx_v = next(iter(tx_map_iter.values()), None)
        tx_v = tx_v if tx_v is not None else DEFAULT_OLT_TX_DBM
        import heapq as _hq
        pq2 = [(0.0, olt_id_iter)]
        bestX = {olt_id_iter: 0.0}
        while pq2:
            loss, u = _hq.heappop(pq2)
            if loss > bestX.get(u, float("inf")): continue
            for edge in adj.get(u, []):
                v = edge["to"]
                add = edge["loss_db"] + _node_insertion_loss(nodes[v])
                u_node_z = nodes.get(u, {})
                if u_node_z.get("kind", "").startswith("coupler_"):
                    add += _coupler_leg_loss(u_node_z, edge.get("u_leg_idx"))
                nl = loss + add
                if nl < bestX.get(v, float("inf")):
                    bestX[v] = nl
                    _hq.heappush(pq2, (nl, v))
        for nid, lossv in bestX.items():
            # _S40zm_  For splitters/couplers/jc_box, expose the INPUT dBm
            # (i.e., add back this node's own insertion loss). The frontend
            # subtracts the splitter loss to render OUTPUT dBm correctly.
            n_kind = (nodes.get(nid) or {}).get("kind", "")
            insertion = _node_insertion_loss(nodes[nid]) if nid in nodes else 0
            exp_v = tx_v - lossv + insertion
            if exp_v > node_exp.get(nid, -999):
                node_exp[nid] = round(exp_v, 2)
    return {"ok": True,
             "constants": {"fiber_loss_db_per_km": FIBER_LOSS_DB_PER_KM,
                            "splice_loss_db": SPLICE_LOSS_DB,
                            "connector_loss_db": CONNECTOR_LOSS_DB,
                            "splitter_loss": SPLITTER_LOSS,
                            "default_olt_tx_dbm": DEFAULT_OLT_TX_DBM},
             "rows": out_rows,
             "node_expected_dbm": node_exp}


class PonTxIn(BaseModel):
    tx_power: float


@router.post("/api/admin/olt/olts/{olt_id}/pon/{port_index}/set-tx")
def api_set_tx_power(request: Request, olt_id: int, port_index: int,
                      body: PonTxIn):
    """Admin can manually set a PON port's TX power budget — used by the
    planning calculator until live telemetry is available."""
    sc = _require_scope(request); cid = sc["company_id"]
    if sc["role"] != "admin": raise HTTPException(403, "Admin-only")
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not row: raise HTTPException(404, "OLT not found")
        pp = conn.exec_driver_sql(
            "SELECT id FROM pon_ports WHERE olt_id=? AND port_index=?",
            (olt_id, port_index)).fetchone()
        if pp:
            conn.exec_driver_sql(
                "UPDATE pon_ports SET tx_power=? WHERE id=?",
                (body.tx_power, pp[0]))
        else:
            conn.exec_driver_sql(
                "INSERT INTO pon_ports (olt_id,port_index,name,tx_power,"
                " admin_up,oper_up) VALUES (?,?,?,?,1,1)",
                (olt_id, port_index, f"gpon0/{port_index}", body.tx_power))
    return {"ok": True, "tx_power": body.tx_power}



# ═══ _S40s_  Attach hardware on existing fiber (split + relink) ═══════
class AttachOnFiberIn(BaseModel):
    fiber_id: int
    insert_index: int = 1     # where in the polyline to split (1-based; 0 = start, len-1 = end)





# ═══ _S40zx_  Path Trace — upstream route from an ONU to its OLT ════════════
@router.get("/api/admin/network-map/onus/{hw_id}/path-trace")
def api_onu_path_trace(request: Request, hw_id: int):
    """Return the minimum-loss path from this ONU back to a serving OLT.

    Response shape:
        ok: bool
        onu: {id, name, ref_onu_id, lat, lng, actual_rx_dbm}
        olt: {id, name, tx_dbm}
        total_loss_db: float
        expected_rx_dbm: float
        delta_db: float | null
        hops: [
            {type:"node", id, kind, name, lat, lng,
             leg_in:int|null, leg_out:int|null,
             loss_db, cum_dbm},
            {type:"fiber", id, name, color, length_m,
             polyline:[[lat,lng],...], loss_db, cum_dbm},
            ...
        ]
    """
    sc = _require_scope(request); cid = sc["company_id"]
    import math, heapq, json as _json
    with engine.begin() as conn:
        # Hardware (network_hardware + auto-pinned olts)
        hw_rows = conn.exec_driver_sql(
            "SELECT id,kind,name,lat,lng,ref_olt_id,ref_onu_id,parent_id,"
            "       props_json FROM network_hardware WHERE company_id=?",
            (cid,)).fetchall()
        fb_rows = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id,"
            "       polyline_json,length_m,props_json "
            "FROM network_fiber WHERE company_id=?",
            (cid,)).fetchall()
        olt_rows = conn.exec_driver_sql(
            "SELECT id,name,vendor,model,latitude,longitude "
            "FROM olts WHERE company_id=?", (cid,)).fetchall()
        pons = conn.exec_driver_sql(
            "SELECT olt_id, port_index, tx_power FROM pon_ports "
            "WHERE olt_id IN (SELECT id FROM olts WHERE company_id=?)",
            (cid,)).fetchall()
        onu_live_rows = conn.exec_driver_sql(
            "SELECT id, rx_power FROM onus WHERE company_id=?",
            (cid,)).fetchall()

    nodes: Dict[int, dict] = {}
    for r in hw_rows:
        try: pj = _json.loads(r[8]) if r[8] else {}
        except Exception: pj = {}
        nodes[r[0]] = {"id": r[0], "kind": r[1], "name": r[2],
                        "lat": r[3], "lng": r[4],
                        "ref_olt_id": r[5], "ref_onu_id": r[6],
                        "parent_id": r[7], "props": pj}
    existing_olts = {n["ref_olt_id"] for n in nodes.values() if n["ref_olt_id"]}
    for o in olt_rows:
        if o[0] in existing_olts: continue
        if o[4] is None or o[5] is None: continue
        syn = -1000 - o[0]
        nodes[syn] = {"id": syn, "kind": "olt", "name": o[1],
                       "lat": o[4], "lng": o[5],
                       "ref_olt_id": o[0], "ref_onu_id": None,
                       "parent_id": None,
                       "props": {"vendor": o[2], "model": o[3], "auto": True}}

    if hw_id not in nodes:
        raise HTTPException(404, "ONU node not found")
    onu_node = nodes[hw_id]
    if onu_node["kind"] != "onu":
        raise HTTPException(400, "Path trace only available for ONU pins")

    # Build adjacency with leg-aware metadata
    adj: Dict[int, list] = {nid: [] for nid in nodes}
    fiber_geom: Dict[int, dict] = {}
    def _hav_m(la1, lo1, la2, lo2):
        R = 6371000.0
        rl1 = math.radians(la1); rl2 = math.radians(la2)
        d_la = math.radians(la2-la1); d_lo = math.radians(lo2-lo1)
        a = (math.sin(d_la/2)**2 + math.cos(rl1)*math.cos(rl2)
              * math.sin(d_lo/2)**2)
        return 2*R*math.asin(math.sqrt(a))
    def _poly_len(pts):
        L = 0.0
        for i in range(len(pts)-1):
            L += _hav_m(pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1])
        return L
    for r in fb_rows:
        fid, fname, fcolor, fcores, fsrc, fdst, fpoly_s, flen, fp_s = r
        if fsrc is None or fdst is None: continue
        if fsrc not in nodes or fdst not in nodes: continue
        try: poly = _json.loads(fpoly_s) if fpoly_s else []
        except Exception: poly = []
        try: fp = _json.loads(fp_s) if fp_s else {}
        except Exception: fp = {}
        length_m = flen if (flen and flen > 0) else _poly_len(poly)
        loss = (length_m / 1000.0) * FIBER_LOSS_DB_PER_KM + CONNECTOR_LOSS_DB
        fiber_geom[fid] = {"id": fid, "name": fname or f"Fiber#{fid}",
                            "color": fcolor or "blue",
                            "length_m": round(length_m, 2),
                            "polyline": poly, "loss_db": round(loss, 2)}
        adj[fsrc].append({"to": fdst, "fiber": fid, "loss_db": loss,
                          "u_leg": fp.get("src_leg_idx"),
                          "v_leg": fp.get("dst_leg_idx")})
        adj[fdst].append({"to": fsrc, "fiber": fid, "loss_db": loss,
                          "u_leg": fp.get("dst_leg_idx"),
                          "v_leg": fp.get("src_leg_idx")})

    # Coupler / splitter helpers (mirror power-budget routine)
    SPL_LOSS = SPLITTER_LOSS
    def _ins_loss(n):
        k = n.get("kind", "")
        if k == "jc_box": return SPLICE_LOSS_DB
        if k.startswith("splitter_"):
            out = (n.get("props") or {}).get("output_count") \
                    or int(k.rsplit("x", 1)[-1] or 8)
            return SPL_LOSS.get(out, SPL_LOSS.get(8, 10.5))
        return 0.0
    def _coupler_loss(n, leg_idx):
        if leg_idx is None or leg_idx < 0: return 0.5
        try:
            ratio = (n.get("props") or {}).get("ratio") \
                      or n["kind"].split("_", 1)[1]
            a, b = [int(x) for x in ratio.replace(":", "x").split("x")]
            pct = a if leg_idx == 0 else (b if leg_idx == 1 else 50)
            return round(-10.0 * math.log10(pct / 100.0) + 0.5, 2)
        except Exception:
            return 3.7

    # TX power lookup
    tx_lookup: Dict[int, Dict[int, float]] = {}
    for o, p_idx, p_tx in pons:
        tx_lookup.setdefault(o, {})[p_idx] = p_tx
    DEFAULT_TX = 5.0

    # Run Dijkstra from every OLT, keeping per-edge predecessor for path
    # reconstruction. Pick the OLT giving the lowest total loss to this ONU.
    best_total = float("inf"); best_olt = None
    best_pred: Dict[int, dict] = {}
    for nid, n in nodes.items():
        if n["kind"] != "olt": continue
        pq = [(0.0, nid)]
        loss_so_far = {nid: 0.0}
        pred: Dict[int, dict] = {nid: None}
        while pq:
            d, u = heapq.heappop(pq)
            if d > loss_so_far.get(u, float("inf")): continue
            for e in adj.get(u, []):
                v = e["to"]
                add = e["loss_db"] + _ins_loss(nodes[v])
                u_node = nodes[u]
                if u_node["kind"].startswith("coupler_"):
                    add += _coupler_loss(u_node, e.get("u_leg"))
                nd = d + add
                if nd < loss_so_far.get(v, float("inf")):
                    loss_so_far[v] = nd
                    pred[v] = {"prev": u, "edge": e}
                    heapq.heappush(pq, (nd, v))
        if hw_id in loss_so_far and loss_so_far[hw_id] < best_total:
            best_total = loss_so_far[hw_id]
            best_olt = nid
            best_pred = pred

    if best_olt is None:
        return {"ok": True, "onu": {"id": hw_id, "name": onu_node["name"],
                                       "lat": onu_node["lat"], "lng": onu_node["lng"]},
                 "error": "No upstream OLT path found",
                 "hops": []}

    # Reconstruct hop chain from OLT → ONU
    chain: List[int] = []
    cur = hw_id
    while cur is not None:
        chain.append(cur)
        p = best_pred.get(cur)
        cur = p["prev"] if p else None
    chain.reverse()  # OLT first, ONU last

    olt_node = nodes[best_olt]
    ref_olt = olt_node["ref_olt_id"]
    tx_map = tx_lookup.get(ref_olt, {})
    olt_tx = next(iter(tx_map.values()), None) or DEFAULT_TX

    onu_live = {r[0]: r[1] for r in onu_live_rows}
    actual_rx = (onu_live.get(onu_node.get("ref_onu_id"))
                  if onu_node.get("ref_onu_id") else None)
    actual_rx = float(actual_rx) if actual_rx is not None else None

    # Build hops list with running cumulative dBm
    hops: List[dict] = []
    cum = olt_tx
    # First hop: OLT itself
    hops.append({"type": "node", "id": olt_node["id"], "kind": "olt",
                  "name": olt_node["name"],
                  "lat": olt_node["lat"], "lng": olt_node["lng"],
                  "leg_in": None, "leg_out": None,
                  "loss_db": 0.0, "cum_dbm": round(cum, 2)})
    # Walk pairs — emit a fiber hop, retroactively credit the previous
    # coupler's outgoing-leg loss to its own hop, then emit the next node hop.
    for i in range(1, len(chain)):
        prev_id = chain[i-1]; cur_id = chain[i]
        prev_node = nodes[prev_id]; cur_node = nodes[cur_id]
        e = best_pred[cur_id]["edge"]
        f = fiber_geom.get(e["fiber"], {})
        fiber_loss = f.get("loss_db", 0.0)
        # _S40zx_  Attribute coupler-leg loss to the coupler hop itself.
        # The previous hop appended in  is prev_node (a node hop).
        # If prev_node is a coupler, retroactively add its outgoing-leg
        # loss so the user sees "Coupler 80:20 (leg 1, tap) -7.49 dB".
        if prev_node["kind"].startswith("coupler_") and hops and hops[-1].get("type") == "node":
            cl = _coupler_loss(prev_node, e.get("u_leg"))
            cum -= cl
            hops[-1]["loss_db"] = round(hops[-1]["loss_db"] + cl, 2)
            hops[-1]["cum_dbm"] = round(cum, 2)
            hops[-1]["leg_out"] = e.get("u_leg")
        cum -= fiber_loss
        hops.append({"type": "fiber", "id": e["fiber"],
                      "name": f.get("name", f"Fiber#{e['fiber']}"),
                      "color": f.get("color", "blue"),
                      "length_m": f.get("length_m", 0),
                      "polyline": f.get("polyline", []),
                      "loss_db": round(fiber_loss, 2),
                      "cum_dbm": round(cum, 2)})
        node_loss = _ins_loss(cur_node)
        cum -= node_loss
        hops.append({"type": "node", "id": cur_node["id"],
                      "kind": cur_node["kind"], "name": cur_node["name"],
                      "lat": cur_node["lat"], "lng": cur_node["lng"],
                      "leg_in": e.get("v_leg"), "leg_out": None,
                      "loss_db": round(node_loss, 2),
                      "cum_dbm": round(cum, 2)})
    # Final ONU connector
    onu_connector = CONNECTOR_LOSS_DB
    cum -= onu_connector
    hops[-1]["loss_db"] = round(hops[-1]["loss_db"] + onu_connector, 2)
    hops[-1]["cum_dbm"] = round(cum, 2)

    delta = (round(actual_rx - cum, 2)
              if actual_rx is not None else None)
    return {"ok": True,
             "onu": {"id": hw_id, "name": onu_node["name"],
                      "lat": onu_node["lat"], "lng": onu_node["lng"],
                      "ref_onu_id": onu_node.get("ref_onu_id"),
                      "actual_rx_dbm": actual_rx},
             "olt": {"id": olt_node["id"], "name": olt_node["name"],
                      "tx_dbm": olt_tx},
             "total_loss_db": round(best_total + onu_connector, 2),
             "expected_rx_dbm": round(cum, 2),
             "delta_db": delta,
             "hops": hops}


@router.post("/api/admin/network-map/hardware/{hw_id}/attach-on-fiber")
def api_attach_on_fiber(request: Request, hw_id: int, body: AttachOnFiberIn):
    # _S40zl_  Store leg metadata so the splitter input/output legs render
    # correctly and the disconnect popup can locate the linked fibers.
    """Split `fiber_id` at `insert_index` (or nearest segment to hw position),
       link half-1.dst = hw, half-2.src = hw. Used when a splitter or JC box
       is dropped on an existing fiber."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_hw_ownership(request, sc, hw_id)  # _S40zz_
    with engine.begin() as conn:
        hw = conn.exec_driver_sql(
            "SELECT id,kind,lat,lng FROM network_hardware "
            "WHERE id=? AND company_id=?",
            (hw_id, cid)).fetchone()
        if not hw: raise HTTPException(404, "Hardware not found")
        fb = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id,polyline_json "
            "FROM network_fiber WHERE id=? AND company_id=?",
            (body.fiber_id, cid)).fetchone()
        if not fb: raise HTTPException(404, "Fiber not found")
        try: poly = json.loads(fb[6] or "[]")
        except Exception: poly = []
        if len(poly) < 2: raise HTTPException(400, "Fiber polyline too short")
        # If insert_index out of range, clamp to a meaningful split
        idx = max(1, min(int(body.insert_index), len(poly) - 1))
        # First half: poly[:idx+1] with last point = hw lat/lng
        first_half = poly[:idx] + [[hw[2], hw[3]]]
        second_half = [[hw[2], hw[3]]] + poly[idx:]
        # _S40zl_  Determine if hw is a splitter/coupler so we mark legs
        is_split = bool(hw[1] and (hw[1].startswith("splitter_")
                                    or hw[1].startswith("coupler_")))
        # First half props: dst goes into INPUT leg (-1) of splitter
        first_props_json = (json.dumps({"dst_leg_idx": -1})
                            if is_split else None)
        # Second half props: src goes into OUTPUT leg 0 of splitter
        second_props_json = (json.dumps({"src_leg_idx": 0})
                             if is_split else None)
        # Update existing fiber to be the first half (dst = hw)
        conn.exec_driver_sql(
            "UPDATE network_fiber SET polyline_json=?, dst_hw_id=?, "
            "props_json=COALESCE(?, props_json) "
            "WHERE id=? AND company_id=?",
            (json.dumps(first_half), hw_id, first_props_json,
             body.fiber_id, cid))
        # Insert the second half as a NEW fiber (src = hw, dst = original dst)
        r = conn.exec_driver_sql(
            "INSERT INTO network_fiber "
            "(company_id,name,color,core_count,src_hw_id,dst_hw_id,"
            " polyline_json,props_json,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, fb[1], fb[2], fb[3], hw_id, fb[5],
             json.dumps(second_half), second_props_json, sc["actor"]))
        new_fid = r.lastrowid
        # _S40zt_  Audit row — best-effort, never block the cut on log failure.
        try:
            conn.exec_driver_sql(
                "INSERT INTO fiber_cut_history "
                "(company_id,node_hw_id,original_fiber_id,first_half_id,"
                " second_half_id,cut_lat,cut_lng,cut_by) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (cid, hw_id, body.fiber_id, body.fiber_id, new_fid,
                 hw[2], hw[3], sc["actor"]))
        except Exception:
            pass
    return {"ok": True, "first_half_id": body.fiber_id,
             "second_half_id": new_fid}



@router.get("/api/admin/network-map/hardware/{hw_id}/cut-history")
def api_hw_cut_history(request: Request, hw_id: int):
    """_S40zt_  Return the chronological audit trail of fiber cuts performed
       at this node (most recent first), enriched with each fiber's display
       name + color so the frontend can render labels without extra lookups."""
    sc = _require_scope(request); cid = sc["company_id"]
    out = []
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id,original_fiber_id,first_half_id,second_half_id,"
            "       cut_lat,cut_lng,cut_by,cut_at,notes "
            "FROM fiber_cut_history "
            "WHERE company_id=? AND node_hw_id=? "
            "ORDER BY id DESC LIMIT 100",
            (cid, hw_id)).fetchall()
        if not rows: return {"ok": True, "items": []}
        # One round-trip to fetch all the fiber names referenced
        ids = set()
        for r in rows:
            ids.update([r[1], r[2], r[3]])
        ids.discard(None)
        fmap = {}
        if ids:
            placeholder = ",".join("?" for _ in ids)
            frows = conn.exec_driver_sql(
                f"SELECT id,name,color,core_count "
                f"FROM network_fiber WHERE company_id=? AND id IN ({placeholder})",
                (cid, *ids)).fetchall()
            for fr in frows:
                fmap[fr[0]] = {"id": fr[0], "name": fr[1] or f"Fiber#{fr[0]}",
                                "color": fr[2] or "blue",
                                "core_count": fr[3] or 0}
        for r in rows:
            out.append({
                "id": r[0],
                "original_fiber": fmap.get(r[1], {"id": r[1], "name": f"(deleted #{r[1]})"}),
                "first_half":     fmap.get(r[2], {"id": r[2], "name": f"(deleted #{r[2]})"}),
                "second_half":    fmap.get(r[3], {"id": r[3], "name": f"(deleted #{r[3]})"}),
                "cut_lat": r[4], "cut_lng": r[5],
                "cut_by":  r[6] or "—",
                "cut_at":  r[7],
                "notes":   r[8] or "",   # _S40zu_
            })
    return {"ok": True, "items": out}



# ═══ _S40zu_  Patch a cut's notes (free-form context for field engineers) ══
class CutNotesPatch(BaseModel):
    notes: str = ""


@router.patch("/api/admin/network-map/cut-history/{cut_id}")
def api_hw_cut_history_notes(request: Request, cut_id: int,
                              body: CutNotesPatch):
    sc = _require_scope(request); cid = sc["company_id"]
    notes = (body.notes or "").strip()[:2000]   # cap at 2KB
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "UPDATE fiber_cut_history SET notes=? "
            "WHERE id=? AND company_id=?",
            (notes or None, cut_id, cid))
        if not r.rowcount:
            raise HTTPException(404, "Cut history row not found")
    return {"ok": True, "id": cut_id, "notes": notes}



# ═══ _S40t_LEG_MOVE_  Reposition fibers when a hardware leg/arm is dragged
class LegMoveIn(BaseModel):
    old_lat: float
    old_lng: float
    new_lat: float
    new_lng: float
    tolerance_m: float = 5.0


@router.post("/api/admin/network-map/hardware/{hw_id}/leg-move")
def api_leg_move(request: Request, hw_id: int, body: LegMoveIn):
    """Move every fiber polyline-endpoint that is anchored to (old_lat,old_lng)
       on `hw_id` to the new (new_lat,new_lng). Only first/last vertex of
       polylines belonging to fibers whose src or dst is this hardware are
       considered. Tolerance is in metres (default 5)."""
    sc = _require_scope(request); cid = sc["company_id"]
    import math
    R = 6371000.0
    def hav(la1, lo1, la2, lo2):
        rla1 = math.radians(la1); rla2 = math.radians(la2)
        dla = math.radians(la2 - la1); dlo = math.radians(lo2 - lo1)
        h = math.sin(dla/2)**2 + math.cos(rla1)*math.cos(rla2)*math.sin(dlo/2)**2
        return 2*R*math.asin(math.sqrt(h))
    moved = 0
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id,polyline_json,src_hw_id,dst_hw_id FROM network_fiber "
            "WHERE company_id=? AND (src_hw_id=? OR dst_hw_id=?)",
            (cid, hw_id, hw_id)).fetchall()
        for fid, poly_s, src_id, dst_id in rows:
            try: poly = json.loads(poly_s or "[]")
            except Exception: poly = []
            if len(poly) < 2: continue
            changed = False
            # Check first vertex
            if (src_id == hw_id and
                    hav(body.old_lat, body.old_lng,
                        poly[0][0], poly[0][1]) <= body.tolerance_m):
                poly[0] = [body.new_lat, body.new_lng]; changed = True
            # Check last vertex
            if (dst_id == hw_id and
                    hav(body.old_lat, body.old_lng,
                        poly[-1][0], poly[-1][1]) <= body.tolerance_m):
                poly[-1] = [body.new_lat, body.new_lng]; changed = True
            if changed:
                conn.exec_driver_sql(
                    "UPDATE network_fiber SET polyline_json=? "
                    "WHERE id=? AND company_id=?",
                    (json.dumps(poly), fid, cid))
                moved += 1
    return {"ok": True, "fibers_updated": moved}


# ═══ _S40zo_  Fiber Length Audit ═══════════════════════════════════════
@router.get("/api/admin/network-map/fiber-audit")
def api_fiber_audit(request: Request,
                     drift_pct_threshold: float = 5.0,
                     drift_m_threshold: float = 10.0):
    """Compute actual vs labeled fiber length, flag drift.

    Severity:
      - missing : labeled length_m is NULL or 0 (never labeled)
      - error   : drift > drift_pct_threshold OR > drift_m_threshold
      - warn    : drift > drift_pct_threshold/2 OR > drift_m_threshold/2
      - ok      : within tolerance
    """
    sc = _require_scope(request); cid = sc["company_id"]
    import math, json as _j
    R = 6371000.0
    def hav(la1, lo1, la2, lo2):
        rla1 = math.radians(la1); rla2 = math.radians(la2)
        dla = math.radians(la2 - la1); dlo = math.radians(lo2 - lo1)
        h = math.sin(dla/2)**2 + math.cos(rla1)*math.cos(rla2)*math.sin(dlo/2)**2
        return 2*R*math.asin(math.sqrt(h))
    rows = []
    with engine.begin() as conn:
        # Build a lookup of hardware names for src/dst columns
        hw_lookup = {}
        for hid, name, kind in conn.exec_driver_sql(
            "SELECT id, name, kind FROM network_hardware WHERE company_id=?",
            (cid,)).fetchall():
            hw_lookup[hid] = name or kind or f"HW#{hid}"
        for fid, fname, color, length_m, src_id, dst_id, poly_s in \
            conn.exec_driver_sql(
                "SELECT id, name, color, length_m, src_hw_id, dst_hw_id, "
                "polyline_json FROM network_fiber WHERE company_id=? "
                "ORDER BY id DESC", (cid,)).fetchall():
            try: poly = _j.loads(poly_s or "[]")
            except Exception: poly = []
            computed_m = 0.0
            for i in range(len(poly) - 1):
                try:
                    computed_m += hav(float(poly[i][0]), float(poly[i][1]),
                                       float(poly[i+1][0]), float(poly[i+1][1]))
                except Exception: pass
            computed_m = round(computed_m, 2)
            labeled = float(length_m) if length_m else 0.0
            drift_m = round(abs(computed_m - labeled), 2) if labeled else None
            drift_pct = round((drift_m / labeled * 100.0), 1) \
                          if (labeled and drift_m is not None) else None
            # Severity
            if not labeled:
                sev = "missing"
            elif (drift_m and drift_m > drift_m_threshold) \
                  or (drift_pct and drift_pct > drift_pct_threshold):
                sev = "error"
            elif (drift_m and drift_m > drift_m_threshold/2) \
                  or (drift_pct and drift_pct > drift_pct_threshold/2):
                sev = "warn"
            else:
                sev = "ok"
            rows.append({
                "id": fid,
                "name": fname or f"Fiber #{fid}",
                "color": color or "blue",
                "src_name": hw_lookup.get(src_id, f"HW#{src_id}" if src_id else "—"),
                "dst_name": hw_lookup.get(dst_id, f"HW#{dst_id}" if dst_id else "—"),
                "src_hw_id": src_id, "dst_hw_id": dst_id,
                "computed_m": computed_m, "labeled_m": labeled,
                "drift_m": drift_m, "drift_pct": drift_pct,
                "severity": sev,
                "midpoint": (poly[len(poly)//2] if poly else None),
            })
    counts = {"missing": 0, "error": 0, "warn": 0, "ok": 0}
    for r in rows: counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    return {"ok": True, "fibers": rows, "counts": counts,
            "thresholds": {"drift_pct": drift_pct_threshold,
                            "drift_m": drift_m_threshold}}


class FiberAuditSyncIn(BaseModel):
    fiber_ids: object = "all"   # list of ints OR the string "all"


@router.post("/api/admin/network-map/fiber-audit/sync")
def api_fiber_audit_sync(request: Request, body: FiberAuditSyncIn):
    """Update length_m to computed_m for the selected fibers."""
    sc = _require_scope(request); cid = sc["company_id"]
    import math, json as _j
    R = 6371000.0
    def hav(la1, lo1, la2, lo2):
        rla1 = math.radians(la1); rla2 = math.radians(la2)
        dla = math.radians(la2 - la1); dlo = math.radians(lo2 - lo1)
        h = math.sin(dla/2)**2 + math.cos(rla1)*math.cos(rla2)*math.sin(dlo/2)**2
        return 2*R*math.asin(math.sqrt(h))
    updated = 0
    with engine.begin() as conn:
        ids_clause = ""
        params = [cid]
        if body.fiber_ids != "all":
            ids = [int(x) for x in (body.fiber_ids or []) if str(x).isdigit()]
            if not ids:
                return {"ok": True, "updated": 0,
                         "message": "No valid fiber_ids provided"}
            ids_clause = " AND id IN (" + ",".join("?" * len(ids)) + ")"
            params.extend(ids)
        rows = conn.exec_driver_sql(
            "SELECT id, polyline_json FROM network_fiber "
            "WHERE company_id=?" + ids_clause, tuple(params)).fetchall()
        for fid, poly_s in rows:
            try: poly = _j.loads(poly_s or "[]")
            except Exception: poly = []
            cm = 0.0
            for i in range(len(poly) - 1):
                try:
                    cm += hav(float(poly[i][0]), float(poly[i][1]),
                               float(poly[i+1][0]), float(poly[i+1][1]))
                except Exception: pass
            conn.exec_driver_sql(
                "UPDATE network_fiber SET length_m=? WHERE id=? AND company_id=?",
                (round(cm, 2), fid, cid))
            updated += 1
    return {"ok": True, "updated": updated}
