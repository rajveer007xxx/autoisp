"""_S57E_  Module 6 — End-to-End Subscriber Fiber-Path Trace.

Walks the graph: customer → ONU → network_hardware → fibers → splitters/joints → OLT port.

Schema used (already in DB):
  customers           — customer_id, customer_name, customer_phone, plan_name, lat, lng
  onus                — id, olt_id, customer_id, serial, mac, rx_power, tx_power, lat, lng, status
  olts                — id, name, vendor, host, lat, lng (if present)
  network_hardware    — id, kind, name, lat, lng, ref_olt_id, ref_onu_id, parent_id, props_json
  network_fiber       — id, name, color, core_count, src_hw_id, dst_hw_id, polyline_json, length_m
  fiber_splice        — id, node_hw_id, src_fiber_id, src_core, dst_fiber_id, dst_core, mode, loss_db

Algorithm:
  1. Locate the customer's ONU.
  2. Find network_hardware row of kind='onu' with ref_onu_id == onu.id.
  3. BFS through fibers + splices toward an OLT-kind node, building the hop list.
  4. Compute path length + RX power + estimated loss budget.

Always returns a JSON payload — never crashes the request even on partial graph."""
from __future__ import annotations
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse
from typing import Any, Dict, List, Optional, Set
import json
import math

from database import engine
from sqlalchemy import text as _t

from olt_routes import _require_scope, _portal_context, templates  # type: ignore

router = APIRouter()


# ───────────────────────────────────────────────────────────────────────
def _hw_neighbors(conn, cid: str, hw_id: int) -> List[Dict[str, Any]]:
    """Return fibers connected to a given hardware id, plus the neighbor hw_id."""
    rows = conn.execute(_t(
        "SELECT id, name, color, core_count, src_hw_id, dst_hw_id, length_m "
        "FROM network_fiber "
        "WHERE company_id=:c AND (src_hw_id=:h OR dst_hw_id=:h)"
    ), {"c": cid, "h": hw_id}).fetchall()
    out = []
    for fid, fname, fcolor, ccount, sh, dh, length_m in rows:
        neighbor = dh if sh == hw_id else sh
        if neighbor is None:
            continue
        out.append({
            "fiber_id": fid, "fiber_name": fname or f"F#{fid}",
            "color": fcolor, "core_count": ccount,
            "length_m": float(length_m) if length_m is not None else None,
            "neighbor_hw_id": neighbor,
        })
    return out


def _hw_info(conn, cid: str, hw_id: int) -> Optional[Dict[str, Any]]:
    r = conn.execute(_t(
        "SELECT id, kind, name, lat, lng, ref_olt_id, ref_onu_id, parent_id "
        "FROM network_hardware WHERE company_id=:c AND id=:i LIMIT 1"
    ), {"c": cid, "i": hw_id}).fetchone()
    if not r:
        return None
    return {"id": r[0], "kind": r[1], "name": r[2] or f"#{r[0]}",
            "lat": float(r[3]) if r[3] is not None else None,
            "lng": float(r[4]) if r[4] is not None else None,
            "ref_olt_id": r[5], "ref_onu_id": r[6], "parent_id": r[7]}


def _bfs_to_olt(conn, cid: str, start_hw_id: int, max_depth: int = 12
                ) -> Optional[List[Dict[str, Any]]]:
    """BFS from a starting hardware id towards a node of kind 'olt'.
    Returns the ordered list of hops (alternating hardware → fiber → hardware →…)."""
    visited: Set[int] = {start_hw_id}
    # Each queue entry is (current_hw_id, path)
    queue: List = [(start_hw_id, [])]
    while queue:
        cur_id, path = queue.pop(0)
        cur = _hw_info(conn, cid, cur_id)
        if not cur:
            continue
        new_path = path + [{"type": "hardware", "data": cur}]
        if cur["kind"] == "olt" or cur["ref_olt_id"]:
            return new_path
        if len(new_path) // 2 >= max_depth:
            continue
        for nb in _hw_neighbors(conn, cid, cur_id):
            if nb["neighbor_hw_id"] in visited:
                continue
            visited.add(nb["neighbor_hw_id"])
            queue.append((nb["neighbor_hw_id"], new_path + [{"type": "fiber", "data": nb}]))
    return None


def _trace_payload(cid: str, customer_id: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ok": False, "customer_id": customer_id, "hops": [],
        "warnings": [], "stats": {},
    }
    with engine.begin() as conn:
        # 1. Customer
        cr = conn.execute(_t(
            "SELECT c.customer_id, c.customer_name, c.customer_phone, "
            "       COALESCE(p.plan_name, c.service_type) AS plan_name, "
            "       c.latitude, c.longitude "
            "FROM customers c "
            "LEFT JOIN plans p ON p.id=c.plan_id AND p.company_id=c.company_id "
            "WHERE c.company_id=:c AND c.customer_id=:cid LIMIT 1"
        ), {"c": cid, "cid": customer_id}).fetchone()
        if not cr:
            out["error"] = "customer not found"
            return out
        out["customer"] = {
            "customer_id": cr[0], "name": cr[1], "phone": cr[2],
            "plan": cr[3],
            "lat": float(cr[4]) if cr[4] is not None else None,
            "lng": float(cr[5]) if cr[5] is not None else None,
        }
        out["hops"].append({"type": "endpoint", "label": "Customer",
                            "data": out["customer"], "icon": "person-vcard"})

        # 2. ONU
        oo = conn.execute(_t(
            "SELECT o.id, o.olt_id, o.serial, o.mac, o.status, "
            "       o.rx_power, o.tx_power, o.lat, o.lng, "
            "       l.name AS olt_name "
            "FROM onus o LEFT JOIN olts l ON l.id=o.olt_id AND l.company_id=o.company_id "
            "WHERE o.company_id=:c AND o.customer_id=:cid LIMIT 1"
        ), {"c": cid, "cid": customer_id}).fetchone()
        if not oo:
            out["warnings"].append("No ONU linked to this customer")
            return out
        onu_id, olt_id, serial, mac, status, rx, tx, olat, olng, olt_name = oo
        onu_data = {
            "id": onu_id, "olt_id": olt_id, "serial": serial, "mac": mac,
            "status": status, "rx_power": float(rx) if rx is not None else None,
            "tx_power": float(tx) if tx is not None else None,
            "lat": float(olat) if olat is not None else None,
            "lng": float(olng) if olng is not None else None,
            "olt_name": olt_name,
        }
        out["hops"].append({"type": "device", "label": "ONU",
                            "data": onu_data, "icon": "hdd-network-fill"})
        out["stats"]["rx_power_dbm"] = onu_data["rx_power"]
        out["stats"]["tx_power_dbm"] = onu_data["tx_power"]

        # 3. Find network_hardware row for this ONU.
        r = conn.execute(_t(
            "SELECT id FROM network_hardware "
            "WHERE company_id=:c AND ref_onu_id=:o LIMIT 1"
        ), {"c": cid, "o": onu_id}).fetchone()
        if not r:
            out["warnings"].append(
                "ONU is not placed on the network map yet — "
                "trace stops at the ONU. Place it from Unmapped ONUs."
            )
            out["ok"] = True
            return out
        onu_hw_id = r[0]

        # 4. BFS to OLT.
        path = _bfs_to_olt(conn, cid, onu_hw_id, max_depth=12)
        if not path:
            out["warnings"].append(
                "No fiber path found from ONU to any OLT in the map. "
                "Draw fibers between ONU and intermediate splitter / FDB / joint hardware."
            )
            out["ok"] = True
            return out

        # 5. Build hop entries and aggregate length + estimated loss.
        total_m = 0.0
        est_loss_db = 0.0
        # Tap-loss budget per kind.
        kind_loss = {
            "splitter_1x2": 3.5, "splitter_1x4": 7.5, "splitter_1x8": 10.5,
            "splitter_1x16": 14.0, "splitter_1x32": 17.5,
            "coupler_95x5": 0.5, "coupler_90x10": 1.0, "coupler_70x30": 3.0,
            "jc_box": 0.1, "joint": 0.1, "manhole": 0.05, "pole": 0.0,
            "olt": 0.0, "onu": 0.0, "fdb": 0.4, "odb": 0.4,
        }
        for step in path:
            if step["type"] == "fiber":
                d = step["data"]
                lm = d.get("length_m") or 0.0
                total_m += lm
                # Single-mode fibre loss budget at 1490nm ≈ 0.22 dB/km.
                est_loss_db += (lm / 1000.0) * 0.22
                out["hops"].append({
                    "type": "fiber", "label": d.get("fiber_name") or "Fiber",
                    "data": d, "icon": "rulers"})
            else:
                d = step["data"]
                k = (d.get("kind") or "").lower()
                est_loss_db += kind_loss.get(k, 0.2)
                icon = {
                    "olt": "hdd-rack",
                    "splitter_1x2": "diagram-2",
                    "splitter_1x4": "diagram-2",
                    "splitter_1x8": "diagram-2",
                    "splitter_1x16": "diagram-2",
                    "splitter_1x32": "diagram-2",
                    "coupler_95x5": "intersect",
                    "coupler_90x10": "intersect",
                    "coupler_70x30": "intersect",
                    "jc_box": "box-seam",
                    "fdb": "boxes",
                    "odb": "boxes",
                    "manhole": "geo-alt",
                    "pole": "ev-station",
                }.get(k, "diagram-3")
                out["hops"].append({
                    "type": "hardware",
                    "label": (k or "node").replace("_", " ").upper(),
                    "data": d, "icon": icon})
        out["stats"]["total_length_m"] = round(total_m, 1)
        out["stats"]["estimated_loss_db"] = round(est_loss_db, 2)
        out["stats"]["hop_count"] = sum(1 for h in out["hops"] if h["type"] != "endpoint")

        # Find the OLT-port (last hop is the OLT hardware → resolve its olts.name + pon)
        last_hw = path[-1]["data"]
        if last_hw.get("ref_olt_id"):
            o2 = conn.execute(_t(
                "SELECT name, vendor, host FROM olts WHERE id=:i AND company_id=:c"
            ), {"i": last_hw["ref_olt_id"], "c": cid}).fetchone()
            if o2:
                out["olt"] = {"id": last_hw["ref_olt_id"], "name": o2[0],
                              "vendor": o2[1], "host": o2[2],
                              "pon_port": onu_data.get("olt_id") and
                                          conn.execute(_t(
                                              "SELECT pon_port_index FROM onus "
                                              "WHERE id=:o AND company_id=:c"),
                                              {"o": onu_id, "c": cid}).scalar()}

        out["ok"] = True
        return out


# ───────────────────────────────────────────────────────────────────────
@router.get("/api/admin/trace/customer/{customer_id}")
def api_trace_customer(customer_id: str, request: Request):
    sc = _require_scope(request)
    return _trace_payload(sc["company_id"], customer_id)


@router.get("/api/admin/trace/onu/{onu_id}")
def api_trace_onu(onu_id: int, request: Request):
    """Same trace, but starting from an ONU id. Looks up customer first."""
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.execute(_t(
            "SELECT customer_id FROM onus WHERE id=:i AND company_id=:c LIMIT 1"
        ), {"i": onu_id, "c": cid}).fetchone()
    if not r or not r[0]:
        return {"ok": False, "error": "ONU has no customer linked"}
    return _trace_payload(cid, r[0])


# ───────────────────────────────────────────────────────────────────────
@router.get("/admin/trace/{customer_id}", response_class=HTMLResponse)
def trace_page(customer_id: str, request: Request):
    """Stand-alone full-page trace viewer (optional)."""
    sc = _require_scope(request)
    payload = _trace_payload(sc["company_id"], customer_id)
    ctx = _portal_context(request, sc, "trace")
    ctx.update({"trace": payload, "customer_id_str": customer_id})
    return templates.TemplateResponse("admin_trace.html", ctx)
