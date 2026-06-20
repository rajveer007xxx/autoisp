"""_S57E_  Module 10 — Live NOC Dashboard.

Aggregates real-time state from multiple tables into a single page that
operators leave on a wall-mounted screen. Polls every 5 s via JS.

Sources used (existing tables):
  olts                          → online/offline by status
  onus                          → online/offline by status
  outage_events_v2              → active outages
  signal_degradation_events_v2  → predicted failures (AI)
  onu_signal_alerts             → 24h signal alerts
  fiber_cut_history             → recent fiber cuts
  onu_signal_samples            → top-RX-drift ONUs
"""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Any, Dict, List

from database import engine
from sqlalchemy import text as _t

from olt_routes import _require_scope, _portal_context, templates  # type: ignore

router = APIRouter()


def _safe_count(conn, sql: str, params: Dict[str, Any]) -> int:
    try:
        v = conn.execute(_t(sql), params).scalar()
        return int(v or 0)
    except Exception:
        return 0


def _noc_payload(cid: str) -> Dict[str, Any]:
    """_S58A_NOC_FIX_  Each block runs in its own short-lived connection
    so one failing query can never poison sibling queries."""
    out: Dict[str, Any] = {"company_id": cid}

    def q(sql: str, params: Dict[str, Any]):
        """Execute one SELECT in its own connection; return rows or []."""
        try:
            with engine.connect() as conn:
                return conn.execute(_t(sql), params).fetchall()
        except Exception as e:
            import logging
            logging.getLogger("noc").warning("noc query failed: %s", e)
            return []

    def cnt(sql: str, params: Dict[str, Any]) -> int:
        try:
            with engine.connect() as conn:
                v = conn.execute(_t(sql), params).scalar()
                return int(v or 0)
        except Exception:
            return 0

    # OLT counters
    out["olts_total"]   = cnt("SELECT COUNT(*) FROM olts WHERE company_id=:c", {"c": cid})
    out["olts_online"]  = cnt(
        "SELECT COUNT(*) FROM olts WHERE company_id=:c AND COALESCE(enabled,1)=1 "
        "AND COALESCE(status,'online') IN ('online','up','ok')", {"c": cid})
    out["olts_offline"] = max(0, out["olts_total"] - out["olts_online"])

    # ONU counters
    out["onus_total"]   = cnt("SELECT COUNT(*) FROM onus WHERE company_id=:c", {"c": cid})
    out["onus_online"]  = cnt("SELECT COUNT(*) FROM onus WHERE company_id=:c AND status='online'", {"c": cid})
    out["onus_offline"] = cnt("SELECT COUNT(*) FROM onus WHERE company_id=:c AND status='offline'", {"c": cid})
    out["onus_unknown"] = max(0, out["onus_total"] - out["onus_online"] - out["onus_offline"])

    # Customers
    out["customers_total"] = cnt("SELECT COUNT(*) FROM customers WHERE company_id=:c", {"c": cid})

    # Active outages — schema: id, kind, severity, scope_kind, scope_id,
    # details(JSON), opened_at, closed_at, ack_by. The render expects
    # onu_count + customer_count, which the outage correlator stores in
    # `details` json.
    import json as _json
    out["active_outages"] = []
    for x in q(
        "SELECT id, kind, severity, scope_kind, scope_id, details, opened_at "
        "FROM outage_events_v2 "
        "WHERE company_id=:c AND closed_at IS NULL "
        "ORDER BY opened_at DESC LIMIT 5", {"c": cid}
    ):
        try:
            det = _json.loads(x[5]) if x[5] else {}
        except Exception:
            det = {}
        out["active_outages"].append({
            "id": x[0],
            "kind": x[1],
            "severity": x[2],
            "scope_kind": x[3],
            "scope_id":  x[4],
            "opened_at": str(x[6]),
            "onu_count": int(det.get("affected_onus") or det.get("onu_count") or det.get("affected_onu_count") or 0),
            "customer_count": int(det.get("affected_customers") or det.get("customer_count") or det.get("affected_customer_count") or 0),
            "suspected_cause": det.get("suspected_cause") or det.get("cause"),
            "status": "open",
        })

    # AI predicted failures
    out["predicted_failures"] = []
    for x in q(
        "SELECT d.id, d.onu_id, d.slope_db_per_day, d.predicted_fail_in_days, "
        "       n.serial, n.name, n.rx_power "
        "FROM signal_degradation_events_v2 d "
        "LEFT JOIN onus n ON n.id=d.onu_id AND n.company_id=d.company_id "
        "WHERE d.company_id=:c AND d.closed_at IS NULL "
        "ORDER BY d.predicted_fail_in_days ASC NULLS LAST LIMIT 8", {"c": cid}
    ):
        out["predicted_failures"].append({
            "id": x[0], "onu_id": x[1],
            "slope": round(float(x[2] or 0), 3),
            "days_left": round(float(x[3] or 0), 1),
            "serial": x[4], "name": x[5],
            "rx_power": float(x[6]) if x[6] is not None else None,
        })
    out["predicted_failures_count"] = cnt(
        "SELECT COUNT(*) FROM signal_degradation_events_v2 "
        "WHERE company_id=:c AND closed_at IS NULL", {"c": cid})

    # Signal alerts (last 24h)
    out["signal_alerts_24h"] = cnt(
        "SELECT COUNT(*) FROM onu_signal_alerts "
        "WHERE company_id=:c AND created_at::timestamptz >= NOW() - INTERVAL '24 hours'",
        {"c": cid})

    # Fiber cuts (last 7d)
    out["fiber_cuts_recent"] = []
    for x in q(
        "SELECT id, cut_at, cut_lat, cut_lng, original_fiber_id "
        "FROM fiber_cut_history "
        "WHERE company_id=:c "
        "  AND cut_at::timestamptz >= NOW() - INTERVAL '7 days' "
        "ORDER BY cut_at DESC LIMIT 5", {"c": cid}
    ):
        out["fiber_cuts_recent"].append({
            "id": x[0], "cut_at": str(x[1]),
            "lat": float(x[2]) if x[2] is not None else None,
            "lng": float(x[3]) if x[3] is not None else None,
            "fiber_id": x[4],
        })

    # Worst-RX ONUs
    out["worst_rx_onus"] = [
        {"id": x[0], "serial": x[1], "mac": x[2], "rx_power": float(x[3])}
        for x in q(
            "SELECT id, serial, mac, rx_power FROM onus "
            "WHERE company_id=:c AND rx_power IS NOT NULL AND status='online' "
            "ORDER BY rx_power ASC LIMIT 5", {"c": cid}
        )
    ]

    # Top traffic (last 30 min mean) — column is rx_mbps, not rx_kbps
    out["top_traffic"] = [
        {"onu_id": x[0], "serial": x[1], "name": x[2],
         "avg_mbps": round(float(x[3] or 0), 1)}
        for x in q(
            "SELECT s.onu_id, n.serial, n.name, AVG(s.rx_mbps) AS avg_mbps "
            "FROM onu_traffic_samples s "
            "LEFT JOIN onus n ON n.id=s.onu_id AND n.company_id=s.company_id "
            "WHERE s.company_id=:c "
            "  AND s.ts::timestamptz >= NOW() - INTERVAL '30 minutes' "
            "GROUP BY s.onu_id, n.serial, n.name "
            "ORDER BY avg_mbps DESC NULLS LAST LIMIT 5", {"c": cid}
        )
    ]

    return out



@router.get("/api/admin/noc/feed")
def api_noc_feed(request: Request):
    sc = _require_scope(request)
    return JSONResponse(_noc_payload(sc["company_id"]))


@router.get("/admin/noc", response_class=HTMLResponse)
def noc_page(request: Request):
    sc = _require_scope(request)
    ctx = _portal_context(request, sc, "noc")
    return templates.TemplateResponse("admin_noc.html", ctx)
