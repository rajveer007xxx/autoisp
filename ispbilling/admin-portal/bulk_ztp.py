"""
_S58G_BULK_ZTP_  Bulk ZTP wizard.

  POST /api/admin/olt/{olt_id}/bulk-ztp
    body: { dry_run: bool = false, target: "online" | "all" = "online" }
    response: {
        ok, olt_id, total, attempted, succeeded, failed,
        results: [ {onu_id, serial, status, ok, error?, output_excerpt?} ],
        elapsed_s
    }

  GET  /api/admin/olt/{olt_id}/bulk-ztp/report
    Compact summary of the last completed run (held in memory per worker).

The wizard runs `zero-touch-provision` against every online ONU on the
chosen OLT, then schedules a 90-second background re-check that queries
GenieACS for each MAC and updates `onus.last_acs_inform`. The endpoint
returns immediately with the push results so the UI can render the modal
without waiting for the ACS recheck.
"""
from __future__ import annotations
import os  # __PHASE_B2_ENV_REFACTOR__
from fastapi import APIRouter, Request, Body
from sqlalchemy import text as _text
from typing import Any, Dict, List
import time, threading, traceback, os

router = APIRouter()
_LAST_REPORT: Dict[int, Dict[str, Any]] = {}


def _require_scope(request: Request):
    # Mirror the helper used by olt_routes for tenant scoping.
    from olt_routes import _require_scope as _r
    return _r(request)


def _genieacs_seen(mac: str) -> bool:
    """Cheap check: is this MAC currently registered with GenieACS?"""
    try:
        import urllib.parse, urllib.request, json
        q = urllib.parse.quote_plus(
            '{"$or":[{"_deviceId._SerialNumber":"%s"},{"DeviceID.SerialNumber":"%s"}]}'
            % (mac, mac)
        )
        url = f"{os.environ.get('GENIEACS_NBI_URL', os.environ.get('GENIEACS_NBI_URL', 'http://127.0.0.1:7557'))}/devices?query={q}&projection=_id"
        with urllib.request.urlopen(url, timeout=2.5) as r:
            j = json.loads(r.read().decode("utf-8"))
            return isinstance(j, list) and len(j) > 0
    except Exception:
        return False


def _kick_acs_recheck(olt_id: int, items: List[Dict[str, Any]], cid: str):
    """Wait 90 s then poll GenieACS for each ONU; store the result."""
    def runner():
        time.sleep(90)
        seen, missing = 0, []
        for it in items:
            mac = it.get("mac") or ""
            if not mac:
                missing.append({"onu_id": it["onu_id"], "reason": "no_mac"})
                continue
            ok = _genieacs_seen(mac)
            if ok:
                seen += 1
                try:
                    from database import engine
                    with engine.begin() as conn:
                        conn.execute(_text(
                            "UPDATE onus SET last_acs_inform = NOW()::text "
                            "WHERE id=:i AND company_id=:c"
                        ), {"i": it["onu_id"], "c": cid})
                except Exception:
                    pass
            else:
                missing.append({"onu_id": it["onu_id"], "mac": mac, "serial": it.get("serial")})
        rep = _LAST_REPORT.get(olt_id) or {}
        rep["acs_recheck"] = {
            "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "seen_in_acs": seen, "still_missing": missing,
        }
        _LAST_REPORT[olt_id] = rep
    t = threading.Thread(target=runner, daemon=True); t.start()


@router.post("/api/admin/olt/{olt_id}/bulk-ztp")
def api_bulk_ztp(olt_id: int, request: Request,
                  body: Dict[str, Any] = Body(default={})):
    """Run zero-touch-provision against every online ONU on this OLT."""
    sc = _require_scope(request); cid = sc["company_id"]
    dry_run = bool(body.get("dry_run", False))
    target  = (body.get("target") or "online").lower()

    # Lazy imports to dodge circular deps.
    from database import engine
    try:
        from olt_routes import _ztp_one_onu  # type: ignore[attr-defined]
    except Exception:
        _ztp_one_onu = None

    started = time.time()
    rows: List[Dict[str, Any]] = []
    with engine.connect() as conn:
        q = (
            "SELECT id, serial, mac, status, pon_port_index AS pon_port, onu_index AS onu_idx "
            "FROM onus WHERE company_id=:c AND olt_id=:o"
        )
        if target == "online":
            q += " AND status='online'"
        for r in conn.execute(_text(q), {"c": cid, "o": olt_id}).fetchall():
            rows.append({"onu_id": r[0], "serial": r[1], "mac": r[2],
                          "status": r[3], "pon": r[4], "idx": r[5]})

    if dry_run:
        return {"ok": True, "olt_id": olt_id, "dry_run": True,
                "would_attempt": len(rows), "rows": rows[:50]}

    succeeded = 0; failed = 0; results: List[Dict[str, Any]] = []
    for r in rows:
        onu_id = r["onu_id"]
        try:
            if _ztp_one_onu is not None:
                # Use the same path the per-ONU endpoint uses
                outcome = _ztp_one_onu(cid, onu_id)
            else:
                # Last-resort: call the per-ONU endpoint over HTTP
                import urllib.request, json as _json
                req = urllib.request.Request(
                    f"{os.environ.get('ISP_ADMIN_URL', os.environ.get('ISP_ADMIN_URL', 'http://127.0.0.1:8001'))}/api/admin/olt/onus/{onu_id}/zero-touch-provision",
                    method="POST",
                    headers={"Content-Type": "application/json",
                              "Cookie": request.headers.get("cookie", "")},
                    data=b"{}",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    outcome = _json.loads(resp.read().decode("utf-8"))
            ok = bool(outcome.get("ok"))
            if ok: succeeded += 1
            else:  failed += 1
            results.append({
                "onu_id": onu_id, "serial": r["serial"], "mac": r["mac"],
                "ok": ok,
                "error": outcome.get("error"),
                "wan_ok":  bool((outcome.get("wan")  or {}).get("ok")) if isinstance(outcome.get("wan"),  dict) else None,
                "wifi_ok": bool((outcome.get("wifi") or {}).get("ok")) if isinstance(outcome.get("wifi"), dict) else None,
                "tr069_ok":bool((outcome.get("tr069") or {}).get("ok")) if isinstance(outcome.get("tr069"), dict) else None,
            })
        except Exception as e:
            failed += 1
            results.append({"onu_id": onu_id, "serial": r["serial"],
                             "mac": r["mac"], "ok": False,
                             "error": f"exception: {e}"})

    elapsed = round(time.time() - started, 1)
    report = {
        "ok": True, "olt_id": olt_id,
        "total": len(rows), "attempted": len(rows),
        "succeeded": succeeded, "failed": failed,
        "elapsed_s": elapsed, "results": results,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
    }
    _LAST_REPORT[olt_id] = report
    # Schedule 90-second ACS re-check in the background.
    _kick_acs_recheck(olt_id, results, cid)
    return report


@router.get("/api/admin/olt/{olt_id}/bulk-ztp/report")
def api_bulk_ztp_report(olt_id: int, request: Request):
    sc = _require_scope(request)  # noqa: F841 — auth check only
    return _LAST_REPORT.get(olt_id) or {"ok": False, "error": "no report yet"}
