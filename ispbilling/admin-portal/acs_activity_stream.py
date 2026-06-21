"""acs_activity_stream.py — Phase-C "Provisioning Activity Toast"
=================================================================

SSE endpoint that streams real-time provisioning events to admin UIs.

Subscribed sources (PostgreSQL, polled once per ~2 s):
  • acs_push_log              — GenieACS push outcomes (PPPoE / WAN / reboot)
  • ztp_discovered_onus       — fresh ONU discovery from OLT scans
  • complaints (kind='Auto-Outage') — outage detector results
  • RPC reboot / factory-reset success/failure (already captured in acs_push_log
    with action='reboot' / 'factory-reset')

Endpoint:
  GET /api/admin/acs/activity/stream      (text/event-stream)

The endpoint is scoped to the admin's company_id (sub-LCO sessions inherit
their parent LCO scope through the existing require_auth middleware).
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import text

from olt_routes import engine, _require_scope  # reuse identical scope helper

router = APIRouter()

# Poll cadence (s) — must balance UI responsiveness vs DB load.
POLL_INTERVAL = 2.0
KEEPALIVE_S = 25.0


def _row_to_event(kind: str, row) -> dict:
    """Map a DB row → an outbound event payload."""
    return {
        "kind": kind,
        "id": int(row.id),
        "ts": int(time.time()),
        **{
            k: getattr(row, k, None)
            for k in (
                "company_id", "onu_id", "onu_serial", "customer_id",
                "reason", "ok", "skip", "error", "action", "actor",
                "olt_id", "pon_port", "onu_vendor", "onu_model",
                "rx_power_dbm", "status", "ticket_no", "priority", "subject",
            )
            if hasattr(row, k)
        },
    }


def _fetch_pushlog(cid: str, last_id: int):
    with engine.begin() as cn:
        rows = cn.execute(
            text("""
                SELECT id, company_id, onu_id, onu_serial, customer_id,
                       reason, ok, skip, error, action, actor, olt_id
                  FROM acs_push_log
                 WHERE company_id = :cid AND id > :last_id
                 ORDER BY id ASC
                 LIMIT 100
            """),
            {"cid": cid, "last_id": last_id},
        ).all()
    return rows


def _fetch_ztp(cid: str, last_id: int):
    with engine.begin() as cn:
        rows = cn.execute(
            text("""
                SELECT id, company_id, olt_id, pon_port, onu_serial,
                       onu_vendor, onu_model, rx_power_dbm, status
                  FROM ztp_discovered_onus
                 WHERE company_id = :cid AND id > :last_id
                 ORDER BY id ASC
                 LIMIT 100
            """),
            {"cid": cid, "last_id": last_id},
        ).all()
    return rows


def _fetch_outages(cid: str, last_id: int):
    with engine.begin() as cn:
        rows = cn.execute(
            text("""
                SELECT id, company_id, ticket_no, priority,
                       subject, status
                  FROM complaints
                 WHERE company_id = :cid AND id > :last_id
                   AND kind = 'Auto-Outage'
                 ORDER BY id ASC
                 LIMIT 50
            """),
            {"cid": cid, "last_id": last_id},
        ).all()
    return rows


def _initial_cursors(cid: str) -> dict:
    """Pick the current MAX(id) for each source so we never replay history."""
    cur = {"acs": 0, "ztp": 0, "outage": 0}
    with engine.begin() as cn:
        for src, tbl, where in (
            ("acs", "acs_push_log", ""),
            ("ztp", "ztp_discovered_onus", ""),
            ("outage", "complaints", " AND kind='Auto-Outage'"),
        ):
            r = cn.execute(
                text(f"SELECT COALESCE(MAX(id),0) AS m FROM {tbl} "
                     f"WHERE company_id = :cid{where}"),
                {"cid": cid},
            ).first()
            cur[src] = int(r.m or 0)
    return cur


@router.get("/api/admin/acs/activity/stream")
async def acs_activity_stream(request: Request):
    """SSE: stream new ACS / ZTP / Outage events as they are written to PG."""
    sc = _require_scope(request)
    if isinstance(sc, JSONResponse):
        return sc
    cid = sc["company_id"]

    async def gen() -> AsyncGenerator[bytes, None]:
        cursors = _initial_cursors(cid)
        yield (
            "event: hello\n"
            f"data: {json.dumps({'cid': cid, 'cursors': cursors, 'ts': int(time.time())})}\n\n"
        ).encode()
        last_keepalive = time.time()

        while True:
            if await request.is_disconnected():
                break
            try:
                for src, fetcher, kind in (
                    ("acs", _fetch_pushlog, "acs_push"),
                    ("ztp", _fetch_ztp,     "ztp_discovery"),
                    ("outage", _fetch_outages, "outage"),
                ):
                    rows = fetcher(cid, cursors[src])
                    for r in rows:
                        ev = _row_to_event(kind, r)
                        # Friendlier human message
                        if kind == "acs_push":
                            act = (ev.get("action") or "").lower()
                            ok = ev.get("ok")
                            ev["message"] = (
                                f"{'✅' if ok else '❌'} "
                                f"{act or 'push'} on ONU "
                                f"{ev.get('onu_serial') or ev.get('onu_id')}"
                                + (f"  ({ev.get('error')})" if not ok and ev.get('error') else "")
                            )
                        elif kind == "ztp_discovery":
                            ev["message"] = (
                                f"🆕 ZTP discovered ONU "
                                f"{ev.get('onu_serial')} on OLT {ev.get('olt_id')} "
                                f"PON {ev.get('pon_port')}"
                            )
                        elif kind == "outage":
                            ev["message"] = (
                                f"🚨 OUTAGE {ev.get('ticket_no')} "
                                f"[{ev.get('priority')}] — {ev.get('subject')}"
                            )
                        yield (
                            f"event: {kind}\n"
                            f"data: {json.dumps(ev, default=str)}\n\n"
                        ).encode()
                        cursors[src] = ev["id"]
                if time.time() - last_keepalive > KEEPALIVE_S:
                    yield b": keepalive\n\n"
                    last_keepalive = time.time()
            except Exception as e:
                yield (
                    "event: error\n"
                    f"data: {json.dumps({'err': str(e)})}\n\n"
                ).encode()
            await asyncio.sleep(POLL_INTERVAL)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering for live UX
            "Connection": "keep-alive",
        },
    )


@router.get("/api/admin/acs/activity/recent")
async def acs_activity_recent(request: Request, limit: int = 50):
    """JSON fallback / history panel — last N events across all sources."""
    sc = _require_scope(request)
    if isinstance(sc, JSONResponse):
        return sc
    cid = sc["company_id"]
    limit = max(1, min(int(limit), 200))
    with engine.begin() as cn:
        acs = cn.execute(text(
            "SELECT 'acs_push' AS kind, id, company_id, onu_id, onu_serial, "
            "       customer_id, reason, ok, skip, error, action, actor, olt_id, "
            "       created_at "
            "  FROM acs_push_log "
            " WHERE company_id = :cid "
            " ORDER BY id DESC LIMIT :lim"
        ), {"cid": cid, "lim": limit}).mappings().all()
        ztp = cn.execute(text(
            "SELECT 'ztp_discovery' AS kind, id, company_id, olt_id, "
            "       pon_port, onu_serial, onu_vendor, onu_model, "
            "       rx_power_dbm, status, created_at "
            "  FROM ztp_discovered_onus "
            " WHERE company_id = :cid "
            " ORDER BY id DESC LIMIT :lim"
        ), {"cid": cid, "lim": limit}).mappings().all()
        out = cn.execute(text(
            "SELECT 'outage' AS kind, id, company_id, ticket_no, "
            "       priority, subject, status, created_at "
            "  FROM complaints "
            " WHERE company_id = :cid AND kind='Auto-Outage' "
            " ORDER BY id DESC LIMIT :lim"
        ), {"cid": cid, "lim": limit}).mappings().all()

    merged = [dict(r) for r in (*acs, *ztp, *out)]
    merged.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return {"events": merged[:limit], "company_id": cid}
