"""Load Balancing routes — page renderer + REST APIs.

Registered from main.py via:
    import lb_routes
    lb_routes.register_lb_routes(app, templates, require_admin, require_auth,
                                  get_admin_context, require_admin_or_internal)

Persists configs in `load_balancing_configs` table (raw SQL — no ORM model
needed). Updates `ip_pools.interface` field for any pool that lived on the
WAN2 interface (since after save it now lives on the LAN-aggregate interface).
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text as _t
from sqlalchemy.orm import Session


LB_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS load_balancing_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    TEXT NOT NULL,
    nas_id        INTEGER NOT NULL,
    wan1_iface    TEXT NOT NULL,
    wan1_ip       TEXT DEFAULT '',
    wan1_gateway  TEXT NOT NULL,
    wan2_iface    TEXT NOT NULL,
    wan2_ip       TEXT NOT NULL,
    wan2_gateway  TEXT NOT NULL,
    lan_iface     TEXT NOT NULL,
    strategy      TEXT NOT NULL DEFAULT 'pcc_balanced',
    weight1       INTEGER DEFAULT 50,
    weight2       INTEGER DEFAULT 50,
    dns           TEXT DEFAULT '',
    status        TEXT DEFAULT 'Active',
    last_backup   TEXT DEFAULT '',
    last_applied  TEXT DEFAULT '',
    last_error    TEXT DEFAULT '',
    created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_lb_company ON load_balancing_configs(company_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lb_company_nas ON load_balancing_configs(company_id, nas_id);
"""


def setup_tables(engine):
    """Run DDL on app startup."""
    with engine.connect() as c:
        for stmt in LB_TABLE_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(_t(stmt))
        try:
            c.commit()
        except Exception:
            pass


def register_lb_routes(app, templates, require_admin, require_auth,
                       get_admin_context, require_admin_or_internal=None):
    from database import get_db
    from radius_network import NasDevice
    import routeros_provision as rp
    import lb_provision

    # ------------------------------------------------------------------
    # Page
    # ------------------------------------------------------------------
    @app.get("/admin/load-balancing")
    async def page_load_balancing(request: Request, db: Session = Depends(get_db)):
        company_id = require_admin_or_internal(request) if require_admin_or_internal \
            else require_admin(request)
        if not isinstance(company_id, str):
            return company_id
        context = get_admin_context(request, db, "load_balancing")

        nas_devices = db.query(NasDevice).filter(
            NasDevice.company_id == company_id,
            NasDevice.status == "Active"
        ).order_by(NasDevice.id.asc()).all()

        rows = db.execute(_t(
            "SELECT lb.*, n.name AS nas_name FROM load_balancing_configs lb "
            "LEFT JOIN nas_devices n ON n.id = lb.nas_id "
            "WHERE lb.company_id = :cid ORDER BY lb.id DESC"
        ), {"cid": company_id}).mappings().all()
        context["lb_configs"] = [dict(r) for r in rows]
        context["nas_devices"] = [
            {"id": n.id, "name": n.name, "ip_address": n.ip_address,
             "status": n.status} for n in nas_devices
        ]
        return templates.TemplateResponse("admin_load_balancing.html", context)

    # ------------------------------------------------------------------
    # Pre-flight: detect WAN1 + iface list + service-bindings on WAN2-iface
    # ------------------------------------------------------------------
    @app.get("/api/load-balancing/preflight")
    async def api_lb_preflight(request: Request, nas_id: int,
                                wan2_iface: str = "",
                                db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id,
            NasDevice.company_id == company_id
        ).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                 status_code=404)
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                lb = lb_provision.LoadBalancer(rc)
                interfaces = lb.list_interfaces()
                wan1 = lb.detect_wan1()
                users = lb.list_iface_users(wan2_iface) if wan2_iface else {}
            # Pools currently bound to wan2_iface in our DB
            pool_rows = db.execute(_t(
                "SELECT id, name, interface, role FROM ip_pools "
                "WHERE company_id = :cid AND interface = :ifc"
            ), {"cid": company_id, "ifc": wan2_iface or "__none__"}
            ).mappings().all() if wan2_iface else []
            return {
                "ok": True,
                "interfaces": interfaces,
                "wan1": wan1,
                "iface_users": users,
                "db_pools_on_iface": [dict(r) for r in pool_rows],
            }
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)},
                                 status_code=500)

    # ------------------------------------------------------------------
    # Save: backup -> migrate services -> push rules -> persist
    # ------------------------------------------------------------------
    @app.post("/api/load-balancing/save")
    async def api_lb_save(request: Request, db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        if not payload.get("ack_backup"):
            return JSONResponse({"ok": False,
                "error": "Please confirm backup acknowledgment"},
                status_code=400)

        nas_id = int(payload.get("nas_id") or 0)
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id,
            NasDevice.company_id == company_id
        ).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                 status_code=404)

        cfg = {
            "wan1_iface":  (payload.get("wan1_iface") or "").strip(),
            "wan1_gw":     (payload.get("wan1_gw") or payload.get("wan1_gateway") or "").strip(),
            "wan1_ip":     (payload.get("wan1_ip") or "").strip(),
            "wan2_iface":  (payload.get("wan2_iface") or "").strip(),
            "wan2_ip":     (payload.get("wan2_ip") or "").strip(),
            "wan2_gw":     (payload.get("wan2_gw") or payload.get("wan2_gateway") or "").strip(),
            "lan_iface":   (payload.get("lan_iface") or "").strip(),
            "strategy":    (payload.get("strategy") or "pcc_balanced").strip(),
            "weight1":     int(payload.get("weight1") or 50),
            "weight2":     int(payload.get("weight2") or 50),
            "dns":         (payload.get("dns") or "").strip(),
        }
        if cfg["strategy"] not in ("pcc_balanced", "pcc_weighted", "failover"):
            return JSONResponse({"ok": False,
                "error": "Invalid strategy"}, status_code=400)
        for k in ("wan1_iface", "wan1_gw", "wan2_iface",
                  "wan2_ip", "wan2_gw", "lan_iface"):
            if not cfg[k]:
                return JSONResponse({"ok": False,
                    "error": f"{k} is required"}, status_code=400)
        if cfg["wan1_iface"] == cfg["wan2_iface"]:
            return JSONResponse({"ok": False,
                "error": "WAN1 and WAN2 cannot be the same interface"},
                status_code=400)
        if cfg["wan2_iface"] == cfg["lan_iface"]:
            return JSONResponse({"ok": False,
                "error": "WAN2 and LAN cannot be the same interface"},
                status_code=400)

        migrate_result: dict = {}
        apply_result: dict = {}
        pool_updated = 0
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                lb = lb_provision.LoadBalancer(rc)
                # Step 1 — migrate services off the WAN2 interface to the LAN
                #   interface (only when they differ). Idempotent.
                if cfg["wan2_iface"] != cfg["lan_iface"]:
                    migrate_result = lb.migrate_iface(
                        cfg["wan2_iface"], cfg["lan_iface"])
                # Step 2 — push the LB ruleset
                apply_result = lb.apply(cfg)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)},
                                 status_code=500)

        if not apply_result.get("ok"):
            # Persist error so admin sees it on the page
            try:
                db.execute(_t(
                    "UPDATE load_balancing_configs SET status='Error', "
                    "last_error=:err, last_applied=:t "
                    "WHERE company_id=:cid AND nas_id=:nas"
                ), {"err": apply_result.get("error", "unknown"),
                    "t": datetime.now(timezone.utc).isoformat(),
                    "cid": company_id, "nas": nas_id})
                db.commit()
            except Exception:
                db.rollback()
            return JSONResponse({"ok": False,
                "stage": apply_result.get("stage"),
                "error": apply_result.get("error"),
                "backup": apply_result.get("backup"),
                "migrate": migrate_result}, status_code=500)

        # Step 3 — update DB ip_pools: any pool that was on wan2_iface should
        #   now reflect lan_iface so future provisioning lines up.
        try:
            res = db.execute(_t(
                "UPDATE ip_pools SET interface=:new "
                "WHERE company_id=:cid AND interface=:old"
            ), {"new": cfg["lan_iface"], "cid": company_id,
                "old": cfg["wan2_iface"]})
            pool_updated = res.rowcount or 0
            db.commit()
        except Exception:
            db.rollback()

        # Step 4 — upsert config row
        now = datetime.now(timezone.utc).isoformat()
        try:
            existing = db.execute(_t(
                "SELECT id FROM load_balancing_configs "
                "WHERE company_id=:cid AND nas_id=:nas"
            ), {"cid": company_id, "nas": nas_id}).first()
            if existing:
                db.execute(_t("""
                    UPDATE load_balancing_configs SET
                      wan1_iface=:w1if, wan1_ip=:w1ip, wan1_gateway=:w1gw,
                      wan2_iface=:w2if, wan2_ip=:w2ip, wan2_gateway=:w2gw,
                      lan_iface=:lan, strategy=:strat,
                      weight1=:w1, weight2=:w2, dns=:dns,
                      status='Active', last_backup=:bk, last_applied=:t,
                      last_error=''
                    WHERE company_id=:cid AND nas_id=:nas
                """), {
                    "w1if": cfg["wan1_iface"], "w1ip": cfg["wan1_ip"],
                    "w1gw": cfg["wan1_gw"], "w2if": cfg["wan2_iface"],
                    "w2ip": cfg["wan2_ip"], "w2gw": cfg["wan2_gw"],
                    "lan": cfg["lan_iface"], "strat": cfg["strategy"],
                    "w1": cfg["weight1"], "w2": cfg["weight2"],
                    "dns": cfg["dns"],
                    "bk": apply_result.get("backup", ""),
                    "t": now, "cid": company_id, "nas": nas_id,
                })
            else:
                db.execute(_t("""
                    INSERT INTO load_balancing_configs
                      (company_id, nas_id, wan1_iface, wan1_ip, wan1_gateway,
                       wan2_iface, wan2_ip, wan2_gateway, lan_iface, strategy,
                       weight1, weight2, dns, status, last_backup,
                       last_applied, created_at)
                    VALUES (:cid, :nas, :w1if, :w1ip, :w1gw, :w2if, :w2ip,
                            :w2gw, :lan, :strat, :w1, :w2, :dns, 'Active',
                            :bk, :t, :t)
                """), {
                    "cid": company_id, "nas": nas_id,
                    "w1if": cfg["wan1_iface"], "w1ip": cfg["wan1_ip"],
                    "w1gw": cfg["wan1_gw"], "w2if": cfg["wan2_iface"],
                    "w2ip": cfg["wan2_ip"], "w2gw": cfg["wan2_gw"],
                    "lan": cfg["lan_iface"], "strat": cfg["strategy"],
                    "w1": cfg["weight1"], "w2": cfg["weight2"],
                    "dns": cfg["dns"],
                    "bk": apply_result.get("backup", ""),
                    "t": now,
                })
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse({"ok": False,
                "error": f"DB persist failed: {e}",
                "apply": apply_result, "migrate": migrate_result},
                status_code=500)

        return {
            "ok": True,
            "migrate": migrate_result,
            "pool_updated": pool_updated,
            "purged": apply_result.get("purged", 0),
            "backup": apply_result.get("backup", ""),
            "is_v7": apply_result.get("is_v7"),
        }

    # ------------------------------------------------------------------
    # Disable: purge LB-tagged rules + delete config row
    # ------------------------------------------------------------------
    @app.post("/api/load-balancing/{lb_id}/disable")
    async def api_lb_disable(lb_id: int, request: Request,
                              db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")

        row = db.execute(_t(
            "SELECT id, nas_id FROM load_balancing_configs "
            "WHERE id=:id AND company_id=:cid"
        ), {"id": lb_id, "cid": company_id}).first()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"},
                                 status_code=404)

        nas = db.query(NasDevice).filter(
            NasDevice.id == row.nas_id,
            NasDevice.company_id == company_id
        ).first()

        purged = 0
        err = ""
        if nas:
            try:
                with rp.RouterOSClient(nas, dry_run=False) as rc:
                    lb = lb_provision.LoadBalancer(rc)
                    purged = lb.disable().get("purged", 0)
            except Exception as e:
                err = str(e)
        try:
            db.execute(_t(
                "DELETE FROM load_balancing_configs "
                "WHERE id=:id AND company_id=:cid"
            ), {"id": lb_id, "cid": company_id})
            db.commit()
        except Exception:
            db.rollback()
        return {"ok": True, "purged": purged, "router_error": err}
