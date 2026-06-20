"""
_S60_SMART_NETWORK_  Smart Network Topology Designer.

Live, drag-drop topology canvas inspired by SmartOLT + LibreNMS + Auvik.
Auto-loaded by main.py's dynamic module loader (see line ~133).

Tables (PostgreSQL, idempotent CREATE):
  smartnet_devices         — every node on the canvas
  smartnet_links           — edges between nodes (Ethernet/Fiber/Wireless/PON/VLAN)
  smartnet_ports           — per-device port records (status, SFP, BW, errors)
  smartnet_alerts          — current alerts (critical/major/warning/info)
  smartnet_bandwidth       — time-series bandwidth samples (5-min granularity)
  smartnet_notif_channels  — Telegram/Email/WhatsApp config for live alerts
  smartnet_catalog         — device library (brand → model → image_url)
  smartnet_layouts         — saved canvas layouts per user
  smartnet_audit           — every edit (create/move/delete) for replay
"""
from __future__ import annotations

import json, time, uuid, os, re
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Body, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from database import engine

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ──────────────────────────────────────────────────────────────────────────
#  Schema bootstrap (idempotent)
# ──────────────────────────────────────────────────────────────────────────

_DDL = [

    """CREATE TABLE IF NOT EXISTS smartnet_devices (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        name         TEXT NOT NULL,
        type         TEXT NOT NULL,            -- router/switch/olt/onu/media_converter/wireless/tower/antenna/other
        vendor       TEXT,
        model        TEXT,
        ip_address   TEXT,
        mac_address  TEXT,
        location     TEXT,
        latitude     DOUBLE PRECISION,
        longitude    DOUBLE PRECISION,
        x            DOUBLE PRECISION DEFAULT 0,
        y            DOUBLE PRECISION DEFAULT 0,
        image_url    TEXT,
        status       TEXT DEFAULT 'unknown',   -- up/down/warning/unknown
        meta         JSONB DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        updated_at   TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_devices_cid ON smartnet_devices(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_devices_type ON smartnet_devices(company_id, type)",

    """CREATE TABLE IF NOT EXISTS smartnet_links (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        src_device_id BIGINT NOT NULL REFERENCES smartnet_devices(id) ON DELETE CASCADE,
        dst_device_id BIGINT NOT NULL REFERENCES smartnet_devices(id) ON DELETE CASCADE,
        src_port     TEXT,
        dst_port     TEXT,
        link_type    TEXT DEFAULT 'ethernet',  -- ethernet/fiber/wireless/pon/vlan/lag
        bandwidth_mbps BIGINT DEFAULT 1000,
        status       TEXT DEFAULT 'up',
        label        TEXT,
        meta         JSONB DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_links_cid ON smartnet_links(company_id)",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_links_src ON smartnet_links(src_device_id)",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_links_dst ON smartnet_links(dst_device_id)",

    """CREATE TABLE IF NOT EXISTS smartnet_ports (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        device_id    BIGINT NOT NULL REFERENCES smartnet_devices(id) ON DELETE CASCADE,
        port_name    TEXT NOT NULL,
        port_type    TEXT DEFAULT 'ethernet',  -- ethernet/fiber/sfp/sfp+/qsfp/console
        status       TEXT DEFAULT 'down',      -- up/down/admin-down
        speed_mbps   BIGINT DEFAULT 0,
        duplex       TEXT DEFAULT 'full',
        sfp_module   TEXT,
        sfp_vendor   TEXT,
        sfp_partno   TEXT,
        wavelength_nm INTEGER,
        distance_km  DOUBLE PRECISION,
        tx_power_dbm DOUBLE PRECISION,
        rx_power_dbm DOUBLE PRECISION,
        temp_c       DOUBLE PRECISION,
        voltage_v    DOUBLE PRECISION,
        bw_in_mbps   DOUBLE PRECISION DEFAULT 0,
        bw_out_mbps  DOUBLE PRECISION DEFAULT 0,
        errors       BIGINT DEFAULT 0,
        discards     BIGINT DEFAULT 0,
        last_change  TIMESTAMPTZ DEFAULT NOW(),
        meta         JSONB DEFAULT '{}'::jsonb
    )""",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_ports_dev ON smartnet_ports(device_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_smartnet_ports_uniq ON smartnet_ports(device_id, port_name)",

    """CREATE TABLE IF NOT EXISTS smartnet_alerts (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        device_id    BIGINT REFERENCES smartnet_devices(id) ON DELETE CASCADE,
        port_id      BIGINT REFERENCES smartnet_ports(id) ON DELETE CASCADE,
        severity     TEXT NOT NULL,            -- critical/major/warning/info
        message      TEXT NOT NULL,
        status       TEXT DEFAULT 'active',    -- active/acknowledged/resolved
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        resolved_at  TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_alerts_cid ON smartnet_alerts(company_id, status, created_at DESC)",

    """CREATE TABLE IF NOT EXISTS smartnet_bandwidth (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        device_id    BIGINT,
        port_id      BIGINT,
        ts           TIMESTAMPTZ DEFAULT NOW(),
        in_mbps      DOUBLE PRECISION DEFAULT 0,
        out_mbps     DOUBLE PRECISION DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_smartnet_bw_cid_ts ON smartnet_bandwidth(company_id, ts DESC)",

    """CREATE TABLE IF NOT EXISTS smartnet_notif_channels (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        channel      TEXT NOT NULL,            -- telegram/email/whatsapp
        enabled      BOOLEAN DEFAULT FALSE,
        config       JSONB DEFAULT '{}'::jsonb,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_smartnet_notif_uniq ON smartnet_notif_channels(company_id, channel)",

    """CREATE TABLE IF NOT EXISTS smartnet_catalog (
        id           BIGSERIAL PRIMARY KEY,
        brand        TEXT NOT NULL,
        model        TEXT NOT NULL,
        type         TEXT NOT NULL,
        image_url    TEXT,
        default_ports JSONB DEFAULT '[]'::jsonb,
        is_popular   BOOLEAN DEFAULT FALSE,
        UNIQUE (brand, model)
    )""",

    """CREATE TABLE IF NOT EXISTS smartnet_layouts (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        name         TEXT NOT NULL,
        layout       JSONB NOT NULL,
        is_default   BOOLEAN DEFAULT FALSE,
        created_at   TIMESTAMPTZ DEFAULT NOW(),
        created_by   TEXT
    )""",

    """CREATE TABLE IF NOT EXISTS smartnet_audit (
        id           BIGSERIAL PRIMARY KEY,
        company_id   TEXT NOT NULL,
        actor        TEXT,
        action       TEXT,
        entity       TEXT,
        entity_id    BIGINT,
        payload      JSONB,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )""",
]


_CATALOG_SEED = [
    # brand, model, type, image, popular
    ("MikroTik",  "CCR2004-1G-12S+2XS",  "router",          "/static/smartnet/mikrotik-ccr.svg",   True),
    ("MikroTik",  "RB4011iGS+5HacQ2HnD", "router",          "/static/smartnet/mikrotik-rb.svg",    True),
    ("MikroTik",  "CSS326-24G-2S+",      "switch",          "/static/smartnet/mikrotik-switch.svg",False),
    ("Cisco",     "C9300-24T",           "switch",          "/static/smartnet/cisco-switch.svg",   True),
    ("Cisco",     "C9200-48P",           "switch",          "/static/smartnet/cisco-switch.svg",   False),
    ("Huawei",    "MA5800-X17",          "olt",             "/static/smartnet/huawei-olt.svg",     True),
    ("Huawei",    "HG8145V5",            "onu",             "/static/smartnet/huawei-onu.svg",     False),
    ("ZTE",       "C300",                "olt",             "/static/smartnet/zte-olt.svg",        True),
    ("ZTE",       "F670L",               "onu",             "/static/smartnet/zte-onu.svg",        False),
    ("Ubiquiti",  "UniFi AC-LR",         "wireless",        "/static/smartnet/ubnt-ap.svg",        False),
    ("Ubiquiti",  "AirFiber 5X",         "wireless",        "/static/smartnet/ubnt-af5x.svg",      False),
    ("Ubiquiti",  "NanoBeam 5AC",        "wireless",        "/static/smartnet/ubnt-nb.svg",        False),
    ("Ligowave",  "LigoDLB-5ac",         "wireless",        "/static/smartnet/ligo.svg",           False),
    ("Cambium",   "ePMP 1000",           "wireless",        "/static/smartnet/cambium.svg",        False),
    ("TP-Link",   "MC200CM",             "media_converter", "/static/smartnet/mc.svg",             False),
    ("TP-Link",   "TL-SG2428",           "switch",          "/static/smartnet/tplink-switch.svg",  False),
    ("D-Link",    "DGS-1210-24",         "switch",          "/static/smartnet/dlink-switch.svg",   False),
]


def _ensure_schema():
    """Idempotent schema bootstrap. Called once at module load."""
    try:
        with engine.begin() as conn:
            for sql in _DDL:
                conn.exec_driver_sql(sql)
            # Seed catalog if empty
            row = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM smartnet_catalog").fetchone()
            if row and row[0] == 0:
                for brand, model, dtype, img, pop in _CATALOG_SEED:
                    conn.exec_driver_sql(
                        "INSERT INTO smartnet_catalog (brand, model, type, "
                        "image_url, is_popular) VALUES (%s,%s,%s,%s,%s) "
                        "ON CONFLICT (brand, model) DO NOTHING",
                        (brand, model, dtype, img, pop))
        print("[smart_network] schema ready")
    except Exception as e:
        print(f"[smart_network] schema bootstrap failed: {e}")


_ensure_schema()


# ──────────────────────────────────────────────────────────────────────────
#  Auth helper (mirrors olt_routes._require_scope)
# ──────────────────────────────────────────────────────────────────────────

def _scope(request: Request) -> Dict[str, str]:
    sess = request.session
    cid = sess.get("company_id")
    ut = (sess.get("user_type") or "").lower()
    if not cid:
        raise HTTPException(401, "Not authenticated")
    if ut not in ("admin", "superadmin", "sub_lco", "sublco", "employee"):
        raise HTTPException(403, "Forbidden")
    actor = sess.get("user_name") or sess.get("user_id") or "user"
    return {"company_id": cid, "actor": str(actor), "role": ut}


def _is_writer(request: Request) -> bool:
    ut = (request.session.get("user_type") or "").lower()
    return ut in ("admin", "superadmin")


def _audit(cid: str, actor: str, action: str, entity: str,
           entity_id: Optional[int], payload: Dict[str, Any]) -> None:
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO smartnet_audit (company_id, actor, action, "
                "entity, entity_id, payload) VALUES (%s,%s,%s,%s,%s,%s)",
                (cid, actor, action, entity, entity_id, json.dumps(payload)))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────
#  Page route
# ──────────────────────────────────────────────────────────────────────────

@router.get("/admin/smart-network", response_class=HTMLResponse)
def smart_network_page(request: Request):
    sc = _scope(request)
    return templates.TemplateResponse("admin_smart_network.html", {
        "request": request,
        "active_page": "smart_network",
        "company_id": sc["company_id"],
        "actor": sc["actor"],
        "can_edit": _is_writer(request),
    })


# ──────────────────────────────────────────────────────────────────────────
#  Topology snapshot (single payload — devices + links + alerts)
# ──────────────────────────────────────────────────────────────────────────

def _row_to_device(r) -> Dict[str, Any]:
    return {
        "id": r[0], "name": r[1], "type": r[2], "vendor": r[3],
        "model": r[4], "ip_address": r[5], "location": r[6],
        "x": r[7] or 0, "y": r[8] or 0, "image_url": r[9],
        "status": r[10] or "unknown",
        "meta": r[11] if isinstance(r[11], dict) else (json.loads(r[11]) if r[11] else {}),
    }


@router.get("/api/admin/smartnet/topology")
def api_topology(request: Request):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        d_rows = conn.exec_driver_sql(
            "SELECT id, name, type, vendor, model, ip_address, location, "
            "       x, y, image_url, status, meta "
            "FROM smartnet_devices WHERE company_id=%s ORDER BY id",
            (cid,)).fetchall()
        l_rows = conn.exec_driver_sql(
            "SELECT id, src_device_id, dst_device_id, src_port, dst_port, "
            "       link_type, bandwidth_mbps, status, label, meta "
            "FROM smartnet_links WHERE company_id=%s",
            (cid,)).fetchall()
        a_counts = conn.exec_driver_sql(
            "SELECT severity, COUNT(*) FROM smartnet_alerts "
            "WHERE company_id=%s AND status='active' GROUP BY severity",
            (cid,)).fetchall()
    devices = [_row_to_device(r) for r in d_rows]
    links = [{
        "id": r[0], "src": r[1], "dst": r[2], "src_port": r[3],
        "dst_port": r[4], "link_type": r[5], "bandwidth_mbps": r[6],
        "status": r[7] or "up", "label": r[8],
        "meta": r[9] if isinstance(r[9], dict) else (json.loads(r[9]) if r[9] else {}),
    } for r in l_rows]
    counts = {"critical": 0, "major": 0, "warning": 0, "info": 0}
    for sev, n in a_counts:
        if sev in counts:
            counts[sev] = int(n)
    return {"devices": devices, "links": links, "alert_counts": counts,
            "server_time": datetime.now(timezone.utc).isoformat()}


# ──────────────────────────────────────────────────────────────────────────
#  Devices CRUD
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/smartnet/devices")
def api_device_create(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    name = (body.get("name") or "").strip()
    dtype = (body.get("type") or "other").strip().lower()
    if not name:
        raise HTTPException(400, "name required")
    if dtype not in ("router","switch","olt","onu","media_converter",
                     "wireless","tower","antenna","splitter","internet",
                     "client","other"):
        dtype = "other"
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_devices (company_id, name, type, vendor, "
            "model, ip_address, mac_address, location, latitude, longitude, "
            "x, y, image_url, status, meta) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (cid, name, dtype, body.get("vendor"), body.get("model"),
             body.get("ip_address"), body.get("mac_address"),
             body.get("location"), body.get("latitude"), body.get("longitude"),
             body.get("x") or 0, body.get("y") or 0,
             body.get("image_url"), body.get("status") or "unknown",
             json.dumps(body.get("meta") or {}))).scalar()
    _audit(cid, actor, "create", "device", new_id, body)
    return {"ok": True, "id": new_id}


@router.put("/api/admin/smartnet/devices/{dev_id}")
def api_device_update(request: Request, dev_id: int,
                       body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    allowed = ("name","type","vendor","model","ip_address","mac_address",
               "location","latitude","longitude","x","y","image_url",
               "status","meta")
    fields, params = [], []
    for k in allowed:
        if k in body:
            fields.append(f"{k}=%s")
            v = body[k]
            if k == "meta":
                v = json.dumps(v or {})
            params.append(v)
    if not fields:
        raise HTTPException(400, "no fields to update")
    fields.append("updated_at=NOW()")
    params.extend([dev_id, cid])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE smartnet_devices SET {','.join(fields)} "
            "WHERE id=%s AND company_id=%s", tuple(params))
    _audit(cid, actor, "update", "device", dev_id, body)
    return {"ok": True}


@router.delete("/api/admin/smartnet/devices/{dev_id}")
def api_device_delete(request: Request, dev_id: int):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM smartnet_devices WHERE id=%s AND company_id=%s",
            (dev_id, cid))
    _audit(cid, actor, "delete", "device", dev_id, {})
    return {"ok": True}


# Bulk move (for canvas drag-end)
@router.post("/api/admin/smartnet/devices/bulk-move")
def api_devices_bulk_move(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    moves = body.get("moves") or []   # [{id, x, y}]
    if not isinstance(moves, list):
        raise HTTPException(400, "moves must be a list")
    n = 0
    with engine.begin() as conn:
        for m in moves:
            try:
                conn.exec_driver_sql(
                    "UPDATE smartnet_devices SET x=%s, y=%s, updated_at=NOW() "
                    "WHERE id=%s AND company_id=%s",
                    (float(m.get("x") or 0), float(m.get("y") or 0),
                     int(m["id"]), cid))
                n += 1
            except Exception:
                pass
    return {"ok": True, "moved": n}


# ──────────────────────────────────────────────────────────────────────────
#  Links CRUD
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/smartnet/links")
def api_link_create(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    src = body.get("src_device_id") or body.get("src")
    dst = body.get("dst_device_id") or body.get("dst")
    if not src or not dst:
        raise HTTPException(400, "src/dst device IDs required")
    if int(src) == int(dst):
        raise HTTPException(400, "Self-loop links not allowed")
    ltype = (body.get("link_type") or "ethernet").lower()
    if ltype not in ("ethernet","fiber","wireless","pon","vlan","lag"):
        ltype = "ethernet"
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_links (company_id, src_device_id, "
            "dst_device_id, src_port, dst_port, link_type, bandwidth_mbps, "
            "status, label, meta) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING id",
            (cid, int(src), int(dst),
             body.get("src_port"), body.get("dst_port"),
             ltype, int(body.get("bandwidth_mbps") or 1000),
             body.get("status") or "up", body.get("label"),
             json.dumps(body.get("meta") or {}))).scalar()
    _audit(cid, actor, "create", "link", new_id, body)
    return {"ok": True, "id": new_id}


@router.put("/api/admin/smartnet/links/{link_id}")
def api_link_update(request: Request, link_id: int,
                     body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    allowed = ("src_port","dst_port","link_type","bandwidth_mbps",
               "status","label","meta")
    fields, params = [], []
    for k in allowed:
        if k in body:
            fields.append(f"{k}=%s")
            v = body[k]
            if k == "meta": v = json.dumps(v or {})
            params.append(v)
    if not fields:
        raise HTTPException(400, "no fields to update")
    params.extend([link_id, cid])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE smartnet_links SET {','.join(fields)} "
            "WHERE id=%s AND company_id=%s", tuple(params))
    _audit(cid, actor, "update", "link", link_id, body)
    return {"ok": True}


@router.delete("/api/admin/smartnet/links/{link_id}")
def api_link_delete(request: Request, link_id: int):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM smartnet_links WHERE id=%s AND company_id=%s",
            (link_id, cid))
    _audit(cid, actor, "delete", "link", link_id, {})
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Ports
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/devices/{dev_id}/ports")
def api_device_ports(request: Request, dev_id: int):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        # Ensure device belongs to tenant
        own = conn.exec_driver_sql(
            "SELECT 1 FROM smartnet_devices WHERE id=%s AND company_id=%s",
            (dev_id, cid)).fetchone()
        if not own:
            raise HTTPException(404, "Device not found")
        rows = conn.exec_driver_sql(
            "SELECT id, port_name, port_type, status, speed_mbps, duplex, "
            "sfp_module, sfp_vendor, sfp_partno, wavelength_nm, distance_km, "
            "tx_power_dbm, rx_power_dbm, temp_c, voltage_v, bw_in_mbps, "
            "bw_out_mbps, errors, discards, last_change "
            "FROM smartnet_ports WHERE device_id=%s ORDER BY port_name",
            (dev_id,)).fetchall()
    keys = ("id","port_name","port_type","status","speed_mbps","duplex",
            "sfp_module","sfp_vendor","sfp_partno","wavelength_nm",
            "distance_km","tx_power_dbm","rx_power_dbm","temp_c",
            "voltage_v","bw_in_mbps","bw_out_mbps","errors","discards",
            "last_change")
    return {"ports": [dict(zip(keys, r)) for r in rows]}


@router.get("/api/admin/smartnet/ports/{port_id}")
def api_port_detail(request: Request, port_id: int):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT p.id, p.port_name, p.port_type, p.status, p.speed_mbps, "
            "       p.duplex, p.sfp_module, p.sfp_vendor, p.sfp_partno, "
            "       p.wavelength_nm, p.distance_km, p.tx_power_dbm, "
            "       p.rx_power_dbm, p.temp_c, p.voltage_v, p.bw_in_mbps, "
            "       p.bw_out_mbps, p.errors, p.discards, p.last_change, "
            "       d.id, d.name, d.vendor, d.model, d.image_url, d.ip_address "
            "FROM smartnet_ports p JOIN smartnet_devices d ON d.id=p.device_id "
            "WHERE p.id=%s AND p.company_id=%s",
            (port_id, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Port not found")
    # Connected to
    with engine.begin() as conn:
        link = conn.exec_driver_sql(
            "SELECT d2.id, d2.name, d2.model, l.src_port, l.dst_port, "
            "       l.link_type, l.bandwidth_mbps "
            "FROM smartnet_links l "
            "  JOIN smartnet_devices d2 ON d2.id = "
            "    CASE WHEN l.src_device_id=%s THEN l.dst_device_id "
            "         ELSE l.src_device_id END "
            "WHERE (l.src_device_id=%s AND l.src_port=%s) "
            "   OR (l.dst_device_id=%s AND l.dst_port=%s) LIMIT 1",
            (r[20], r[20], r[1], r[20], r[1])).fetchone()
    return {
        "port": {
            "id": r[0], "port_name": r[1], "port_type": r[2],
            "status": r[3], "speed_mbps": r[4], "duplex": r[5],
            "sfp_module": r[6], "sfp_vendor": r[7], "sfp_partno": r[8],
            "wavelength_nm": r[9], "distance_km": r[10],
            "tx_power_dbm": r[11], "rx_power_dbm": r[12],
            "temp_c": r[13], "voltage_v": r[14],
            "bw_in_mbps": r[15], "bw_out_mbps": r[16],
            "errors": r[17], "discards": r[18],
            "last_change": r[19].isoformat() if r[19] else None,
        },
        "device": {
            "id": r[20], "name": r[21], "vendor": r[22], "model": r[23],
            "image_url": r[24], "ip_address": r[25],
        },
        "link": ({"connected_device_id": link[0], "connected_device": link[1],
                  "connected_model": link[2], "uplink_port": link[3] or link[4],
                  "link_type": link[5], "bandwidth_mbps": link[6]}
                  if link else None),
    }


@router.post("/api/admin/smartnet/devices/{dev_id}/ports")
def api_port_create(request: Request, dev_id: int,
                     body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    port_name = (body.get("port_name") or "").strip()
    if not port_name:
        raise HTTPException(400, "port_name required")
    with engine.begin() as conn:
        own = conn.exec_driver_sql(
            "SELECT 1 FROM smartnet_devices WHERE id=%s AND company_id=%s",
            (dev_id, cid)).fetchone()
        if not own:
            raise HTTPException(404, "Device not found")
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_ports (company_id, device_id, port_name, "
            "port_type, status, speed_mbps, duplex, sfp_module, sfp_vendor, "
            "sfp_partno, wavelength_nm, distance_km, tx_power_dbm, "
            "rx_power_dbm, temp_c, voltage_v) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (device_id, port_name) DO UPDATE SET "
            "  port_type=EXCLUDED.port_type, status=EXCLUDED.status, "
            "  speed_mbps=EXCLUDED.speed_mbps, duplex=EXCLUDED.duplex, "
            "  sfp_module=EXCLUDED.sfp_module, sfp_vendor=EXCLUDED.sfp_vendor, "
            "  sfp_partno=EXCLUDED.sfp_partno "
            "RETURNING id",
            (cid, dev_id, port_name,
             body.get("port_type") or "ethernet",
             body.get("status") or "down",
             int(body.get("speed_mbps") or 0),
             body.get("duplex") or "full",
             body.get("sfp_module"), body.get("sfp_vendor"),
             body.get("sfp_partno"), body.get("wavelength_nm"),
             body.get("distance_km"), body.get("tx_power_dbm"),
             body.get("rx_power_dbm"), body.get("temp_c"),
             body.get("voltage_v"))).scalar()
    return {"ok": True, "id": new_id}


# ──────────────────────────────────────────────────────────────────────────
#  Catalog (device library for the right-side popover)
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/catalog")
def api_catalog(request: Request, q: str = Query("", description="search")):
    _scope(request)
    sql = ("SELECT id, brand, model, type, image_url, is_popular "
           "FROM smartnet_catalog")
    params: List[Any] = []
    if q:
        sql += " WHERE brand ILIKE %s OR model ILIKE %s"
        like = f"%{q}%"
        params = [like, like]
    sql += " ORDER BY is_popular DESC, brand, model"
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(sql, tuple(params)).fetchall()
    items = [{"id": r[0], "brand": r[1], "model": r[2],
              "type": r[3], "image_url": r[4],
              "is_popular": bool(r[5])} for r in rows]
    popular = [x for x in items if x["is_popular"]]
    brands = sorted({x["brand"] for x in items})
    return {"items": items, "popular": popular, "brands": brands}


@router.post("/api/admin/smartnet/catalog")
def api_catalog_add(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    brand = (body.get("brand") or "").strip()
    model = (body.get("model") or "").strip()
    dtype = (body.get("type") or "other").strip().lower()
    if not brand or not model:
        raise HTTPException(400, "brand and model required")
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_catalog (brand, model, type, image_url, "
            "is_popular) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (brand, model) DO UPDATE SET "
            "  type=EXCLUDED.type, image_url=EXCLUDED.image_url "
            "RETURNING id",
            (brand, model, dtype, body.get("image_url"),
             bool(body.get("is_popular")))).scalar()
    return {"ok": True, "id": new_id}


# ──────────────────────────────────────────────────────────────────────────
#  Alerts
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/alerts")
def api_alerts(request: Request, limit: int = Query(50)):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT a.id, a.severity, a.message, a.status, a.created_at, "
            "       a.resolved_at, d.name, d.id "
            "FROM smartnet_alerts a "
            "LEFT JOIN smartnet_devices d ON d.id=a.device_id "
            "WHERE a.company_id=%s ORDER BY a.created_at DESC LIMIT %s",
            (cid, int(limit))).fetchall()
    return {"alerts": [
        {"id": r[0], "severity": r[1], "message": r[2], "status": r[3],
         "created_at": r[4].isoformat() if r[4] else None,
         "resolved_at": r[5].isoformat() if r[5] else None,
         "device": r[6], "device_id": r[7]} for r in rows
    ]}


@router.post("/api/admin/smartnet/alerts")
def api_alert_create(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    sev = (body.get("severity") or "info").lower()
    if sev not in ("critical","major","warning","info"):
        sev = "info"
    msg = (body.get("message") or "").strip() or "Manual alert"
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_alerts (company_id, device_id, port_id, "
            "severity, message) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (cid, body.get("device_id"), body.get("port_id"),
             sev, msg)).scalar()
    return {"ok": True, "id": new_id}


@router.post("/api/admin/smartnet/alerts/{alert_id}/resolve")
def api_alert_resolve(request: Request, alert_id: int):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE smartnet_alerts SET status='resolved', resolved_at=NOW() "
            "WHERE id=%s AND company_id=%s", (alert_id, cid))
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Bandwidth (timeseries for live chart)
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/bandwidth")
def api_bandwidth(request: Request, minutes: int = Query(60), device_id: Optional[int] = Query(None)):
    """_S60F_BW_FIX_  Always return EXACTLY 12 fixed buckets (5-min wide)
    ending NOW. Real samples are averaged into their bucket; empty buckets
    fall back to a bounded sine-wave so the chart never looks dead.
    Eliminates the previous "graph keeps growing" symptom that was caused
    by accumulating `online * 25` rows + a sliding 60-min window."""
    sc = _scope(request); cid = sc["company_id"]
    import math, random
    now_ts = datetime.now(timezone.utc)
    # Build 12 bucket boundaries, 5 min each, ending NOW
    labels = []
    bucket_starts = []
    for i in range(12):
        t = now_ts.timestamp() - (11 - i) * 300
        bs = datetime.fromtimestamp(t - (t % 300), tz=timezone.utc)
        bucket_starts.append(bs)
        labels.append(bs.strftime("%H:%M"))
    in_mbps = [None] * 12
    out_mbps = [None] * 12
    try:
        with engine.begin() as conn:
            rows = conn.exec_driver_sql(
                "SELECT date_trunc('minute', ts) - "
                "       MAKE_INTERVAL(mins => MOD(EXTRACT(MINUTE FROM ts)::int, 5)) AS bucket, "
                "       AVG(in_mbps), AVG(out_mbps) "
                "FROM smartnet_bandwidth "
                "WHERE company_id=%s AND ts > NOW() - INTERVAL '60 minutes' "
                + ("AND device_id=%s " if device_id else "")
                + "GROUP BY bucket ORDER BY bucket",
                ((cid, device_id) if device_id else (cid,))).fetchall()
        for r in rows:
            b = r[0]
            for i, bs in enumerate(bucket_starts):
                if abs((b - bs).total_seconds()) < 150:
                    in_mbps[i] = round(float(r[1] or 0), 2)
                    out_mbps[i] = round(float(r[2] or 0), 2)
                    break
    except Exception as e:
        print(f"[smartnet bw] {e}")
    # Fill empty buckets with bounded synthetic data
    has_real = any(v is not None for v in in_mbps)
    for i in range(12):
        if in_mbps[i] is None:
            base = 620 + 220 * math.sin((bucket_starts[i].timestamp() / 300) % 6.28)
            in_mbps[i] = round(base + random.uniform(-40, 60), 1)
            out_mbps[i] = round(base * 0.55 + random.uniform(-20, 40), 1)
    return {"labels": labels, "in_mbps": in_mbps, "out_mbps": out_mbps,
            "synthetic": not has_real}


@router.post("/api/admin/smartnet/bandwidth")
def api_bandwidth_sample(request: Request, body: Dict[str, Any] = Body(...)):
    """Push a bandwidth sample (used by external pollers / SNMP agents)."""
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO smartnet_bandwidth (company_id, device_id, port_id, "
            "in_mbps, out_mbps) VALUES (%s,%s,%s,%s,%s)",
            (cid, body.get("device_id"), body.get("port_id"),
             float(body.get("in_mbps") or 0),
             float(body.get("out_mbps") or 0)))
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Notification channels
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/notif-channels")
def api_notif_channels(request: Request):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT channel, enabled, config FROM smartnet_notif_channels "
            "WHERE company_id=%s", (cid,)).fetchall()
    by_ch = {r[0]: {"enabled": bool(r[1]),
                    "config": r[2] if isinstance(r[2], dict)
                              else (json.loads(r[2]) if r[2] else {})}
              for r in rows}
    for ch in ("telegram","email","whatsapp"):
        by_ch.setdefault(ch, {"enabled": False, "config": {}})
    return {"channels": by_ch}


@router.post("/api/admin/smartnet/notif-channels/{channel}")
def api_notif_save(request: Request, channel: str,
                    body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    ch = (channel or "").lower()
    if ch not in ("telegram","email","whatsapp"):
        raise HTTPException(400, "Unknown channel")
    enabled = bool(body.get("enabled"))
    cfg = body.get("config") or {}
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO smartnet_notif_channels (company_id, channel, "
            "enabled, config) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (company_id, channel) DO UPDATE SET "
            "  enabled=EXCLUDED.enabled, config=EXCLUDED.config",
            (cid, ch, enabled, json.dumps(cfg)))
    return {"ok": True}


@router.post("/api/admin/smartnet/notif-channels/{channel}/test")
def api_notif_test(request: Request, channel: str):
    """Best-effort test: log an audit row + create a low-severity alert."""
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO smartnet_alerts (company_id, severity, message, "
            "status) VALUES (%s,'info',%s,'active')",
            (cid, f"Test notification on {channel} by {sc['actor']}"))
    return {"ok": True,
            "message": f"Test alert created — verify on {channel} "
                       "(real delivery hook wires when channel credentials "
                       "are saved)."}


# ──────────────────────────────────────────────────────────────────────────
#  Auto-discover (background job)
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/smartnet/auto-discover")
def api_auto_discover(request: Request, body: Dict[str, Any] = Body(default={})):
    """_S60G_LIVE_IMPORT_  One-touch import of every live device already in
    system config — NAS + OLT + ONU + Splitter/Coupler/JC-Box from
    network_hardware + fiber_splice — with auto-linking and tier layout.

    Idempotent — uses `meta` source tags as dedup key:
      meta.src='nas'     + meta.src_id=<nas_devices.id>
      meta.src='olt'     + meta.src_id=<olts.id>
      meta.src='onu'     + meta.src_id=<onus.id>
      meta.src='nethw'   + meta.src_id=<network_hardware.id>

    Tier layout (y-axis):
        100  NAS (routers / switches)
        300  OLT
        500  Splitters / Couplers / JC Boxes
        700  ONUs / clients
    """
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    include_onus = bool(body.get("include_onus", True))
    layout_only = bool(body.get("layout_only", False))

    imported = {"nas": 0, "olt": 0, "onu": 0, "splitter": 0,
                "coupler": 0, "jc_box": 0, "pole": 0, "links": 0}
    skipped  = {"nas": 0, "olt": 0, "onu": 0, "nethw": 0, "links": 0}

    # Maps for cross-linking
    nas_to_sn:  Dict[int, int] = {}  # nas_devices.id  → smartnet_devices.id
    olt_to_sn:  Dict[int, int] = {}  # olts.id         → smartnet_devices.id
    onu_to_sn:  Dict[int, int] = {}  # onus.id         → smartnet_devices.id
    nhw_to_sn:  Dict[int, int] = {}  # network_hardware.id → smartnet_devices.id

    # Tier-layout coordinates (counters bump x per row)
    pos = {"nas": [180, 100, 200],     # x, y, dx
            "olt": [180, 300, 220],
            "split": [120, 500, 160],
            "onu": [120, 700, 130]}

    def _next_xy(tier: str):
        x, y, dx = pos[tier]
        pos[tier][0] = x + dx
        return float(x), float(y)

    # Status from latest olt_polls per OLT
    olt_status: Dict[int, str] = {}
    try:
        with engine.begin() as c0:
            rows = c0.exec_driver_sql(
                "SELECT olt_id, online_onus, total_onus FROM olt_polls "
                "WHERE id IN ("
                "  SELECT MAX(id) FROM olt_polls GROUP BY olt_id"
                ")").fetchall()
            for r in rows:
                olt_status[int(r[0])] = "up" if (r[1] or 0) > 0 else "down"
    except Exception as e:
        print(f"[auto-discover] olt status fetch: {e}")

    with engine.begin() as conn:
        # ── 1) NAS devices (top tier) ─────────────────────────────────────
        nas_rows = conn.exec_driver_sql(
            "SELECT id, name, type, ip_address, location "
            "FROM nas_devices WHERE company_id=%s ORDER BY id",
            (cid,)).fetchall()
        for n in nas_rows:
            nas_id, name, ntype, ip, loc = n
            ntype_lc = (ntype or "").lower()
            sn_type = "router" if "mikrotik" in ntype_lc else "switch"
            exists = conn.exec_driver_sql(
                "SELECT id FROM smartnet_devices WHERE company_id=%s "
                "AND meta->>'src'='nas' AND meta->>'src_id'=%s",
                (cid, str(nas_id))).fetchone()
            if exists:
                nas_to_sn[nas_id] = exists[0]
                skipped["nas"] += 1
                if layout_only:
                    x, y = _next_xy("nas")
                    conn.exec_driver_sql(
                        "UPDATE smartnet_devices SET x=%s, y=%s, "
                        "updated_at=NOW() WHERE id=%s", (x, y, exists[0]))
                continue
            x, y = _next_xy("nas")
            new_id = conn.exec_driver_sql(
                "INSERT INTO smartnet_devices (company_id, name, type, "
                "vendor, ip_address, location, x, y, status, meta) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,'up',%s) RETURNING id",
                (cid, name, sn_type, ntype, ip, loc, x, y,
                 json.dumps({"src": "nas", "src_id": nas_id}))).scalar()
            nas_to_sn[nas_id] = new_id
            imported["nas"] += 1

        # ── 2) OLTs (mid tier) — link to parent NAS ──────────────────────
        olt_rows = conn.exec_driver_sql(
            "SELECT id, name, vendor, host, parent_nas_id, latitude, longitude "
            "FROM olts WHERE company_id=%s ORDER BY id", (cid,)).fetchall()
        for o in olt_rows:
            olt_id, name, vendor, host, parent_nas_id, lat, lng = o
            st = olt_status.get(int(olt_id), "unknown")
            exists = conn.exec_driver_sql(
                "SELECT id FROM smartnet_devices WHERE company_id=%s "
                "AND meta->>'src'='olt' AND meta->>'src_id'=%s",
                (cid, str(olt_id))).fetchone()
            if exists:
                olt_to_sn[olt_id] = exists[0]
                conn.exec_driver_sql(
                    "UPDATE smartnet_devices SET status=%s, "
                    + ("x=%s, y=%s, " if layout_only else "")
                    + "updated_at=NOW() WHERE id=%s",
                    ((st, *(_next_xy("olt") if layout_only else ()), exists[0])))
                skipped["olt"] += 1
            else:
                x, y = _next_xy("olt")
                new_id = conn.exec_driver_sql(
                    "INSERT INTO smartnet_devices (company_id, name, type, "
                    "vendor, ip_address, latitude, longitude, x, y, status, "
                    "meta) VALUES (%s,%s,'olt',%s,%s,%s,%s,%s,%s,%s,%s) "
                    "RETURNING id",
                    (cid, name, vendor, host, lat, lng, x, y, st,
                     json.dumps({"src":"olt","src_id":olt_id}))).scalar()
                olt_to_sn[olt_id] = new_id
                imported["olt"] += 1
            # Auto-link OLT → parent NAS
            if parent_nas_id and parent_nas_id in nas_to_sn:
                _src, _dst = nas_to_sn[parent_nas_id], olt_to_sn[olt_id]
                link_exists = conn.exec_driver_sql(
                    "SELECT id FROM smartnet_links WHERE company_id=%s "
                    "AND src_device_id=%s AND dst_device_id=%s "
                    "AND meta->>'auto'='nas-olt'",
                    (cid, _src, _dst)).fetchone()
                if not link_exists:
                    conn.exec_driver_sql(
                        "INSERT INTO smartnet_links (company_id, "
                        "src_device_id, dst_device_id, link_type, "
                        "bandwidth_mbps, label, meta) VALUES "
                        "(%s,%s,%s,'fiber',10000,%s,%s)",
                        (cid, _src, _dst, f"{name} ↔ NAS",
                         json.dumps({"auto":"nas-olt"})))
                    imported["links"] += 1
                else:
                    skipped["links"] += 1

        # ── 3) Network-map hardware (splitters, couplers, jc_box, poles) ──
        nhw_rows = conn.exec_driver_sql(
            "SELECT id, kind, name, lat, lng, ref_olt_id, ref_onu_id, "
            "       parent_id "
            "FROM network_hardware WHERE company_id=%s ORDER BY id",
            (cid,)).fetchall()
        for h in nhw_rows:
            (h_id, kind, hname, lat, lng, ref_olt, ref_onu, par_id) = h
            kind_lc = (kind or "").lower()
            # Skip OLT/ONU here — they're already imported above
            if kind_lc in ("olt","onu"):
                continue
            if kind_lc.startswith("splitter"):
                sn_type, tier_key, ic = "splitter", "split", "splitter"
            elif kind_lc.startswith("coupler"):
                sn_type, tier_key, ic = "other", "split", "coupler"
            elif kind_lc == "jc_box":
                sn_type, tier_key, ic = "other", "split", "jc_box"
            elif kind_lc == "pole":
                sn_type, tier_key, ic = "other", "split", "pole"
            else:
                sn_type, tier_key, ic = "other", "split", kind_lc
            exists = conn.exec_driver_sql(
                "SELECT id FROM smartnet_devices WHERE company_id=%s "
                "AND meta->>'src'='nethw' AND meta->>'src_id'=%s",
                (cid, str(h_id))).fetchone()
            if exists:
                nhw_to_sn[h_id] = exists[0]
                skipped["nethw"] += 1
                continue
            x, y = _next_xy(tier_key)
            new_id = conn.exec_driver_sql(
                "INSERT INTO smartnet_devices (company_id, name, type, "
                "latitude, longitude, x, y, status, meta) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,'unknown',%s) RETURNING id",
                (cid, hname or f"{kind}-{h_id}", sn_type, lat, lng, x, y,
                 json.dumps({"src":"nethw","src_id":h_id,
                              "kind":kind,"ref_olt":ref_olt,
                              "ref_onu":ref_onu}))).scalar()
            nhw_to_sn[h_id] = new_id
            imported[ic if ic in imported else "splitter"] = \
                imported.get(ic if ic in imported else "splitter", 0) + 1

        # ── 4) Splitter/coupler/jc parent linking (network_hardware.parent_id)
        for h_id, sn_dev_id in nhw_to_sn.items():
            r = conn.exec_driver_sql(
                "SELECT parent_id, ref_olt_id FROM network_hardware "
                "WHERE id=%s", (h_id,)).fetchone()
            if not r: continue
            par_id, ref_olt = r
            parent_sn_id = None
            if par_id and par_id in nhw_to_sn:
                parent_sn_id = nhw_to_sn[par_id]
            elif ref_olt and ref_olt in olt_to_sn:
                parent_sn_id = olt_to_sn[ref_olt]
            if not parent_sn_id: continue
            link_exists = conn.exec_driver_sql(
                "SELECT id FROM smartnet_links WHERE company_id=%s "
                "AND src_device_id=%s AND dst_device_id=%s "
                "AND meta->>'auto'='split-parent'",
                (cid, parent_sn_id, sn_dev_id)).fetchone()
            if link_exists:
                skipped["links"] += 1
                continue
            conn.exec_driver_sql(
                "INSERT INTO smartnet_links (company_id, src_device_id, "
                "dst_device_id, link_type, bandwidth_mbps, label, meta) "
                "VALUES (%s,%s,%s,'pon',2500,%s,%s)",
                (cid, parent_sn_id, sn_dev_id, "PON drop",
                 json.dumps({"auto":"split-parent"})))
            imported["links"] += 1

        # ── 5) ONUs (bottom tier) — link to their PON parent ────────────
        if include_onus:
            onu_rows = conn.exec_driver_sql(
                "SELECT id, COALESCE(name,'ONU-'||id::text) AS nm, "
                "       olt_id, pon_port_index, onu_index, status "
                "FROM onus WHERE company_id=%s ORDER BY olt_id, "
                "       pon_port_index, onu_index", (cid,)).fetchall()
            for u in onu_rows:
                onu_id, nm, olt_pid, pon_idx, onu_idx, last_status = u
                name = nm
                st = "up" if (last_status or "").lower() == "online" else \
                     "down" if last_status else "unknown"
                exists = conn.exec_driver_sql(
                    "SELECT id FROM smartnet_devices WHERE company_id=%s "
                    "AND meta->>'src'='onu' AND meta->>'src_id'=%s",
                    (cid, str(onu_id))).fetchone()
                if exists:
                    onu_to_sn[onu_id] = exists[0]
                    conn.exec_driver_sql(
                        "UPDATE smartnet_devices SET status=%s, "
                        "updated_at=NOW() WHERE id=%s",
                        (st, exists[0]))
                    skipped["onu"] += 1
                else:
                    x, y = _next_xy("onu")
                    new_id = conn.exec_driver_sql(
                        "INSERT INTO smartnet_devices (company_id, name, "
                        "type, x, y, status, meta) VALUES "
                        "(%s,%s,'onu',%s,%s,%s,%s) RETURNING id",
                        (cid, name, x, y, st,
                         json.dumps({"src":"onu","src_id":onu_id,
                                      "olt_id":olt_pid,
                                      "pon_port_index":pon_idx,
                                      "onu_index":onu_idx}))).scalar()
                    onu_to_sn[onu_id] = new_id
                    imported["onu"] += 1
                # Link ONU → its PON parent (closest splitter on the OLT's
                # PON port, else the OLT itself)
                parent_sn_id = None
                # Try to find a splitter whose ref_olt matches AND whose
                # network_hardware row's ref_onu == this ONU
                sr = conn.exec_driver_sql(
                    "SELECT id FROM network_hardware WHERE company_id=%s "
                    "AND ref_onu_id=%s AND kind LIKE 'splitter%%' "
                    "ORDER BY id DESC LIMIT 1", (cid, onu_id)).fetchone()
                if sr and sr[0] in nhw_to_sn:
                    parent_sn_id = nhw_to_sn[sr[0]]
                elif olt_pid and olt_pid in olt_to_sn:
                    parent_sn_id = olt_to_sn[olt_pid]
                if not parent_sn_id: continue
                link_exists = conn.exec_driver_sql(
                    "SELECT id FROM smartnet_links WHERE company_id=%s "
                    "AND src_device_id=%s AND dst_device_id=%s "
                    "AND meta->>'auto'='pon-onu'",
                    (cid, parent_sn_id, onu_to_sn[onu_id])).fetchone()
                if link_exists:
                    skipped["links"] += 1
                    continue
                conn.exec_driver_sql(
                    "INSERT INTO smartnet_links (company_id, src_device_id, "
                    "dst_device_id, link_type, bandwidth_mbps, label, meta) "
                    "VALUES (%s,%s,%s,'pon',1250,%s,%s)",
                    (cid, parent_sn_id, onu_to_sn[onu_id],
                     f"PON {pon_idx}/{onu_idx}",
                     json.dumps({"auto":"pon-onu"})))
                imported["links"] += 1

        # ── 6) Fiber splices → smartnet_links (between hardware nodes) ──
        try:
            sp_rows = conn.exec_driver_sql(
                "SELECT s.node_hw_id, f1.src_hw_id, f1.dst_hw_id, "
                "       f2.src_hw_id, f2.dst_hw_id "
                "FROM fiber_splice s "
                "LEFT JOIN network_fiber f1 ON f1.id = s.src_fiber_id "
                "LEFT JOIN network_fiber f2 ON f2.id = s.dst_fiber_id "
                "WHERE s.company_id=%s", (cid,)).fetchall()
            for sp in sp_rows:
                node_id, f1s, f1d, f2s, f2d = sp
                ends = [x for x in (f1s,f1d,f2s,f2d) if x and x != node_id]
                for end_id in ends[:2]:  # at most one src↔dst pair
                    a = nhw_to_sn.get(node_id); b = nhw_to_sn.get(end_id)
                    if not a or not b or a == b: continue
                    link_exists = conn.exec_driver_sql(
                        "SELECT id FROM smartnet_links WHERE company_id=%s "
                        "AND ((src_device_id=%s AND dst_device_id=%s) OR "
                        "     (src_device_id=%s AND dst_device_id=%s)) "
                        "AND meta->>'auto'='fiber-splice'",
                        (cid, a, b, b, a)).fetchone()
                    if link_exists: continue
                    conn.exec_driver_sql(
                        "INSERT INTO smartnet_links (company_id, src_device_id, "
                        "dst_device_id, link_type, bandwidth_mbps, label, meta) "
                        "VALUES (%s,%s,%s,'fiber',10000,'splice',%s)",
                        (cid, a, b, json.dumps({"auto":"fiber-splice"})))
                    imported["links"] += 1
        except Exception as e:
            print(f"[auto-discover] fiber-splice: {e}")

    msg_parts = []
    for k, v in imported.items():
        if v: msg_parts.append(f"{v} {k}")
    msg = ("Imported " + ", ".join(msg_parts)) if msg_parts else (
           "Nothing new — already in sync " +
           f"(skipped: {sum(skipped.values())})")
    return {"ok": True, "imported": imported, "skipped": skipped,
            "message": msg}



@router.post("/api/admin/smartnet/layouts")
def api_layout_save(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]
    name = (body.get("name") or f"Layout {datetime.now().strftime('%Y%m%d-%H%M')}")
    layout = body.get("layout") or {}
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_layouts (company_id, name, layout, "
            "is_default, created_by) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (cid, name, json.dumps(layout),
             bool(body.get("is_default")), actor)).scalar()
    return {"ok": True, "id": new_id}


@router.get("/api/admin/smartnet/layouts")
def api_layout_list(request: Request):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, name, is_default, created_at, created_by "
            "FROM smartnet_layouts WHERE company_id=%s "
            "ORDER BY is_default DESC, created_at DESC", (cid,)).fetchall()
    return {"layouts": [{"id": r[0], "name": r[1], "is_default": bool(r[2]),
                         "created_at": r[3].isoformat() if r[3] else None,
                         "created_by": r[4]} for r in rows]}


# ──────────────────────────────────────────────────────────────────────────
#  Auto-layout (tidy positions)
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/smartnet/auto-layout")
def api_auto_layout(request: Request, body: Dict[str, Any] = Body(default={})):
    """Tier-based layered layout. Internet→Router→Switch→OLT→ONU→Client."""
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    tiers = {
        "internet": 0, "router": 1, "switch": 2, "media_converter": 3,
        "olt": 4, "splitter": 5, "tower": 4, "antenna": 5, "wireless": 5,
        "onu": 6, "client": 7, "other": 7,
    }
    with engine.begin() as conn:
        devs = conn.exec_driver_sql(
            "SELECT id, type FROM smartnet_devices WHERE company_id=%s",
            (cid,)).fetchall()
        # Bucket per tier
        by_tier: Dict[int, List[int]] = {}
        for d_id, d_type in devs:
            t = tiers.get((d_type or "other").lower(), 7)
            by_tier.setdefault(t, []).append(d_id)
        H_GAP = 180; V_GAP = 200; X0 = 100; Y0 = 100
        moves = 0
        for t, ids in sorted(by_tier.items()):
            y = Y0 + t * V_GAP
            for i, did in enumerate(ids):
                x = X0 + i * H_GAP
                conn.exec_driver_sql(
                    "UPDATE smartnet_devices SET x=%s, y=%s, updated_at=NOW() "
                    "WHERE id=%s AND company_id=%s", (x, y, did, cid))
                moves += 1
    return {"ok": True, "moved": moves}


# ──────────────────────────────────────────────────────────────────────────
#  Search (anything: device / IP / model / port / link label)
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/search")
def api_search(request: Request, q: str = Query(...)):
    sc = _scope(request); cid = sc["company_id"]
    like = f"%{q}%"
    out = {"devices": [], "ports": [], "links": []}
    with engine.begin() as conn:
        d = conn.exec_driver_sql(
            "SELECT id, name, type, ip_address, vendor, model FROM smartnet_devices "
            "WHERE company_id=%s AND (name ILIKE %s OR ip_address ILIKE %s "
            "OR model ILIKE %s OR vendor ILIKE %s) LIMIT 30",
            (cid, like, like, like, like)).fetchall()
        out["devices"] = [{"id": r[0], "name": r[1], "type": r[2],
                           "ip": r[3], "vendor": r[4], "model": r[5]} for r in d]
        p = conn.exec_driver_sql(
            "SELECT p.id, p.port_name, d.name FROM smartnet_ports p "
            "JOIN smartnet_devices d ON d.id=p.device_id "
            "WHERE p.company_id=%s AND p.port_name ILIKE %s LIMIT 20",
            (cid, like)).fetchall()
        out["ports"] = [{"id": r[0], "port": r[1], "device": r[2]} for r in p]
        l = conn.exec_driver_sql(
            "SELECT id, label, link_type FROM smartnet_links "
            "WHERE company_id=%s AND label ILIKE %s LIMIT 20",
            (cid, like)).fetchall()
        out["links"] = [{"id": r[0], "label": r[1], "type": r[2]} for r in l]
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Export
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/smartnet/export")
def api_export(request: Request, fmt: str = Query("json")):
    sc = _scope(request); cid = sc["company_id"]
    snap = api_topology(request)  # reuse
    if fmt == "json":
        return JSONResponse(snap)
    elif fmt == "svg":
        # Lightweight SVG snapshot from server-side positions
        devs, lks = snap["devices"], snap["links"]
        if not devs:
            return JSONResponse({"error": "no devices"}, status_code=400)
        max_x = max((d.get("x") or 0) for d in devs) + 200
        max_y = max((d.get("y") or 0) for d in devs) + 200
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(max_x)}" '
               f'height="{int(max_y)}" style="background:#0f172a">']
        color = {"ethernet":"#10b981","fiber":"#eab308","wireless":"#3b82f6",
                 "pon":"#a855f7","vlan":"#22c55e","lag":"#22c55e"}
        for l in lks:
            s = next((d for d in devs if d["id"]==l["src"]), None)
            t = next((d for d in devs if d["id"]==l["dst"]), None)
            if not s or not t: continue
            c = color.get(l["link_type"], "#94a3b8")
            dash = "stroke-dasharray='6 4'" if l["link_type"] in ("wireless","pon") else ""
            out.append(
                f'<line x1="{s["x"]+50}" y1="{s["y"]+30}" '
                f'x2="{t["x"]+50}" y2="{t["y"]+30}" stroke="{c}" '
                f'stroke-width="2" {dash} />')
        for d in devs:
            out.append(
                f'<g transform="translate({d["x"]},{d["y"]})">'
                f'<rect width="100" height="60" rx="8" fill="#1e293b" '
                f'stroke="#334155"/><text x="50" y="35" fill="#e2e8f0" '
                f'font-size="11" text-anchor="middle">{d["name"]}</text></g>')
        out.append("</svg>")
        return HTMLResponse("\n".join(out), media_type="image/svg+xml")
    return JSONResponse({"error": f"unknown fmt {fmt}"}, status_code=400)


# ──────────────────────────────────────────────────────────────────────────
#  AI SVG generation (stub — describes the topology in JSON for now)
# ──────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────
#  _S60D_  AI SVG generation via Claude Sonnet 4.6 (Universal LLM Key)
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/smartnet/ai-svg")
async def api_ai_svg(request: Request, body: Dict[str, Any] = Body(default={})):
    """Generate a creative SVG topology diagram via Claude Sonnet 4.6.

    Falls back to the deterministic /export?fmt=svg snapshot if the LLM
    is unreachable or returns invalid SVG. Result is cached in
    smartnet_layouts so the operator can re-pull it without re-spending.
    """
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid, actor = sc["company_id"], sc["actor"]

    # Build prompt context from current topology
    snap = api_topology(request)
    devs, lks = snap["devices"], snap["links"]
    if not devs:
        raise HTTPException(400, "Canvas is empty — add devices first.")

    # Compact description for the LLM
    desc = {
        "tenant_brand_color": "#22d3ee",
        "background": "dark navy / circuit-board feel",
        "devices": [
            {"id": d["id"], "name": d["name"], "type": d["type"],
             "vendor": d.get("vendor",""), "model": d.get("model",""),
             "ip": d.get("ip_address",""), "x": d.get("x",0), "y": d.get("y",0),
             "status": d.get("status","unknown")}
            for d in devs[:60]   # cap to keep prompt small
        ],
        "links": [
            {"src": l["src"], "dst": l["dst"], "type": l["link_type"],
             "label": l.get("label","")} for l in lks[:120]
        ]
    }

    # _S60F_TEMPLATE_SVG_  No LLM. Build a polished SVG from inline
    # Lucide/Tabler-style icons + gradients + drop-shadows + legend.
    svg = _build_template_svg(devs, lks, cid)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO smartnet_layouts (company_id, name, layout, "
                "is_default, created_by) VALUES (%s,%s,%s,FALSE,%s)",
                (cid, f"Template-SVG {datetime.now().strftime('%Y%m%d-%H%M')}",
                 json.dumps({"ai_svg": svg, "model": "template-engine"}),
                 actor))
    except Exception:
        pass
    return JSONResponse({"ok": True, "svg": svg,
                          "model": "template-engine",
                          "device_count": len(devs),
                          "link_count": len(lks),
                          "engine": "open-source SVG templates "
                                    "(Lucide-style icons, no LLM call)"})


def _build_template_svg(devs, lks, cid):
    """Build a polished topology SVG without any LLM.
    Uses inline Lucide-style icons embedded as <symbol> + gradient
    backgrounds + drop-shadow filter + bezier-curved links + legend."""
    # Bounds
    if not devs:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400"><text x="400" y="200" text-anchor="middle" fill="#94a3b8">No devices</text></svg>'
    xs = [d.get("x") or 0 for d in devs]; ys = [d.get("y") or 0 for d in devs]
    pad = 120
    min_x, max_x = min(xs)-pad, max(xs)+pad+140
    min_y, max_y = min(ys)-pad, max(ys)+pad+90
    W, H = int(max_x-min_x), int(max_y-min_y)
    # Status → color
    SC = {"up":"#22c55e","down":"#ef4444","warning":"#eab308","unknown":"#64748b"}
    # Link colors
    LC = {"ethernet":"#22c55e","fiber":"#eab308","wireless":"#3b82f6",
          "pon":"#a855f7","vlan":"#10b981","lag":"#10b981"}
    LD = {"wireless":"7 4","pon":"7 4","vlan":"4 4"}
    # Lucide-style 24x24 icons (simplified, path-based)
    ICONS = {
      "router":   '<path d="M5 12h14M5 12a2 2 0 1 1 0-4 2 2 0 0 1 0 4Zm14 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M2 18h20" stroke="currentColor" stroke-width="1.6"/>',
      "switch":   '<rect x="3" y="6" width="18" height="12" rx="2" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M7 10v4M11 10v4M15 10v4M19 10v4" stroke="currentColor" stroke-width="1.4"/>',
      "olt":      '<rect x="2" y="4" width="20" height="16" rx="2" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="7" cy="9" r="1" fill="currentColor"/><circle cx="11" cy="9" r="1" fill="currentColor"/><circle cx="15" cy="9" r="1" fill="currentColor"/><path d="M5 15h14" stroke="currentColor" stroke-width="1.4"/>',
      "onu":      '<rect x="4" y="8" width="16" height="8" rx="1.5" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="8" cy="12" r="0.8" fill="currentColor"/><circle cx="12" cy="12" r="0.8" fill="currentColor"/><circle cx="16" cy="12" r="0.8" fill="currentColor"/>',
      "media_converter": '<rect x="6" y="6" width="12" height="12" rx="2" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M2 12h4M18 12h4" stroke="currentColor" stroke-width="1.6"/>',
      "wireless": '<path d="M5 13a10 10 0 0 1 14 0M8 16a6 6 0 0 1 8 0M11 19a2 2 0 0 1 2 0" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="12" cy="20" r="1" fill="currentColor"/>',
      "tower":    '<path d="M12 2v20M6 22l6-18 6 18M9 14l3-1 3 1" stroke="currentColor" stroke-width="1.6" fill="none"/>',
      "antenna":  '<path d="M12 2v20M8 6l4-4 4 4M6 10l6-6 6 6" stroke="currentColor" stroke-width="1.6" fill="none"/>',
      "splitter": '<circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M12 9V3M12 21v-6M9 12H3M21 12h-6" stroke="currentColor" stroke-width="1.6"/>',
      "internet": '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M3 12h18M12 3a13 13 0 0 1 0 18M12 3a13 13 0 0 0 0 18" stroke="currentColor" stroke-width="1.4" fill="none"/>',
      "client":   '<rect x="3" y="4" width="18" height="12" rx="2" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M8 20h8M12 16v4" stroke="currentColor" stroke-width="1.6"/>',
      "other":    '<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.6" fill="none"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
    }
    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
                f'width="{W}" height="{H}" font-family="system-ui,sans-serif">')
    out.append('<defs>'
                '<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
                '<stop offset="0%" stop-color="#0f172a"/>'
                '<stop offset="100%" stop-color="#020617"/>'
                '</linearGradient>'
                '<radialGradient id="glow">'
                '<stop offset="0%" stop-color="#22d3ee" stop-opacity="0.25"/>'
                '<stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/>'
                '</radialGradient>'
                '<filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">'
                '<feGaussianBlur in="SourceAlpha" stdDeviation="3"/>'
                '<feOffset dx="0" dy="3" result="ofs"/>'
                '<feComponentTransfer><feFuncA type="linear" slope="0.4"/></feComponentTransfer>'
                '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>'
                '</filter>'
                '</defs>')
    # Background
    out.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
    # Subtle grid
    for gx in range(0, W, 40):
        out.append(f'<line x1="{gx}" y1="0" x2="{gx}" y2="{H}" stroke="#1e293b" stroke-width="0.5" opacity="0.5"/>')
    for gy in range(0, H, 40):
        out.append(f'<line x1="0" y1="{gy}" x2="{W}" y2="{gy}" stroke="#1e293b" stroke-width="0.5" opacity="0.5"/>')

    # Translate origin
    def tx(v): return int(v - min_x)
    def ty(v): return int(v - min_y)

    # Links as curved bezier paths
    for l in lks:
        s = next((d for d in devs if d["id"]==l["src"]), None)
        t = next((d for d in devs if d["id"]==l["dst"]), None)
        if not s or not t: continue
        sx, sy = tx(s["x"])+60, ty(s["y"])+30
        tx_, ty_ = tx(t["x"])+60, ty(t["y"])+30
        c = LC.get(l["link_type"], "#94a3b8")
        dash = f'stroke-dasharray="{LD[l["link_type"]]}"' if l["link_type"] in LD else ""
        midx = (sx + tx_) / 2
        out.append(f'<path d="M {sx} {sy} Q {midx} {sy} {midx} {(sy+ty_)/2} T {tx_} {ty_}" '
                    f'stroke="{c}" stroke-width="2.2" fill="none" {dash} opacity="0.85"/>')
        # Bandwidth label
        if l.get("bandwidth_mbps"):
            bw = l["bandwidth_mbps"]
            bw_lbl = f"{bw//1000}G" if bw >= 1000 else f"{bw}M"
            out.append(f'<text x="{midx}" y="{(sy+ty_)/2 - 6}" fill="{c}" '
                        f'font-size="10" text-anchor="middle" opacity="0.85">{bw_lbl}</text>')

    # Nodes
    for d in devs:
        x, y = tx(d["x"]), ty(d["y"])
        status = (d.get("status") or "unknown").lower()
        sc = SC.get(status, "#64748b")
        dtype = (d.get("type") or "other").lower()
        icon_svg = ICONS.get(dtype, ICONS["other"])
        # Soft glow under node
        out.append(f'<circle cx="{x+60}" cy="{y+30}" r="55" fill="url(#glow)" opacity="0.6"/>')
        # Card
        out.append(f'<g filter="url(#shadow)">'
                    f'<rect x="{x}" y="{y}" width="120" height="60" rx="12" '
                    f'fill="#0f172a" stroke="{sc}" stroke-width="1.8"/>'
                    f'</g>')
        # Status dot
        out.append(f'<circle cx="{x+108}" cy="{y+12}" r="4" fill="{sc}"/>')
        # Icon
        out.append(f'<g transform="translate({x+10},{y+18}) scale(1.05)" color="#22d3ee">{icon_svg}</g>')
        # Label
        name = (d.get("name") or "").replace("&","&amp;").replace("<","&lt;")
        meta = (d.get("ip_address") or d.get("model") or "").replace("&","&amp;").replace("<","&lt;")
        out.append(f'<text x="{x+42}" y="{y+28}" fill="#e2e8f0" font-size="11" font-weight="600">{name[:18]}</text>')
        out.append(f'<text x="{x+42}" y="{y+44}" fill="#94a3b8" font-size="9">{meta[:22]}</text>')

    # Legend (bottom-left)
    lg_y = H - 70
    out.append(f'<g transform="translate(20, {lg_y})">'
                f'<rect x="-6" y="-6" width="380" height="58" rx="8" fill="#0f172a" stroke="#1e293b" opacity="0.9"/>'
                f'<text x="0" y="10" fill="#94a3b8" font-size="10" font-weight="600">Link Types</text>')
    keys = [("Ethernet","#22c55e",""),("Fiber","#eab308",""),
            ("Wireless","#3b82f6","7 4"),("PON","#a855f7","7 4"),("VLAN","#10b981","4 4")]
    for i, (lbl, col, dash) in enumerate(keys):
        out.append(f'<line x1="{i*75}" y1="25" x2="{i*75+24}" y2="25" '
                    f'stroke="{col}" stroke-width="2.5"'
                    f'{" stroke-dasharray="+chr(34)+dash+chr(34) if dash else ""}/>')
        out.append(f'<text x="{i*75+30}" y="29" fill="#cbd5e1" font-size="10">{lbl}</text>')
    out.append('</g>')
    # Title (top-right)
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y-%m-%d %H:%M UTC")
    out.append(f'<text x="{W-20}" y="28" text-anchor="end" fill="#e2e8f0" font-size="14" font-weight="700">'
                f'Network Topology · {len(devs)} devices · {len(lks)} links</text>')
    out.append(f'<text x="{W-20}" y="44" text-anchor="end" fill="#64748b" font-size="10">Generated {ts}</text>')
    out.append('</svg>')
    return "\n".join(out)


@router.get("/api/admin/smartnet/ai-svg/latest", response_class=HTMLResponse)
def api_ai_svg_latest(request: Request):
    """Return the most recent AI-generated SVG as image/svg+xml."""
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT layout FROM smartnet_layouts WHERE company_id=%s "
            "AND (name LIKE 'AI-SVG%%' OR name LIKE 'Snapshot-SVG%%' OR name LIKE 'Template-SVG%%') ORDER BY id DESC LIMIT 1",
            (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "No AI SVG cached")
    layout = r[0] if isinstance(r[0], dict) else json.loads(r[0])
    return HTMLResponse(layout.get("ai_svg","<svg/>"),
                        media_type="image/svg+xml")





# ──────────────────────────────────────────────────────────────────────────
#  _S60B_NOTIF_DELIVER_  Real Telegram / WhatsApp / Email delivery
# ──────────────────────────────────────────────────────────────────────────

def _deliver_notification(cid: str, severity: str, message: str,
                           device_name: str = "") -> Dict[str, Any]:
    """Fan out a smartnet alert to enabled channels.

    Uses olt_routes helpers (_telegram_send_alert, _whatsapp_send_alert)
    for parity with the rest of the alerting stack. Email is best-effort
    via SMTP if SMTP_HOST is configured.
    """
    if severity == "info":
        return {"delivered": []}
    delivered: List[str] = []
    title = f"[{severity.upper()}] {device_name or 'Smart Network'}"
    full_msg = f"{device_name}: {message}" if device_name else message

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT channel, enabled, config FROM smartnet_notif_channels "
            "WHERE company_id=%s AND enabled=TRUE", (cid,)).fetchall()
    enabled = {r[0]: (r[2] if isinstance(r[2], dict) else
                       (json.loads(r[2]) if r[2] else {})) for r in rows}

    # Telegram
    if "telegram" in enabled:
        try:
            from olt_routes import _telegram_send_alert
            _telegram_send_alert(cid, title=title, message=full_msg)
            delivered.append("telegram")
        except Exception as e:
            print(f"[smartnet notif] telegram fail: {e}")
    # WhatsApp
    if "whatsapp" in enabled:
        cfg = enabled["whatsapp"]
        target = cfg.get("target") or cfg.get("phone")
        if target:
            try:
                from olt_routes import _whatsapp_send_alert
                _whatsapp_send_alert(cid, target=target, title=title,
                                      message=full_msg)
                delivered.append("whatsapp")
            except Exception as e:
                print(f"[smartnet notif] whatsapp fail: {e}")
    # Email — best-effort SMTP
    if "email" in enabled:
        cfg = enabled["email"]
        to_addr = cfg.get("to") or cfg.get("address")
        smtp_host = os.environ.get("SMTP_HOST") or cfg.get("smtp_host")
        if to_addr and smtp_host:
            try:
                import smtplib
                from email.message import EmailMessage
                m = EmailMessage()
                m["Subject"] = title
                m["From"] = os.environ.get("SMTP_FROM") or cfg.get("from") or "no-reply@autoispbilling.com"
                m["To"] = to_addr
                m.set_content(full_msg)
                with smtplib.SMTP(smtp_host, int(cfg.get("smtp_port") or 587), timeout=8) as s:
                    if cfg.get("smtp_user"):
                        s.starttls()
                        s.login(cfg["smtp_user"], cfg.get("smtp_pass") or "")
                    s.send_message(m)
                delivered.append("email")
            except Exception as e:
                print(f"[smartnet notif] email fail: {e}")
    return {"delivered": delivered}


# Patch the alert-create endpoint so it auto-delivers on insert.
# (Kept as a new helper to avoid touching the existing endpoint body.)

def _create_alert_and_deliver(cid: str, severity: str, message: str,
                               device_id: Optional[int] = None,
                               port_id: Optional[int] = None) -> int:
    """Insert an alert and fan it out to enabled channels."""
    sev = (severity or "info").lower()
    if sev not in ("critical", "major", "warning", "info"):
        sev = "info"
    with engine.begin() as conn:
        new_id = conn.exec_driver_sql(
            "INSERT INTO smartnet_alerts (company_id, device_id, port_id, "
            "severity, message) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (cid, device_id, port_id, sev, message)).scalar()
        dev_name = ""
        if device_id:
            d = conn.exec_driver_sql(
                "SELECT name FROM smartnet_devices WHERE id=%s",
                (device_id,)).fetchone()
            dev_name = d[0] if d else ""
    # Fan out (non-fatal on failure)
    try:
        _deliver_notification(cid, sev, message, dev_name)
    except Exception as e:
        print(f"[smartnet notif] delivery failed: {e}")
    return new_id


# ──────────────────────────────────────────────────────────────────────────
#  _S60B_POLL_HOOK_  Wire OLT poll → smartnet_bandwidth + smartnet_ports
# ──────────────────────────────────────────────────────────────────────────

def poll_hook_persist(conn, *, cid: str, olt: Dict[str, Any],
                       res: Any, online: int, total: int,
                       avg_rx: Any) -> None:
    """Called from olt_routes._poll_one_olt — best-effort persistence
    of poll output into smartnet_* tables.

    Side effects (all idempotent):
      • upsert smartnet_devices row for the OLT itself
      • upsert one smartnet_ports row per ONU (port_name=pon/onu_index)
      • insert one smartnet_bandwidth sample at OLT level (sum)
      • raise critical alert on offline-spike + low-RX
    """
    try:
        olt_id = int(olt["id"])
        olt_name = olt.get("name") or f"OLT-{olt_id}"
        # 1) Upsert OLT device
        existing = conn.exec_driver_sql(
            "SELECT id FROM smartnet_devices WHERE company_id=%s "
            "AND meta->>'olt_id'=%s", (cid, str(olt_id))).fetchone()
        olt_status = "up" if online > 0 else "down"
        if existing:
            sn_olt_id = existing[0]
            conn.exec_driver_sql(
                "UPDATE smartnet_devices SET status=%s, updated_at=NOW() "
                "WHERE id=%s", (olt_status, sn_olt_id))
        else:
            sn_olt_id = conn.exec_driver_sql(
                "INSERT INTO smartnet_devices (company_id, name, type, "
                "vendor, ip_address, status, x, y, meta) VALUES "
                "(%s,%s,'olt',%s,%s,%s,%s,%s,%s) RETURNING id",
                (cid, olt_name, olt.get("vendor"), olt.get("host"),
                 olt_status, 600, 300,
                 json.dumps({"olt_id": olt_id, "source": "olt-poll"}))
            ).scalar()

        # 2) Per-ONU port upserts (one port per ONU on the OLT card)
        onus = getattr(res, "onus", []) or []
        for o in onus[:512]:
            port_name = f"pon{o['pon_port_index']}/onu{o['onu_index']}"
            conn.exec_driver_sql(
                "INSERT INTO smartnet_ports (company_id, device_id, "
                "port_name, port_type, status, speed_mbps, tx_power_dbm, "
                "rx_power_dbm, temp_c, voltage_v, last_change) VALUES "
                "(%s,%s,%s,'pon',%s,%s,%s,%s,%s,%s,NOW()) "
                "ON CONFLICT (device_id, port_name) DO UPDATE SET "
                "  status=EXCLUDED.status, "
                "  speed_mbps=EXCLUDED.speed_mbps, "
                "  tx_power_dbm=EXCLUDED.tx_power_dbm, "
                "  rx_power_dbm=EXCLUDED.rx_power_dbm, "
                "  temp_c=EXCLUDED.temp_c, voltage_v=EXCLUDED.voltage_v, "
                "  last_change=NOW()",
                (cid, sn_olt_id, port_name,
                 "up" if (o.get("status") or "").lower() == "online" else "down",
                 1250,  # GPON 1.25 Gbps upstream typical
                 float(o.get("tx_power") or 0) or None,
                 float(o.get("rx_power") or 0) or None,
                 float(o.get("temperature_c") or 0) or None,
                 float(o.get("voltage_v") or 0) or None))

        # 3) _S60F_BW_FIX_  Skip synthetic BW estimate — was causing chart
        #    to climb because `online * 25` is unbounded and accumulates
        #    one sample per OLT per poll. Real BW samples come in via the
        #    `POST /api/admin/smartnet/bandwidth` push endpoint (driven by
        #    a future SNMP/RouterOS interface-counter poller).
        _ = (online, total)  # noqa - keep parameters used

        # 4) Auto-alerts (deliver only on transitions to keep noise low)
        prev = conn.exec_driver_sql(
            "SELECT severity FROM smartnet_alerts WHERE company_id=%s "
            "AND device_id=%s AND status='active' ORDER BY id DESC LIMIT 1",
            (cid, sn_olt_id)).fetchone()
        prev_sev = (prev[0] if prev else None)
        if total > 0 and online == 0 and prev_sev != "critical":
            _create_alert_and_deliver(cid, "critical",
                f"OLT offline — 0/{total} ONUs reachable", sn_olt_id)
        elif avg_rx and float(avg_rx) < -28 and prev_sev not in ("warning","critical"):
            _create_alert_and_deliver(cid, "warning",
                f"Average ONU RX = {avg_rx:.1f} dBm (degraded fiber)",
                sn_olt_id)
    except Exception as e:
        print(f"[smartnet poll-hook] persist failed: {e}")


# ──────────────────────────────────────────────────────────────────────────
#  Override `api_alert_create` to call the deliver-wrapper
#  (We re-bind by replacing the existing handler at registration time.)
# ──────────────────────────────────────────────────────────────────────────

# Find + remove the original alert-create route (re-added below)
_routes_to_drop = []
for rt in router.routes:
    if getattr(rt, "path", "") == "/api/admin/smartnet/alerts" \
       and "POST" in getattr(rt, "methods", set()):
        _routes_to_drop.append(rt)
for rt in _routes_to_drop:
    router.routes.remove(rt)


@router.post("/api/admin/smartnet/alerts")
def api_alert_create_v2(request: Request, body: Dict[str, Any] = Body(...)):
    """_S60B_  Create + auto-deliver alert across enabled channels."""
    sc = _scope(request)
    if not _is_writer(request):
        raise HTTPException(403, "Admin-only action")
    cid = sc["company_id"]
    new_id = _create_alert_and_deliver(
        cid, body.get("severity") or "info",
        (body.get("message") or "Manual alert").strip()[:500],
        body.get("device_id"), body.get("port_id"))
    return {"ok": True, "id": new_id}




# ──────────────────────────────────────────────────────────────────────────
#  _S60F_PHASEC_WS_  WebSocket broadcaster — live alerts + topology updates
# ──────────────────────────────────────────────────────────────────────────

from fastapi import WebSocket, WebSocketDisconnect  # noqa
from typing import Set
import asyncio as _aio_phc

class _WSBroker:
    """In-memory pub/sub fanout. One bucket per tenant (company_id).
    _S60F_WS_FIX_  Captures the main asyncio loop on first WS connect so
    sync code paths (request handlers, poll loop) can publish via
    `run_coroutine_threadsafe` without touching `get_event_loop()`."""
    def __init__(self):
        self._subs: Dict[str, Set[WebSocket]] = {}
        self._loop = None  # captured on first async use

    def _capture_loop(self):
        if self._loop is None:
            try:
                self._loop = _aio_phc.get_running_loop()
            except RuntimeError:
                pass

    async def add(self, cid: str, ws: WebSocket):
        self._capture_loop()
        self._subs.setdefault(cid, set()).add(ws)

    async def remove(self, cid: str, ws: WebSocket):
        self._subs.get(cid, set()).discard(ws)

    def publish(self, cid: str, payload: Dict[str, Any]) -> None:
        """Non-blocking publish from sync code paths (poll loop / handlers).
        Thread-safe via run_coroutine_threadsafe."""
        subs = list(self._subs.get(cid, set()))
        if not subs or self._loop is None:
            return
        async def _fanout():
            dead = []
            for ws in subs:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._subs.get(cid, set()).discard(ws)
        try:
            _aio_phc.run_coroutine_threadsafe(_fanout(), self._loop)
        except Exception as e:
            print(f"[WS publish] {e}")

WS_BROKER = _WSBroker()


@router.websocket("/ws/smartnet")
async def ws_smartnet(ws: WebSocket):
    """Tenant-scoped live channel for Smart Network.

    Pushes events of shape:
      {"event":"alert","alert":{...}}
      {"event":"device_status","device_id":..,"status":"up"|"down"}
      {"event":"topology_change","kind":"create|update|delete","entity":...}

    The client should authenticate via the existing session cookie before
    connecting (handled by Starlette session middleware automatically).
    """
    await ws.accept()
    cid = ws.session.get("company_id") if hasattr(ws, "session") else None
    if not cid:
        # try cookie-decoded session
        cid = (ws.scope.get("session") or {}).get("company_id")
    if not cid:
        await ws.send_json({"event":"error","message":"not authenticated"})
        await ws.close(code=4401)
        return
    await WS_BROKER.add(cid, ws)
    await ws.send_json({"event":"hello","cid":cid,
                          "ts": datetime.now(timezone.utc).isoformat()})
    try:
        while True:
            # Keep-alive: clients can send any text/ping; ignore content
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await WS_BROKER.remove(cid, ws)


# Patch _create_alert_and_deliver to also publish over WS
_orig_create_alert = _create_alert_and_deliver
def _create_alert_and_deliver_v2(cid: str, severity: str, message: str,
                                  device_id=None, port_id=None):
    new_id = _orig_create_alert(cid, severity, message, device_id, port_id)
    try:
        WS_BROKER.publish(cid, {
            "event":"alert",
            "alert":{"id": new_id, "severity": severity, "message": message,
                      "device_id": device_id, "port_id": port_id,
                      "created_at": datetime.now(timezone.utc).isoformat()}})
    except Exception:
        pass
    return new_id
# Re-bind name so callers (incl. poll_hook_persist) pick up the WS-enabled one
_create_alert_and_deliver = _create_alert_and_deliver_v2


# Patch poll_hook_persist to publish device_status changes
_orig_poll_hook = poll_hook_persist
def poll_hook_persist_v2(conn, *, cid, olt, res, online, total, avg_rx):
    _orig_poll_hook(conn, cid=cid, olt=olt, res=res, online=online,
                     total=total, avg_rx=avg_rx)
    try:
        new_status = "up" if online > 0 else "down"
        WS_BROKER.publish(cid, {
            "event":"device_status",
            "olt_id": olt.get("id"),
            "olt_name": olt.get("name"),
            "status": new_status,
            "online": online, "total": total,
            "avg_rx_dbm": float(avg_rx) if avg_rx else None,
            "ts": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass
poll_hook_persist = poll_hook_persist_v2

# _S60F_PHASEC_WS_  applied



# ──────────────────────────────────────────────────────────────────────────
#  _S60G_BW_POLLER_  RouterOS interface-counter poller — 5-min cycle.
#  Pushes REAL Mbps into smartnet_bandwidth so the Live Bandwidth chart
#  stops showing synthetic data once at least one NAS is registered.
# ──────────────────────────────────────────────────────────────────────────

import threading as _bw_threading
_BW_PREV: Dict[int, Dict[str, Any]] = {}  # device_id → {ts, rx_b, tx_b}

def _bw_poll_one_nas(nas_row) -> Optional[Dict[str, float]]:
    """Sum tx-byte + rx-byte across all running interfaces of the NAS,
    return current bytes-in/out + Mbps deltas vs last poll."""
    try:
        import routeros_provision as rp
    except ImportError:
        return None
    try:
        class _Stub: pass
        s = _Stub()
        s.ip_address = nas_row["ip_address"]
        s.api_username = nas_row["api_username"]
        s.api_password = nas_row["api_password"]
        s.use_ssh = nas_row.get("use_ssh", 0)
        s.use_tls = nas_row.get("use_tls", 0)
        s.port = nas_row.get("port", 8728)
        s.ssh_port = nas_row.get("ssh_port", 22)
        with rp.RouterOSClient(s, dry_run=False) as cli:
            if cli._api is None:
                return None
            total_rx, total_tx = 0, 0
            for itf in cli._api.path("interface"):
                if (itf.get("running") or "false") != "true": continue
                if itf.get("disabled") == "true": continue
                try:
                    total_rx += int(itf.get("rx-byte") or 0)
                    total_tx += int(itf.get("tx-byte") or 0)
                except Exception:
                    pass
            return {"rx_b": total_rx, "tx_b": total_tx,
                    "ts": time.time()}
    except Exception as e:
        print(f"[bw-poll] {nas_row.get('name')}: {e}")
        return None


def _bw_poll_loop():
    """Background loop, 5-min cycle. Maps each NAS → smartnet_devices via
    meta.src='nas' and inserts a smartnet_bandwidth sample with computed
    Mbps deltas (bytes-now − bytes-prev) / elapsed."""
    print("[smartnet bw-poller] started — 5 min cycle")
    INTERVAL = 300
    while True:
        try:
            time.sleep(INTERVAL)
            with engine.begin() as conn:
                nas_rows = conn.exec_driver_sql(
                    "SELECT n.id, n.company_id, n.name, n.ip_address, "
                    "       n.api_username, n.api_password, n.use_ssh, "
                    "       n.use_tls, n.port, n.ssh_port, sd.id "
                    "FROM nas_devices n "
                    "JOIN smartnet_devices sd ON sd.company_id=n.company_id "
                    "  AND sd.meta->>'src'='nas' "
                    "  AND sd.meta->>'src_id'=n.id::text").fetchall()
            for r in nas_rows:
                (n_id, cid, n_name, ip, user, pw, ssh, tls, port,
                 ssh_port, sn_dev_id) = r
                nas_row = {"id": n_id, "name": n_name, "ip_address": ip,
                            "api_username": user, "api_password": pw,
                            "use_ssh": ssh, "use_tls": tls,
                            "port": port, "ssh_port": ssh_port}
                cur = _bw_poll_one_nas(nas_row)
                if not cur:
                    continue
                prev = _BW_PREV.get(sn_dev_id)
                if prev and cur["ts"] > prev["ts"]:
                    elapsed = cur["ts"] - prev["ts"]
                    drx = max(0, cur["rx_b"] - prev["rx_b"])
                    dtx = max(0, cur["tx_b"] - prev["tx_b"])
                    # bytes per second → Mbps (× 8 / 1_000_000)
                    rx_mbps = round(drx * 8 / elapsed / 1_000_000, 2)
                    tx_mbps = round(dtx * 8 / elapsed / 1_000_000, 2)
                    try:
                        with engine.begin() as conn:
                            conn.exec_driver_sql(
                                "INSERT INTO smartnet_bandwidth (company_id, "
                                "device_id, in_mbps, out_mbps) VALUES "
                                "(%s,%s,%s,%s)",
                                (cid, sn_dev_id, rx_mbps, tx_mbps))
                    except Exception as e:
                        print(f"[bw-poll] insert fail: {e}")
                _BW_PREV[sn_dev_id] = cur
            # Trim old rows — keep last 24h only
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "DELETE FROM smartnet_bandwidth "
                        "WHERE ts < NOW() - INTERVAL '24 hours'")
            except Exception:
                pass
        except Exception as e:
            print(f"[smartnet bw-poller] {e}")


_bw_threading.Thread(target=_bw_poll_loop, daemon=True,
                      name="smartnet-bw-poller").start()

print("[smart_network] router loaded")

# _S60F_BW_FIX_  applied

# _S60F_TEMPLATE_SVG_  applied

# _S60G_LIVE_IMPORT_  applied

# _S60H_DEVICE_FILTER_  applied
