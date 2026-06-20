"""
s60K_ZTP — FastAPI routes + background watcher (Phases 6, 11, 12).

Routes (all prefixed /api/admin/ztp; tenant-scoped via session):
  • GET    /discovered?olt_id=N         list unauthorized ONUs
  • POST   /discover                    {olt_id} → triggers discovery
  • POST   /map                         create/update customer mapping
  • POST   /provision                   trigger full bootstrap pipeline
  • POST   /recover                     factory-reset recovery
  • GET    /diagnose?serial=X           diagnostic checklist
  • GET    /compatibility?serial=X      locked-ONT capability label
  • GET    /state-audit?serial=X
  • GET    /vendors                     list available driver vendors
  • GET    /parameter-profiles          list known TR-069 path profiles
  • POST   /acs/watcher/scan            run GenieACS watcher once
  • POST   /dhcp43/generate             one-touch MikroTik script
  • POST   /dhcp43/save                 save the generated config
  • GET    /dhcp43/list?nas_id=N        list configs per NAS

UI partials (HTML):
  • GET    /admin/ztp/dashboard         in-app overview
  • GET    /admin/ztp/diagnose/{serial} compact diagnostic table
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional
from fastapi import APIRouter, Body, HTTPException, Request

# Reuse the canonical scope helpers from olt_routes.
from olt_routes import _require_scope, engine

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────
#  Health
# ──────────────────────────────────────────────────────────────────────
@router.get("/api/admin/ztp/health")
def ztp_health(request: Request):
    _require_scope(request)
    try:
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT "
                "  (SELECT COUNT(*) FROM ztp_discovered_onus), "
                "  (SELECT COUNT(*) FROM ztp_onu_customer_mapping), "
                "  (SELECT COUNT(*) FROM ztp_onu_profiles), "
                "  (SELECT COUNT(*) FROM acs_device_mapping), "
                "  (SELECT COUNT(*) FROM acs_device_parameter_profiles), "
                "  (SELECT COUNT(*) FROM ztp_state_audit), "
                "  (SELECT COUNT(*) FROM ztp_dhcp_option43_configs)"
            ).fetchone()
        return {"ok": True,
                "tables": {"discovered_onus": row[0],
                           "customer_mappings": row[1],
                           "profiles": row[2],
                           "acs_devices": row[3],
                           "parameter_profiles": row[4],
                           "state_audit": row[5],
                           "dhcp43_configs": row[6]}}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ──────────────────────────────────────────────────────────────────────
#  Discovery
# ──────────────────────────────────────────────────────────────────────
@router.post("/api/admin/ztp/discover")
def ztp_discover(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    olt_id = int(body.get("olt_id") or 0)
    if not olt_id:
        raise HTTPException(400, "olt_id required")
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.discover(olt_id)


@router.get("/api/admin/ztp/discovered")
def ztp_list_discovered(request: Request, olt_id: Optional[int] = None,
                         status: Optional[str] = None, limit: int = 200):
    sc = _require_scope(request)
    q = ("SELECT id, olt_id, pon_port, onu_serial, onu_vendor, onu_model, "
         "status, last_seen, rx_power_dbm, tx_power_dbm "
         "FROM ztp_discovered_onus WHERE company_id=?")
    args = [sc["company_id"]]
    if olt_id:
        q += " AND olt_id=?"; args.append(olt_id)
    if status:
        q += " AND status=?"; args.append(status.upper())
    q += " ORDER BY last_seen DESC LIMIT ?"; args.append(int(limit))
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(q, tuple(args)).fetchall()
    return {"ok": True,
            "items": [{"id": r[0], "olt_id": r[1], "pon_port": r[2],
                       "serial": r[3], "vendor": r[4], "model": r[5],
                       "status": r[6],
                       "last_seen": r[7].isoformat() if r[7] else None,
                       "rx_dbm": float(r[8]) if r[8] is not None else None,
                       "tx_dbm": float(r[9]) if r[9] is not None else None}
                      for r in rows]}


# ──────────────────────────────────────────────────────────────────────
#  Customer mapping & provisioning
# ──────────────────────────────────────────────────────────────────────
@router.post("/api/admin/ztp/map")
def ztp_map(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    serial = (body.get("serial") or "").strip()
    cust_id = (body.get("customer_id") or "").strip()
    olt_id = int(body.get("olt_id") or 0)
    pon_port = int(body.get("pon_port") or 0)
    onu_index = int(body.get("onu_index") or 0)
    if not (serial and cust_id and olt_id):
        raise HTTPException(400, "serial, customer_id, olt_id required")
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.map_to_customer(
        serial=serial, customer_id=cust_id, olt_id=olt_id,
        pon_port=pon_port, onu_index=onu_index,
        plan_id=body.get("plan_id"),
        vlan_id=body.get("vlan_id"),
        pppoe_user=body.get("pppoe_user"),
        pppoe_password=body.get("pppoe_password"))


@router.post("/api/admin/ztp/provision")
def ztp_provision(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    serial = (body.get("serial") or "").strip()
    if not serial:
        raise HTTPException(400, "serial required")
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.provision(serial)


@router.post("/api/admin/ztp/recover")
def ztp_recover(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    serial = (body.get("serial") or "").strip()
    if not serial:
        raise HTTPException(400, "serial required")
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.recover_factory_reset(serial)


@router.get("/api/admin/ztp/diagnose")
def ztp_diagnose(request: Request, serial: str):
    sc = _require_scope(request)
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.diagnose(serial)


@router.get("/api/admin/ztp/compatibility")
def ztp_compat(request: Request, serial: str):
    sc = _require_scope(request)
    from ztp_engine import ZTPEngine
    eng = ZTPEngine(sc["company_id"], sc["actor"])
    return eng.compatibility(serial)


@router.get("/api/admin/ztp/state-audit")
def ztp_audit(request: Request, serial: str, limit: int = 50):
    sc = _require_scope(request)
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT from_state, to_state, reason, actor, created_at "
            "FROM ztp_state_audit WHERE company_id=? AND onu_serial=? "
            "ORDER BY created_at DESC LIMIT ?",
            (sc["company_id"], serial, int(limit))).fetchall()
    return {"ok": True,
            "items": [{"from": r[0], "to": r[1], "reason": r[2],
                       "actor": r[3],
                       "ts": r[4].isoformat() if r[4] else None}
                      for r in rows]}


# ──────────────────────────────────────────────────────────────────────
#  Vendor / parameter profile catalogs
# ──────────────────────────────────────────────────────────────────────
@router.get("/api/admin/ztp/vendors")
def ztp_vendors(request: Request):
    _require_scope(request)
    from ztp_drivers import list_drivers
    return {"ok": True, "vendors": list_drivers()}


@router.get("/api/admin/ztp/parameter-profiles")
def ztp_param_profiles(request: Request):
    _require_scope(request)
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT vendor, model, product_class, set_strategy, priority, "
            "notes FROM acs_device_parameter_profiles "
            "ORDER BY priority, vendor").fetchall()
    return {"ok": True,
            "items": [{"vendor": r[0], "model": r[1],
                       "product_class": r[2], "strategy": r[3],
                       "priority": r[4], "notes": r[5]}
                      for r in rows]}


# ──────────────────────────────────────────────────────────────────────
#  GenieACS watcher
# ──────────────────────────────────────────────────────────────────────
@router.post("/api/admin/ztp/acs/watcher/scan")
def ztp_acs_scan(request: Request):
    """Pulls all GenieACS devices and matches against tenant mappings.
    Idempotent — safe to call repeatedly. Returns counts."""
    sc = _require_scope(request)
    cid = sc["company_id"]
    # Get NBI URL
    with engine.begin() as conn:
        s = conn.exec_driver_sql(
            "SELECT genieacs_url, genieacs_username, genieacs_password "
            "FROM olt_settings WHERE company_id=? LIMIT 1",
            (cid,)).fetchone()
    if not s or not s[0]:
        return {"ok": False, "error": "GenieACS NBI URL not configured"}
    import urllib.request, urllib.parse, base64
    auth_hdr = None
    if s[1]:
        tok = base64.b64encode(f"{s[1]}:{s[2] or ''}".encode()).decode()
        auth_hdr = "Basic " + tok

    try:
        url = (s[0].rstrip("/")
               + "/devices?projection=_id,_deviceId,_lastInform,"
                 "_lastBootstrap,InternetGatewayDevice.DeviceInfo."
                 "Manufacturer,InternetGatewayDevice.DeviceInfo.ModelName,"
                 "InternetGatewayDevice.DeviceInfo.SoftwareVersion")
        req = urllib.request.Request(url)
        if auth_hdr:
            req.add_header("Authorization", auth_hdr)
        with urllib.request.urlopen(req, timeout=15) as r:
            devices = json.loads(r.read(2_000_000).decode("utf-8", "ignore"))
    except Exception as e:
        return {"ok": False, "error": f"NBI list failed: {e}"}

    from ztp_engine import ZTPEngine
    eng = ZTPEngine(cid, sc["actor"])
    matched = unmatched = 0
    for d in devices or []:
        dev_id = d.get("_id") or ""
        di = (d.get("_deviceId") or {})
        info = {
            "SerialNumber": di.get("_SerialNumber"),
            "ProductClass": di.get("_ProductClass"),
            "OUI": di.get("_OUI"),
            "Manufacturer": di.get("_Manufacturer"),
            "Model": (d.get("InternetGatewayDevice", {})
                      .get("DeviceInfo", {}).get("ModelName", {})
                      .get("_value") if isinstance(d, dict) else None),
            "FirmwareVersion": (d.get("InternetGatewayDevice", {})
                                .get("DeviceInfo", {})
                                .get("SoftwareVersion", {})
                                .get("_value") if isinstance(d, dict) else None),
        }
        res = eng.acs_match_and_push(dev_id, info)
        if res.get("matched"):
            matched += 1
        else:
            unmatched += 1
    return {"ok": True, "devices_seen": len(devices or []),
            "matched": matched, "unmatched": unmatched}


# ──────────────────────────────────────────────────────────────────────
#  DHCP Option 43 + MikroTik script
# ──────────────────────────────────────────────────────────────────────
@router.post("/api/admin/ztp/dhcp43/generate")
def ztp_dhcp43_generate(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    port = (body.get("port_name") or "").strip()
    vlan = int(body.get("vlan_id") or 0)
    acs_url = (body.get("acs_url") or "").strip()
    if not (port and vlan and acs_url):
        raise HTTPException(400,
                            "port_name, vlan_id and acs_url are required")
    # Fallback to tenant ACS URL if blank-supplied
    if not acs_url:
        with engine.begin() as conn:
            s = conn.exec_driver_sql(
                "SELECT tr069_acs_url FROM olt_settings WHERE company_id=? "
                "LIMIT 1", (sc["company_id"],)).fetchone()
        acs_url = (s[0] if s else "") or ""
    if not acs_url:
        raise HTTPException(400, "ACS URL is empty and no tenant default")
    from ztp_dhcp43 import generate_mikrotik_script
    try:
        out = generate_mikrotik_script(
            port_name=port, vlan_id=vlan, acs_url=acs_url,
            acs_username=body.get("acs_username") or "",
            acs_password=body.get("acs_password") or "",
            dhcp_pool_range=body.get("dhcp_pool_range")
                or "10.43.43.10-10.43.43.250",
            dhcp_gateway=body.get("dhcp_gateway") or "10.43.43.1",
            dhcp_subnet_mask=body.get("dhcp_subnet_mask") or "255.255.255.0",
            dns_servers=body.get("dns_servers") or "8.8.8.8,1.1.1.1",
            vendor_class_filter=body.get("vendor_class_filter"),
            nas_label=body.get("nas_label") or "AUTOISP-ZTP")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **out}


@router.post("/api/admin/ztp/dhcp43/save")
def ztp_dhcp43_save(request: Request, body: Dict = Body(...)):
    sc = _require_scope(request)
    nas_id = int(body.get("nas_id") or 0)
    if not nas_id:
        raise HTTPException(400, "nas_id required")
    from ztp_dhcp43 import generate_mikrotik_script, save_config
    try:
        gen = generate_mikrotik_script(
            port_name=body.get("port_name") or "",
            vlan_id=int(body.get("vlan_id") or 0),
            acs_url=body.get("acs_url") or "",
            acs_username=body.get("acs_username") or "",
            acs_password=body.get("acs_password") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    save_config(engine, company_id=sc["company_id"], nas_id=nas_id,
                port_name=body.get("port_name") or "",
                vlan_id=int(body.get("vlan_id") or 0),
                acs_url=body.get("acs_url") or "",
                acs_username=body.get("acs_username") or "",
                acs_password=body.get("acs_password") or "",
                generated_script=gen["script"])
    return {"ok": True, "saved": True, "summary": gen["summary"]}


@router.get("/api/admin/ztp/dhcp43/list")
def ztp_dhcp43_list(request: Request, nas_id: Optional[int] = None):
    sc = _require_scope(request)
    q = ("SELECT id, nas_id, port_name, vlan_id, acs_url, enabled, "
         "last_generated_at FROM ztp_dhcp_option43_configs "
         "WHERE company_id=?")
    args = [sc["company_id"]]
    if nas_id:
        q += " AND nas_id=?"; args.append(nas_id)
    q += " ORDER BY updated_at DESC"
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(q, tuple(args)).fetchall()
    return {"ok": True,
            "items": [{"id": r[0], "nas_id": r[1], "port_name": r[2],
                       "vlan_id": r[3], "acs_url": r[4], "enabled": r[5],
                       "last_generated_at": r[6].isoformat() if r[6]
                                            else None}
                      for r in rows]}
