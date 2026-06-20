"""_S57C_PON_TREE_  Phase 5 — PON Tree Visualizer route.

Single read-only page that groups every ONU under its parent PON port and
parent OLT, with live RX power tags. Built on top of the existing onus +
olts tables — no schema changes."""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from typing import Any, Dict, List

from database import engine
from sqlalchemy import text as _t

# Reuse existing helpers from olt_routes — keeps tenant scope + layout
# template path identical to MDU / Outage / Triple-play pages.
from olt_routes import _require_scope, _portal_context, templates  # type: ignore

router = APIRouter()


@router.get("/admin/olt/pon-tree", response_class=HTMLResponse)
def pon_tree_page(request: Request):
    sc = _require_scope(request)
    cid = sc["company_id"]
    olts: List[Dict[str, Any]] = []
    with engine.begin() as conn:
        olt_rows = conn.execute(_t(
            "SELECT id, name, vendor, host FROM olts "
            "WHERE company_id=:c ORDER BY id"
        ), {"c": cid}).fetchall()
        for o in olt_rows:
            olt_id, name, vendor, host = o
            onu_rows = conn.execute(_t(
                "SELECT n.id, n.pon_port_index, n.onu_index, n.serial, n.mac, "
                "       n.status, n.rx_power, n.name, n.customer_id, "
                "       c.customer_name "
                "FROM onus n LEFT JOIN customers c "
                "  ON c.customer_id = n.customer_id "
                "  AND c.company_id = n.company_id "
                "WHERE n.olt_id=:oid AND n.company_id=:cid "
                "ORDER BY n.pon_port_index, n.onu_index"
            ), {"oid": olt_id, "cid": cid}).fetchall()
            pons_map: Dict[int, Dict[str, Any]] = {}
            for r in onu_rows:
                (nid, port_idx, idx, serial, mac, status,
                 rx_power, name_, customer_id, customer_name) = r
                p = pons_map.setdefault(port_idx or 0,
                    {"port_index": port_idx or 0, "onus": []})
                p["onus"].append({
                    "id": nid, "onu_index": idx,
                    "serial": serial, "mac": mac, "status": status,
                    "rx_power": float(rx_power) if rx_power is not None else None,
                    "name": name_, "customer_name": customer_name,
                })
            pons = [pons_map[k] for k in sorted(pons_map.keys())]
            olts.append({
                "id": olt_id, "name": name, "vendor": vendor, "host": host,
                "pon_count": len(pons),
                "onu_count": sum(len(p["onus"]) for p in pons),
                "pons": pons,
            })
    ctx = _portal_context(request, sc, "pon_tree")
    ctx.update({"olts": olts, "page_title": "PON Tree", "active_page": "pon_tree"})
    return templates.TemplateResponse("admin_pon_tree.html", ctx)
