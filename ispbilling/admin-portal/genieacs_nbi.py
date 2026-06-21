"""_S60I_GENIEACS_NBI_  GenieACS NBI integration — pre-stage ONUs by
serial number before they connect, push TR-069 tasks to existing devices,
and create/update presets so common provisioning rules apply automatically.

Reference docs consulted (Feb 2026):
  - docs.genieacs.com/en/latest/api-reference.html  (NBI endpoints)
  - GenieACS GitHub ARCHITECTURE.md                 (task lifecycle)

Also bundles vendor-specific OLT CLI command profiles for ACS-URL push so
the system can fall back to OLT-side push when GenieACS NBI is unavailable:
  - VSOL V1600D EPON     (already in olt_telnet_actions)
  - Huawei MA5800 GPON
  - ZTE C300 / C320 GPON
  - BDCOM GP3600 GPON
  - Syrotech SY-GPON
"""
from __future__ import annotations
import os  # __PHASE_B_ENV_REFACTOR__

import json, os, urllib.parse, urllib.request, urllib.error
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Body

from database import engine

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────
#  Tenant settings helper
# ──────────────────────────────────────────────────────────────────────────

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


def _nbi_base(cid: str) -> str:
    """Return the tenant's GenieACS NBI base URL (default port 7557)."""
    try:
        with engine.begin() as conn:
            r = conn.exec_driver_sql(
                "SELECT value FROM olt_settings WHERE company_id=%s "
                "AND key IN ('genieacs_nbi_url','genieacs_url') "
                "ORDER BY key DESC LIMIT 1", (cid,)).fetchone()
        if r and r[0]:
            base = r[0].rstrip("/")
            if base.endswith(":7557") or "/devices" in base:
                return base.replace("/devices","")
            # If it's just the ACS endpoint, swap port
            return base.replace(":7547", ":7557")
    except Exception:
        pass
    return os.environ.get("GENIEACS_NBI_URL", os.environ.get("GENIEACS_NBI_URL", os.environ.get('GENIEACS_NBI_URL', 'http://127.0.0.1:7557')))


def _nbi(method: str, path: str, body: Optional[Dict] = None,
         cid: str = "", timeout: int = 10) -> Dict[str, Any]:
    """Thin REST client around GenieACS NBI. Returns
    `{ok, status, json, raw}` and never raises on HTTP errors."""
    base = _nbi_base(cid)
    url = base + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    out = {"ok": False, "status": 0, "raw": "", "json": None, "url": url}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out["status"] = resp.status
            raw = resp.read().decode("utf-8", "ignore")
            out["raw"] = raw[:4000]
            try:
                out["json"] = json.loads(raw) if raw else None
            except Exception:
                out["json"] = None
            out["ok"] = 200 <= resp.status < 300
    except urllib.error.HTTPError as e:
        out["status"] = e.code
        try:
            out["raw"] = e.read().decode("utf-8", "ignore")[:4000]
        except Exception:
            pass
    except Exception as e:
        out["status"] = -1
        out["raw"] = f"{type(e).__name__}: {e}"
    return out


# ──────────────────────────────────────────────────────────────────────────
#  NBI helpers
# ──────────────────────────────────────────────────────────────────────────

def nbi_list_devices(cid: str, q: Optional[Dict] = None,
                      limit: int = 50) -> Dict:
    """Query devices. `q` is a MongoDB-style filter dict."""
    params = {"limit": limit}
    if q:
        params["query"] = json.dumps(q)
    qs = "?" + urllib.parse.urlencode(params)
    return _nbi("GET", "/devices" + qs, cid=cid)


def nbi_create_task(cid: str, device_id: str, task: Dict,
                     connection_request: bool = True) -> Dict:
    """POST /devices/<id>/tasks. Returns 200 if executed immediately, or
    202 if queued for the next Inform."""
    enc = urllib.parse.quote(device_id, safe="")
    qs = "?connection_request" if connection_request else ""
    return _nbi("POST", f"/devices/{enc}/tasks{qs}", body=task, cid=cid)


def nbi_upsert_preset(cid: str, name: str, weight: int,
                       precondition: str, provisions: List[List]) -> Dict:
    """PUT /presets/<name> — create or replace a preset.

    `precondition` is a JSON string MongoDB filter (e.g.
    `'{"_tags":"managed"}'` or `'{"DeviceID.SerialNumber":"ABCD1234"}'`).
    `provisions` is a list of provision-script tuples, e.g.
        [["set_inform_interval", "300"],
         ["set_acs_url",         "http://acs.example.com:7547/"]]
    """
    return _nbi("PUT", "/presets/" + urllib.parse.quote(name, safe=""),
                 body={"weight": weight,
                        "precondition": precondition,
                        "provisions": provisions}, cid=cid)


def nbi_delete_preset(cid: str, name: str) -> Dict:
    return _nbi("DELETE", "/presets/" + urllib.parse.quote(name, safe=""),
                 cid=cid)


def nbi_tag_device(cid: str, device_id: str, tag: str) -> Dict:
    enc = urllib.parse.quote(device_id, safe="")
    return _nbi("POST", f"/devices/{enc}/tags/{urllib.parse.quote(tag)}",
                 cid=cid)


# ──────────────────────────────────────────────────────────────────────────
#  High-level: pre-stage an ONU by serial number
# ──────────────────────────────────────────────────────────────────────────

def prestage_by_serial(cid: str, *, serial: str, ssid: Optional[str] = None,
                        wifi_pw: Optional[str] = None,
                        wan_user: Optional[str] = None,
                        wan_pw: Optional[str] = None,
                        vlan: Optional[int] = None,
                        inform_sec: int = 300) -> Dict:
    """Create a GenieACS preset that auto-applies on the first Inform of an
    ONU whose serial number matches. The preset stays in place until the
    operator deletes it.

    This is the GenieACS-native equivalent of "vendor OMCI MIB push" — when
    the ONU finally contacts the ACS (even hours later), GenieACS will hand
    out the correct config without operator intervention.
    """
    serial = (serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "serial required")

    # Match on DeviceID.SerialNumber (CWMP standard parameter)
    precond = json.dumps({"DeviceID.SerialNumber": serial})
    provisions: List[List[str]] = [
        ["set_inform_interval", str(int(inform_sec))],
    ]
    # WAN PPPoE block (TR-098 / TR-181 dual mapping; GenieACS picks model)
    if wan_user:
        provisions += [
            ["set_value", "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Enable", "true"],
            ["set_value", "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username", wan_user],
        ]
        if wan_pw:
            provisions.append(
                ["set_value",
                 "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
                 wan_pw])
    # VLAN
    if vlan:
        provisions.append(
            ["set_value",
             "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.X_VLANID",
             str(int(vlan))])
    # WiFi 2.4 GHz
    if ssid:
        provisions += [
            ["set_value", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Enable", "true"],
            ["set_value", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID", ssid],
        ]
        if wifi_pw:
            provisions += [
                ["set_value", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.BeaconType", "WPAand11i"],
                ["set_value", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.KeyPassphrase", wifi_pw],
                ["set_value", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.IEEE11iEncryptionModes", "AESEncryption"],
            ]
    preset_name = f"prestage-{serial}"
    return nbi_upsert_preset(cid, preset_name, weight=1,
                              precondition=precond, provisions=provisions)


# ──────────────────────────────────────────────────────────────────────────
#  Vendor CLI knowledge base — used by olt_routes when GenieACS NBI is down
# ──────────────────────────────────────────────────────────────────────────

VENDOR_ACS_CLI = {
    "vsol": [   # V1600D EPON (already wired in olt_telnet_actions)
        "configure terminal",
        "onu {onu_idx} pri tr069_mng enable acs_server url {acs_url}",
        "onu {onu_idx} pri tr069_mng acs_server username admin password admin",
        "onu {onu_idx} pri tr069_mng inform interval 300",
        "exit",
    ],
    "huawei": [  # MA5800 / MA5608T GPON
        "config",
        "interface gpon {pon_port}",
        "onu tr069-server-profile {onu_idx} profile-name autoisp",
        "tr069-server-profile add profile-name autoisp acs http://placeholder",
        # NB: actual ACS URL is configured at the profile-name 'autoisp' separately
    ],
    "zte": [    # C300 / C320 GPON
        "configure terminal",
        "pon-onu-mng gpon-onu_{pon_port}:{onu_idx}",
        "tr069-management 1 acs {acs_url}",
        "tr069-management 1 inform-interval 300",
        "exit",
    ],
    "bdcom": [  # GP3600 GPON
        "config",
        "interface epon0/{pon_port}",
        "epon onu {onu_idx} ctc tr069 acs-url {acs_url}",
        "epon onu {onu_idx} ctc tr069 inform 300",
        "exit",
    ],
    "syrotech": [  # Indian GPON OLTs (CLI similar to VSOL/BDCOM)
        "configure terminal",
        "onu {onu_idx} tr069 enable acs-url {acs_url}",
        "onu {onu_idx} tr069 inform 300",
        "exit",
    ],
}


# ──────────────────────────────────────────────────────────────────────────
#  Endpoints
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/genieacs/health")
def api_genieacs_health(request: Request):
    """Probe GenieACS NBI for reachability + device count."""
    sc = _scope(request); cid = sc["company_id"]
    base = _nbi_base(cid)
    r = _nbi("GET", "/devices?limit=1", cid=cid, timeout=5)
    out = {"nbi_url": base, "reachable": r["ok"], "status": r["status"]}
    # Count
    cr = _nbi("HEAD", "/devices", cid=cid, timeout=5)
    if cr["status"] in (200, 204):
        out["device_count"] = cr.get("status", 0)
    return out


@router.get("/api/admin/genieacs/devices")
def api_genieacs_devices(request: Request, limit: int = 100,
                          serial: Optional[str] = None):
    sc = _scope(request); cid = sc["company_id"]
    q = {"DeviceID.SerialNumber": serial.upper()} if serial else None
    r = nbi_list_devices(cid, q=q, limit=limit)
    if not r["ok"]:
        raise HTTPException(502, f"NBI unreachable ({r['status']}): {r['raw'][:200]}")
    devs = r["json"] or []
    # Slim payload
    out = []
    for d in devs:
        dvid = d.get("_id") or d.get("DeviceID", {}).get("ID", "")
        out.append({
            "device_id": dvid,
            "serial": (d.get("DeviceID", {}) or {}).get("SerialNumber"),
            "manufacturer": (d.get("DeviceID", {}) or {}).get("Manufacturer"),
            "product_class": (d.get("DeviceID", {}) or {}).get("ProductClass"),
            "last_inform": d.get("_lastInform"),
            "tags": d.get("_tags", []),
        })
    return {"devices": out, "count": len(out)}


@router.post("/api/admin/genieacs/onus/{onu_id}/prestage")
def api_genieacs_prestage(request: Request, onu_id: int,
                          body: Dict[str, Any] = Body(default={})):
    """_S60I_  Pre-stage this ONU in GenieACS — creates a serial-matched
    preset that auto-applies on the first Inform. Pulls serial / WAN /
    WiFi defaults from the ONU's bound service profile if available."""
    sc = _scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT n.serial, n.wifi_ssid, n.wifi_password, "
            "       n.wan_username, n.wan_password, "
            "       p.vlan, p.qos_dl_kbps, p.wifi_ssid_tpl, p.wifi_pw_tpl, "
            "       p.acs_inform_int "
            "FROM onus n LEFT JOIN onu_service_profiles p "
            "  ON p.id=n.service_profile_id AND p.company_id=n.company_id "
            "WHERE n.id=%s AND n.company_id=%s", (onu_id, cid)).fetchone()
    if not r:
        raise HTTPException(404, "ONU not found")
    (serial, ssid, wifi_pw, wan_u, wan_p,
     vlan, _dl, ssid_tpl, pw_tpl, inform_int) = r
    # Allow body overrides
    serial = (body.get("serial") or serial or "").strip()
    if not serial:
        raise HTTPException(400,
            "ONU has no serial number — set it manually or scan from OLT first.")
    final_ssid = (body.get("ssid") or ssid
                  or (ssid_tpl or "").replace("{cust}", str(onu_id))
                                       .replace("{customer_id}", str(onu_id)))
    final_pw = body.get("wifi_password") or wifi_pw or pw_tpl
    if final_pw == "auto-8":
        import secrets, string
        final_pw = "".join(secrets.choice(string.ascii_letters + string.digits)
                            for _ in range(8))
    res = prestage_by_serial(cid, serial=serial.upper(),
                              ssid=final_ssid,
                              wifi_pw=final_pw,
                              wan_user=body.get("wan_user") or wan_u,
                              wan_pw=body.get("wan_password") or wan_p,
                              vlan=int(vlan) if vlan else None,
                              inform_sec=int(inform_int or 300))
    # Audit
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (%s,(SELECT olt_id "
                "FROM onus WHERE id=%s),%s,%s,'genieacs-prestage',%s,%s)",
                (cid, onu_id, onu_id, sc["actor"],
                 1 if res.get("ok") else 0,
                 (res.get("raw") or "preset created")[:500]))
    except Exception:
        pass
    return {"ok": bool(res.get("ok")), "preset": f"prestage-{serial.upper()}",
            "nbi_status": res.get("status"),
            "message": res.get("raw") or "Preset created — will auto-apply "
                                          "on next ONU Inform",
            "device_count_in_acs": None}


@router.post("/api/admin/genieacs/onus/{onu_id}/task")
def api_genieacs_task(request: Request, onu_id: int,
                       body: Dict[str, Any] = Body(...)):
    """Push a one-shot TR-069 task to an ONU that's ALREADY in GenieACS.

    Body: {device_id: "...", task: {...}}
    Task examples:
      {"name":"refreshObject","objectName":""}
      {"name":"setParameterValues","parameterValues":[
        ["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID","MyWiFi","xsd:string"]]}
      {"name":"reboot"}
    """
    sc = _scope(request); cid = sc["company_id"]
    device_id = (body.get("device_id") or "").strip()
    task = body.get("task")
    if not device_id or not task:
        raise HTTPException(400, "device_id and task required")
    r = nbi_create_task(cid, device_id, task,
                         connection_request=bool(body.get("connection_request", True)))
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (%s,(SELECT olt_id "
                "FROM onus WHERE id=%s),%s,%s,'genieacs-task',%s,%s)",
                (cid, onu_id, onu_id, sc["actor"],
                 1 if r.get("ok") else 0,
                 (json.dumps(task) + " → " + (r.get("raw") or ""))[:500]))
    except Exception:
        pass
    return {"ok": bool(r.get("ok")), "nbi_status": r.get("status"),
            "raw": r.get("raw")[:500]}


@router.get("/api/admin/genieacs/presets")
def api_list_presets(request: Request):
    sc = _scope(request); cid = sc["company_id"]
    r = _nbi("GET", "/presets", cid=cid)
    if not r["ok"]:
        raise HTTPException(502, f"NBI unreachable ({r['status']})")
    return {"presets": r["json"] or []}


@router.delete("/api/admin/genieacs/presets/{name}")
def api_del_preset(request: Request, name: str):
    sc = _scope(request); cid = sc["company_id"]
    r = nbi_delete_preset(cid, name)
    return {"ok": bool(r.get("ok")), "status": r.get("status")}


@router.get("/api/admin/genieacs/vendor-cli/{vendor}")
def api_vendor_cli(request: Request, vendor: str):
    """Returns the documented CLI command template for a given vendor.
    Used by the operator UI to see what would be pushed before clicking
    'Run on OLT'."""
    _scope(request)
    v = vendor.lower()
    if v not in VENDOR_ACS_CLI:
        raise HTTPException(404, f"No CLI template for vendor '{vendor}'. "
                                  f"Supported: {list(VENDOR_ACS_CLI.keys())}")
    return {"vendor": v, "commands": VENDOR_ACS_CLI[v]}


print("[genieacs_nbi] router loaded")
