"""NAT (outbound) routes — page + REST APIs.

Registered from main.py via:
    nat_routes.register_nat_routes(app, templates, require_admin, require_auth,
                                    get_admin_context, require_admin_or_internal)

Supports the 4 NAT actions: masquerade, netmap, src-nat, 1to1.
For action='1to1', child rows in `nat_one_to_one_pairs` track per-customer
pairs and push individual srcnat+dstnat rules to the router.
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text as _t
from sqlalchemy.orm import Session


NAT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS nat_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    nas_id          INTEGER NOT NULL,
    name            TEXT DEFAULT '',
    nat_pool_id     INTEGER DEFAULT 0,
    nat_network     TEXT NOT NULL,
    nat_range       TEXT DEFAULT '',
    interface       TEXT NOT NULL,
    action          TEXT NOT NULL DEFAULT 'masquerade',
    source_pool_id  INTEGER DEFAULT 0,
    source_address  TEXT DEFAULT '',
    bind_address    INTEGER DEFAULT 1,
    /* _S39R5V_MULTIIP_NAT_ multi-IP NAT options */
    place_at_top      INTEGER DEFAULT 1,
    auto_disable_masq INTEGER DEFAULT 0,
    pcc_enabled       INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'Active',
    last_applied    TEXT DEFAULT '',
    last_error      TEXT DEFAULT '',
    pushed_summary  TEXT DEFAULT '',
    created_at      TEXT
);
/* _S39R5V_MULTIIP_NAT_ idempotent column adds for existing installs */

CREATE INDEX IF NOT EXISTS idx_nat_company ON nat_configs(company_id);
CREATE TABLE IF NOT EXISTS nat_one_to_one_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nat_config_id     INTEGER NOT NULL,
    company_id        TEXT NOT NULL,
    customer_pk       INTEGER DEFAULT 0,
    customer_username TEXT DEFAULT '',
    customer_name     TEXT DEFAULT '',
    private_ip        TEXT NOT NULL,
    public_ip         TEXT NOT NULL,
    status            TEXT DEFAULT 'Active',
    last_error        TEXT DEFAULT '',
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_nat_pairs_cfg ON nat_one_to_one_pairs(nat_config_id);
CREATE INDEX IF NOT EXISTS idx_nat_pairs_company ON nat_one_to_one_pairs(company_id);
"""


def setup_tables(engine):
    with engine.connect() as c:
        for stmt in NAT_TABLE_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(_t(stmt))
        # _S39R5V_MULTIIP_NAT_ — idempotent column adds for existing installs
        for col, ddl in [
            ("place_at_top",      "INTEGER DEFAULT 1"),
            ("auto_disable_masq", "INTEGER DEFAULT 0"),
            ("pcc_enabled",       "INTEGER DEFAULT 0"),
        ]:
            try:
                c.execute(_t(f"ALTER TABLE nat_configs ADD COLUMN {col} {ddl}"))
            except Exception:
                pass  # already exists
        try:
            c.commit()
        except Exception:
            pass


def register_nat_routes(app, templates, require_admin, require_auth,
                        get_admin_context, require_admin_or_internal=None):
    from database import get_db, Customer
    from radius_network import NasDevice, IpPool
    import routeros_provision as rp
    import nat_provision

    @app.get("/admin/nat-config")
    async def page_nat(request: Request, db: Session = Depends(get_db)):
        company_id = require_admin_or_internal(request) if require_admin_or_internal \
            else require_admin(request)
        if not isinstance(company_id, str):
            return company_id
        ctx = get_admin_context(request, db, "nat_config")

        nas_devices = db.query(NasDevice).filter(
            NasDevice.company_id == company_id,
            NasDevice.status == "Active"
        ).order_by(NasDevice.id.asc()).all()

        pools = db.query(IpPool).filter(
            IpPool.company_id == company_id,
            IpPool.status == "Active"
        ).order_by(IpPool.id.asc()).all()

        rows = db.execute(_t(
            "SELECT n.*, d.name AS nas_name FROM nat_configs n "
            "LEFT JOIN nas_devices d ON d.id = n.nas_id "
            "WHERE n.company_id = :cid ORDER BY n.id DESC"
        ), {"cid": company_id}).mappings().all()
        rows = [dict(r) for r in rows]

        # Attach pair lists for each 1to1 config
        for r in rows:
            if r.get("action") == "1to1":
                pair_rows = db.execute(_t(
                    "SELECT id, customer_username, customer_name, "
                    "       private_ip, public_ip, status, last_error "
                    "  FROM nat_one_to_one_pairs "
                    " WHERE nat_config_id=:cid AND company_id=:cmp "
                    " ORDER BY id ASC"
                ), {"cid": r["id"], "cmp": company_id}).mappings().all()
                r["pairs"] = [dict(p) for p in pair_rows]
            else:
                r["pairs"] = []

        ctx["nas_devices"] = [{"id": n.id, "name": n.name, "ip_address": n.ip_address}
                              for n in nas_devices]
        ctx["ip_pools"] = [{"id": p.id, "name": p.name, "network": p.network,
                            "role": p.role, "gateway": p.gateway} for p in pools]
        ctx["nat_configs"] = rows
        return templates.TemplateResponse("admin_nat.html", ctx)

    @app.get("/api/nat-config/interfaces")
    async def api_nat_interfaces(request: Request, nas_id: int,
                                  db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id, NasDevice.company_id == company_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                status_code=404)
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                ifs = nat_provision.NATProvisioner(rc).list_interfaces()
            return {"ok": True, "interfaces": ifs}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)},
                                status_code=500)

    @app.get("/api/nat-config/static-customers")
    async def api_nat_static_customers(request: Request,
                                        nat_network: str = "",
                                        db: Session = Depends(get_db)):
        """Returns customers eligible for 1:1 NAT (have static_ip_address set
        OR fix_ip_address='Yes') plus the list of public IPs already taken
        by other 1to1 pairs in this tenant."""
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")

        # _S39NAT_SCOPE_FIX — strict tenant scoping:
        #  1. SQL-level company_id filter
        #  2. Exclude soft-deleted / removed / cancelled states (canonical
        #     terminator states used elsewhere in the codebase).
        #  3. Defensive Python-level company_id re-check (catches any ORM
        #     identity-map leak).
        if not company_id:
            return JSONResponse({"ok": False,
                "error": "no company_id in session — please re-login"},
                status_code=401)
        EXCLUDE_STATES = ("Deleted", "Removed", "Cancelled", "Canceled")
        custs = db.query(Customer).filter(
            Customer.company_id == company_id,
            Customer.status.notin_(EXCLUDE_STATES),
        ).all()
        eligible = []
        for c in custs:
            # Defensive re-check: never trust a single filter
            if (c.company_id or "") != company_id:
                continue
            if (c.status or "") in EXCLUDE_STATES:
                continue
            sip = (getattr(c, "static_ip_address", "") or "").strip()
            fix = (getattr(c, "fix_ip_address", "") or "").strip().lower()
            if sip or fix == "yes":
                eligible.append({
                    "pk": c.id,
                    "customer_id": c.customer_id,
                    "customer_name": c.customer_name,
                    "username": c.username,
                    "static_ip": sip or (getattr(c, "ip_address", "") or ""),
                    "status": c.status,
                    "sub_lco_id": getattr(c, "sub_lco_id", None),
                })

        # Public IPs already mapped (across ALL 1to1 configs of this tenant)
        used_rows = db.execute(_t(
            "SELECT public_ip, customer_username FROM nat_one_to_one_pairs "
            "WHERE company_id=:cid"
        ), {"cid": company_id}).mappings().all()
        used = [{"public_ip": r["public_ip"], "by": r["customer_username"]}
                for r in used_rows]

        # Available public IPs derived from the supplied block (skip net+bcast)
        available = nat_provision.network_to_ip_list(nat_network) if nat_network else []
        used_set = {u["public_ip"] for u in used}
        available_filtered = [ip for ip in available if ip not in used_set]

        return {
            "ok": True,
            "customers": eligible,
            "used_public_ips": used,
            "available_public_ips": available_filtered,
            "total_available": len(available_filtered),
            "total_in_block": len(available),
        }

    @app.post("/api/nat-config/save")
    async def api_nat_save(request: Request, db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        nas_id = int(payload.get("nas_id") or 0)
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id, NasDevice.company_id == company_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                status_code=404)

        # Resolve nat_network: from selected pool OR direct entry
        pool_id = int(payload.get("nat_pool_id") or 0)
        nat_network = (payload.get("nat_network") or "").strip()
        if pool_id:
            pool = db.query(IpPool).filter(
                IpPool.id == pool_id, IpPool.company_id == company_id).first()
            if not pool:
                return JSONResponse({"ok": False,
                    "error": "selected pool not found"}, status_code=404)
            if not nat_network:
                nat_network = pool.network or ""
        if not nat_network or "/" not in nat_network:
            return JSONResponse({"ok": False,
                "error": "Provide a public block (e.g. 36.60.119.111/29)"},
                status_code=400)

        src_pool_id = int(payload.get("source_pool_id") or 0)
        source_address = (payload.get("source_address") or "").strip()
        if src_pool_id:
            sp = db.query(IpPool).filter(
                IpPool.id == src_pool_id, IpPool.company_id == company_id).first()
            if sp and not source_address:
                source_address = sp.network or ""

        action = (payload.get("action") or "masquerade").strip()
        if action not in ("masquerade", "netmap", "src-nat", "1to1"):
            return JSONResponse({"ok": False, "error": "invalid action"},
                                status_code=400)

        cfg = {
            "nat_network": nat_network,
            "nat_range": (payload.get("nat_range") or "").strip(),
            "interface": (payload.get("interface") or "").strip(),
            "action": action,
            "source_address": source_address,
            "bind_address": bool(payload.get("bind_address", True)),
            # _S39R5V_MULTIIP_NAT_ multi-IP NAT toggles
            "place_at_top":      bool(payload.get("place_at_top", True)),
            "auto_disable_masq": bool(payload.get("auto_disable_masq", False)),
            "pcc_enabled":       bool(payload.get("pcc_enabled", False)),
        }
        if not cfg["interface"]:
            return JSONResponse({"ok": False, "error": "interface is required"},
                                status_code=400)

        # Validate pairs payload for 1to1
        pairs_in = payload.get("pairs") or []
        if action == "1to1":
            if not isinstance(pairs_in, list) or not pairs_in:
                return JSONResponse({"ok": False,
                    "error": "1:1 NAT requires at least one customer pair"},
                    status_code=400)
            # Sanity — no duplicate public_ip / private_ip within payload
            seen_pub, seen_priv = set(), set()
            for p in pairs_in:
                pub = (p.get("public_ip") or "").strip()
                priv = (p.get("private_ip") or "").strip()
                if not pub or not priv:
                    return JSONResponse({"ok": False,
                        "error": "Every pair needs both private_ip and public_ip"},
                        status_code=400)
                if pub in seen_pub:
                    return JSONResponse({"ok": False,
                        "error": f"duplicate public_ip {pub} in payload"},
                        status_code=400)
                if priv in seen_priv:
                    return JSONResponse({"ok": False,
                        "error": f"duplicate private_ip {priv} in payload"},
                        status_code=400)
                seen_pub.add(pub); seen_priv.add(priv)
            # Also check no clash with another existing 1to1 config
            taken = db.execute(_t(
                "SELECT public_ip FROM nat_one_to_one_pairs WHERE company_id=:c"
            ), {"c": company_id}).mappings().all()
            taken_ips = {r["public_ip"] for r in taken}
            for pub in seen_pub:
                if pub in taken_ips:
                    return JSONResponse({"ok": False,
                        "error": f"public_ip {pub} is already mapped by another NAT config"},
                        status_code=400)

        now = datetime.now(timezone.utc).isoformat()

        # Insert parent row first (so we have the id for tagging)
        cfg_id = None
        try:
            res = db.execute(_t("""
                INSERT INTO nat_configs
                  (company_id, nas_id, name, nat_pool_id, nat_network, nat_range,
                   interface, action, source_pool_id, source_address,
                   bind_address, place_at_top, auto_disable_masq, pcc_enabled,
                   status, created_at, last_applied)
                VALUES (:cid, :nas, :nm, :pid, :net, :rng, :ifc, :act,
                        :spid, :saddr, :bind, :pat, :adm, :pcc,
                        'Active', :t, :t)
            """), {
                "cid": company_id, "nas": nas_id,
                "nm": (payload.get("name") or "").strip()
                       or f"NAT for {nat_network} via {cfg['interface']}",
                "pid": pool_id, "net": nat_network,
                "rng": cfg["nat_range"], "ifc": cfg["interface"],
                "act": action, "spid": src_pool_id,
                "saddr": source_address,
                "bind": 1 if cfg["bind_address"] else 0,
                "pat":  1 if cfg.get("place_at_top",      True)  else 0,
                "adm":  1 if cfg.get("auto_disable_masq", False) else 0,
                "pcc":  1 if cfg.get("pcc_enabled",       False) else 0,
                "t": now,
            })
            cfg_id = res.lastrowid
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse({"ok": False, "error": f"DB insert: {e}"},
                                status_code=500)

        # Insert pair rows for 1to1, get their ids for unique pair tagging
        pair_db_rows = []
        if action == "1to1":
            for p in pairs_in:
                priv = (p.get("private_ip") or "").strip()
                pub = (p.get("public_ip") or "").strip()
                cust_pk = int(p.get("customer_pk") or 0)
                uname = (p.get("customer_username") or "").strip()
                cname = (p.get("customer_name") or "").strip()
                # Resolve missing username/name from DB if customer_pk given
                if cust_pk and (not uname or not cname):
                    cust = db.query(Customer).filter(
                        Customer.id == cust_pk,
                        Customer.company_id == company_id).first()
                    if cust:
                        uname = uname or (cust.username or "")
                        cname = cname or (cust.customer_name or "")
                try:
                    res2 = db.execute(_t("""
                        INSERT INTO nat_one_to_one_pairs
                          (nat_config_id, company_id, customer_pk,
                           customer_username, customer_name, private_ip,
                           public_ip, status, created_at)
                        VALUES (:cid, :cmp, :pk, :un, :nm, :priv, :pub,
                                'Active', :t)
                    """), {
                        "cid": cfg_id, "cmp": company_id, "pk": cust_pk,
                        "un": uname, "nm": cname, "priv": priv, "pub": pub,
                        "t": now,
                    })
                    pair_db_rows.append({
                        "pair_id": res2.lastrowid,
                        "private_ip": priv,
                        "public_ip": pub,
                        "customer_username": uname,
                    })
                except Exception as e:
                    db.rollback()
                    # Cleanup parent row + already-inserted pairs
                    db.execute(_t("DELETE FROM nat_one_to_one_pairs "
                                  "WHERE nat_config_id=:cid"), {"cid": cfg_id})
                    db.execute(_t("DELETE FROM nat_configs WHERE id=:id"),
                               {"id": cfg_id})
                    db.commit()
                    return JSONResponse({"ok": False,
                        "error": f"DB pair insert: {e}"}, status_code=500)
            db.commit()

        # Push to router
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                np = nat_provision.NATProvisioner(rc)
                result = np.apply(cfg_id, cfg, pairs=pair_db_rows)
        except Exception as e:
            db.execute(_t(
                "UPDATE nat_configs SET status='Error', last_error=:err "
                "WHERE id=:id"), {"err": str(e), "id": cfg_id})
            db.commit()
            return JSONResponse({"ok": False, "error": str(e),
                "config_id": cfg_id}, status_code=500)

        if not result.get("ok"):
            db.execute(_t(
                "UPDATE nat_configs SET status='Error', last_error=:err "
                "WHERE id=:id"),
                {"err": result.get("error", "unknown"), "id": cfg_id})
            db.commit()
            return JSONResponse({"ok": False,
                "error": result.get("error"),
                "config_id": cfg_id,
                "pair_results": result.get("pair_results"),
            }, status_code=500)

        # Persist pushed summary + per-pair status
        db.execute(_t("""
            UPDATE nat_configs
               SET status='Active', last_applied=:t, last_error='',
                   pushed_summary=:sum
             WHERE id=:id
        """), {"t": now, "sum": " | ".join(result.get("pushed") or []),
               "id": cfg_id})
        for pr in result.get("pair_results") or []:
            db.execute(_t("""
                UPDATE nat_one_to_one_pairs
                   SET status=:st, last_error=:err
                 WHERE id=:pid AND nat_config_id=:cid
            """), {
                "st": "Active" if pr.get("ok") else "Error",
                "err": pr.get("error", ""),
                "pid": pr.get("pair_id"),
                "cid": cfg_id,
            })
        db.commit()

        return {"ok": True, "config_id": cfg_id,
                "pushed": result.get("pushed") or [],
                "pair_results": result.get("pair_results") or []}

    @app.post("/api/nat-config/{cfg_id}/disable")
    async def api_nat_disable(cfg_id: int, request: Request,
                               db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = request.session.get("company_id")

        row = db.execute(_t(
            "SELECT id, nas_id FROM nat_configs "
            "WHERE id=:id AND company_id=:cid"
        ), {"id": cfg_id, "cid": company_id}).first()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"},
                                status_code=404)

        nas = db.query(NasDevice).filter(
            NasDevice.id == row.nas_id,
            NasDevice.company_id == company_id).first()

        purged = 0
        err = ""
        if nas:
            try:
                with rp.RouterOSClient(nas, dry_run=False) as rc:
                    np = nat_provision.NATProvisioner(rc)
                    purged = np.disable(cfg_id).get("purged", 0)
            except Exception as e:
                err = str(e)
        try:
            db.execute(_t(
                "DELETE FROM nat_one_to_one_pairs "
                "WHERE nat_config_id=:id AND company_id=:cid"
            ), {"id": cfg_id, "cid": company_id})
            db.execute(_t(
                "DELETE FROM nat_configs WHERE id=:id AND company_id=:cid"
            ), {"id": cfg_id, "cid": company_id})
            db.commit()
        except Exception:
            db.rollback()
        return {"ok": True, "purged": purged, "router_error": err}
