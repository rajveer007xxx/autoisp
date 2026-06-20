"""_S60_PROFILE_ENGINE_  ZTP Service Profile CRUD + UI.

ONU service profile templates that bundle PPPoE/IPoE auth, VLAN, QoS
(up/down kbps), default WiFi SSID template, ACS inform interval, etc.
On binding to a customer's ONU, the corresponding profile is applied
during the ZTP push. Auto-loaded via main.py dynamic loader.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Body, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from database import engine

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _scope(request: Request) -> Dict[str, str]:
    sess = request.session
    cid = sess.get("company_id")
    ut = (sess.get("user_type") or "").lower()
    if not cid:
        raise HTTPException(401, "Not authenticated")
    if ut not in ("admin", "superadmin"):
        raise HTTPException(403, "Admin-only")
    return {"company_id": cid,
            "actor": sess.get("user_name") or sess.get("user_id") or "admin"}


# ──────────────────────────────────────────────────────────────────────────
#  Page
# ──────────────────────────────────────────────────────────────────────────

@router.get("/admin/olt/profiles", response_class=HTMLResponse)
def profiles_page(request: Request):
    _scope(request)
    return templates.TemplateResponse("admin_profile_engine.html", {
        "request": request, "active_page": "olt_profiles",
    })


# ──────────────────────────────────────────────────────────────────────────
#  CRUD
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/olt/profiles")
def api_profiles_list(request: Request):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, name, connection_type, vlan, qos_dl_kbps, qos_ul_kbps, "
            "       wifi_ssid_tpl, wifi_pw_tpl, wifi_band_split, "
            "       acs_inform_int, created_at, updated_at "
            "FROM onu_service_profiles WHERE company_id=%s ORDER BY name",
            (cid,)).fetchall()
    keys = ("id","name","connection_type","vlan","qos_dl_kbps","qos_ul_kbps",
            "wifi_ssid_tpl","wifi_pw_tpl","wifi_band_split","acs_inform_int",
            "created_at","updated_at")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        for k in ("created_at","updated_at"):
            if d.get(k):
                d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
        out.append(d)
    return {"profiles": out}


@router.get("/api/admin/olt/profiles/{pid}")
def api_profile_get(request: Request, pid: int):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT id, name, connection_type, vlan, qos_dl_kbps, qos_ul_kbps, "
            "       wifi_ssid_tpl, wifi_pw_tpl, wifi_band_split, acs_inform_int "
            "FROM onu_service_profiles WHERE id=%s AND company_id=%s",
            (pid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Profile not found")
    keys = ("id","name","connection_type","vlan","qos_dl_kbps","qos_ul_kbps",
            "wifi_ssid_tpl","wifi_pw_tpl","wifi_band_split","acs_inform_int")
    return {"profile": dict(zip(keys, r))}


@router.post("/api/admin/olt/profiles")
def api_profile_create(request: Request, body: Dict[str, Any] = Body(...)):
    sc = _scope(request); cid = sc["company_id"]
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    ctype = (body.get("connection_type") or "pppoe").lower()
    if ctype not in ("pppoe","ipoe","static","bridge"):
        ctype = "pppoe"
    try:
        with engine.begin() as conn:
            new_id = conn.exec_driver_sql(
                "INSERT INTO onu_service_profiles (company_id, name, "
                "connection_type, vlan, qos_dl_kbps, qos_ul_kbps, "
                "wifi_ssid_tpl, wifi_pw_tpl, wifi_band_split, "
                "acs_inform_int) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (cid, name, ctype,
                 int(body.get("vlan") or 0) or None,
                 int(body.get("qos_dl_kbps") or 0) or None,
                 int(body.get("qos_ul_kbps") or 0) or None,
                 body.get("wifi_ssid_tpl"),
                 body.get("wifi_pw_tpl"),
                 1 if body.get("wifi_band_split") else 0,
                 int(body.get("acs_inform_int") or 300))).scalar()
    except Exception as e:
        msg = str(e)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(409, f"Profile '{name}' already exists")
        raise HTTPException(400, msg[:200])
    return {"ok": True, "id": new_id}


@router.put("/api/admin/olt/profiles/{pid}")
def api_profile_update(request: Request, pid: int,
                        body: Dict[str, Any] = Body(...)):
    sc = _scope(request); cid = sc["company_id"]
    allowed = ("name","connection_type","vlan","qos_dl_kbps","qos_ul_kbps",
               "wifi_ssid_tpl","wifi_pw_tpl","wifi_band_split","acs_inform_int")
    fields, params = [], []
    for k in allowed:
        if k in body:
            fields.append(f"{k}=%s")
            v = body[k]
            if k == "wifi_band_split":
                v = 1 if v else 0
            elif k in ("vlan","qos_dl_kbps","qos_ul_kbps","acs_inform_int"):
                v = int(v) if v not in (None, "") else None
            params.append(v)
    if not fields:
        raise HTTPException(400, "no fields")
    fields.append("updated_at=NOW()")
    params.extend([pid, cid])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE onu_service_profiles SET {','.join(fields)} "
            "WHERE id=%s AND company_id=%s", tuple(params))
    return {"ok": True}


@router.delete("/api/admin/olt/profiles/{pid}")
def api_profile_delete(request: Request, pid: int):
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        # Detach from ONUs first
        try:
            conn.exec_driver_sql(
                "UPDATE onus SET service_profile_id=NULL "
                "WHERE service_profile_id=%s AND company_id=%s",
                (pid, cid))
        except Exception:
            pass
        conn.exec_driver_sql(
            "DELETE FROM onu_service_profiles WHERE id=%s AND company_id=%s",
            (pid, cid))
    return {"ok": True}


@router.post("/api/admin/olt/profiles/{pid}/apply-to-onus")
def api_profile_apply(request: Request, pid: int,
                       body: Dict[str, Any] = Body(default={})):
    """Bulk-bind profile to ONUs by id-list, or by olt_id (all ONUs of an OLT)."""
    sc = _scope(request); cid = sc["company_id"]
    onu_ids = body.get("onu_ids") or []
    olt_id = body.get("olt_id")
    n = 0
    with engine.begin() as conn:
        # Verify profile belongs to tenant
        own = conn.exec_driver_sql(
            "SELECT 1 FROM onu_service_profiles WHERE id=%s AND company_id=%s",
            (pid, cid)).fetchone()
        if not own:
            raise HTTPException(404, "Profile not found")
        try:
            if onu_ids:
                for oid in onu_ids:
                    conn.exec_driver_sql(
                        "UPDATE onus SET service_profile_id=%s "
                        "WHERE id=%s AND company_id=%s",
                        (pid, int(oid), cid))
                    n += 1
            elif olt_id:
                r = conn.exec_driver_sql(
                    "UPDATE onus SET service_profile_id=%s "
                    "WHERE olt_id=%s AND company_id=%s",
                    (pid, int(olt_id), cid))
                n = r.rowcount if hasattr(r, "rowcount") else 0
        except Exception as e:
            # service_profile_id column may not exist yet — best-effort
            print(f"[profile apply] {e}")
    return {"ok": True, "applied": n}


print("[profile_engine] router loaded")
