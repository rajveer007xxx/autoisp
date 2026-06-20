"""Website-blocker routes — page + REST APIs.

Tenant-scoped: every endpoint enforces company_id from session, customers
are filtered by company_id with the standard "exclude soft-deleted" guard,
NAS device must belong to the same company.

Tables:
  website_blocks         (parent — one row per block config per NAS)
  website_block_targets  (child — one row per affected customer; also
                          stores the snapshot IP at apply time)
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text as _t
from sqlalchemy.orm import Session


BLOCKS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS website_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    nas_id          INTEGER NOT NULL,
    name            TEXT DEFAULT '',
    domains         TEXT DEFAULT '',
    status          TEXT DEFAULT 'Active',
    last_applied    TEXT DEFAULT '',
    last_error      TEXT DEFAULT '',
    pushed_summary  TEXT DEFAULT '',
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_company ON website_blocks(company_id);
CREATE TABLE IF NOT EXISTS website_block_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    block_id          INTEGER NOT NULL,
    company_id        TEXT NOT NULL,
    customer_pk       INTEGER DEFAULT 0,
    customer_username TEXT DEFAULT '',
    customer_name     TEXT DEFAULT '',
    snapshot_ip       TEXT DEFAULT '',
    status            TEXT DEFAULT 'Active',
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_targets_block ON website_block_targets(block_id);
CREATE INDEX IF NOT EXISTS idx_blocks_targets_company ON website_block_targets(company_id);
"""


EXCLUDE_STATES = ("Deleted", "Removed", "Cancelled", "Canceled")


def setup_tables(engine):
    with engine.connect() as c:
        for stmt in BLOCKS_TABLE_DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                c.execute(_t(stmt))
        try:
            c.commit()
        except Exception:
            pass


def register_blocks_routes(app, templates, require_admin, require_auth,
                           get_admin_context, require_admin_or_internal=None):
    from database import get_db, Customer
    from radius_network import NasDevice
    import routeros_provision as rp
    import blocks_provision

    @app.get("/admin/website-blocks")
    async def page_website_blocks(request: Request,
                                   db: Session = Depends(get_db)):
        company_id = require_admin_or_internal(request) if require_admin_or_internal \
            else require_admin(request)
        if not isinstance(company_id, str):
            return company_id
        ctx = get_admin_context(request, db, "website_blocks")

        nas_devices = db.query(NasDevice).filter(
            NasDevice.company_id == company_id,
            NasDevice.status == "Active"
        ).order_by(NasDevice.id.asc()).all()

        rows = db.execute(_t(
            "SELECT b.*, d.name AS nas_name FROM website_blocks b "
            "LEFT JOIN nas_devices d ON d.id = b.nas_id "
            "WHERE b.company_id = :cid ORDER BY b.id DESC"
        ), {"cid": company_id}).mappings().all()
        rows = [dict(r) for r in rows]

        # Attach targets per block
        for r in rows:
            tg = db.execute(_t(
                "SELECT id, customer_pk, customer_username, customer_name, "
                "       snapshot_ip, status "
                "  FROM website_block_targets "
                " WHERE block_id=:bid AND company_id=:cmp "
                " ORDER BY id ASC"
            ), {"bid": r["id"], "cmp": company_id}).mappings().all()
            r["targets"] = [dict(x) for x in tg]
            r["domain_list"] = [d.strip() for d in (r.get("domains") or "").split(",")
                                if d.strip()]

        ctx["nas_devices"] = [{"id": n.id, "name": n.name, "ip_address": n.ip_address}
                              for n in nas_devices]
        ctx["website_blocks"] = rows
        return templates.TemplateResponse("admin_website_blocks.html", ctx)

    @app.get("/api/website-blocks/customers")
    async def api_blocks_customers(request: Request, nas_id: int = 0,
                                    db: Session = Depends(get_db)):
        """Returns active customers belonging to the admin's company,
        with their best-known IP snapshot. If nas_id is given AND the NAS
        is reachable, we also attempt live PPPoE/Hotspot lookup so admins
        see who's currently online."""
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = (request.session.get("company_id") or "").strip()
        if not company_id:
            return JSONResponse({"ok": False,
                "error": "no company_id in session — please re-login"},
                status_code=401)

        custs = db.query(Customer).filter(
            Customer.company_id == company_id,
            Customer.status.notin_(EXCLUDE_STATES),
        ).all()

        # Attempt live IP lookup if NAS specified + reachable
        live_map = {}
        if nas_id:
            nas = db.query(NasDevice).filter(
                NasDevice.id == nas_id,
                NasDevice.company_id == company_id).first()
            if nas:
                try:
                    with rp.RouterOSClient(nas, dry_run=False) as rc:
                        for r in rc._api.path("ppp/active"):
                            n = (r.get("name") or "").strip()
                            ip = (r.get("address") or "").strip()
                            if n and ip:
                                live_map[n] = ip
                        for r in rc._api.path("ip/hotspot/active"):
                            n = (r.get("user") or "").strip()
                            ip = (r.get("address") or "").strip()
                            if n and ip:
                                live_map[n] = ip
                except Exception:
                    pass

        out = []
        for c in custs:
            if (c.company_id or "") != company_id:
                continue
            uname = (c.username or "").strip()
            sip = (getattr(c, "static_ip_address", "") or "").strip()
            ip = ""
            source = ""
            if sip:
                ip = sip
                source = "static"
            elif uname and uname in live_map:
                ip = live_map[uname]
                source = "live"
            else:
                ip = (getattr(c, "ip_address", "") or "").strip()
                source = "last-known" if ip else "offline"
            out.append({
                "pk": c.id,
                "customer_id": c.customer_id,
                "customer_name": c.customer_name,
                "username": uname,
                "ip": ip,
                "ip_source": source,
                "status": c.status,
                "sub_lco_id": getattr(c, "sub_lco_id", None),
            })
        return {"ok": True, "customers": out, "live_count": len(live_map)}

    @app.get("/api/website-blocks/for-customer/{customer_pk}")
    async def api_blocks_for_customer(request: Request, customer_pk: int,
                                       db: Session = Depends(get_db)):
        """_S40f_ — Per-customer website-block snapshot.
        Returns:
          * available NAS devices in the company (so the modal can offer
            a picker, defaulting to the customer's provisioned_nas_id)
          * existing blocks that already target this customer (so admin
            can see / extend / remove)"""
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = (request.session.get("company_id") or "").strip()
        if not company_id:
            return JSONResponse({"ok": False, "error": "no company in session"},
                                status_code=401)
        cust = db.query(Customer).filter(
            Customer.id == customer_pk,
            Customer.company_id == company_id).first()
        if not cust:
            return JSONResponse({"ok": False, "error": "customer_not_found"},
                                status_code=404)
        nas = db.query(NasDevice).filter(
            NasDevice.company_id == company_id,
            
        ).order_by(NasDevice.id).all()
        nas_list = [{"id": n.id, "name": n.name, "ip_address": n.ip_address}
                    for n in nas]
        # Default = customer's own provisioned NAS, else the first
        # MikroTik in the company.
        default_nas = getattr(cust, "provisioned_nas_id", None) or \
                      (nas_list[0]["id"] if nas_list else None)
        # Existing blocks where this customer is a target.
        rows = db.execute(_t("""
            SELECT b.id, b.name, b.domains, b.status, b.last_applied,
                   b.nas_id, d.name AS nas_name, t.snapshot_ip
            FROM website_blocks b
            JOIN website_block_targets t ON t.block_id = b.id
            LEFT JOIN nas_devices d ON d.id = b.nas_id
            WHERE b.company_id = :cid AND t.customer_pk = :pk
              AND t.status = 'Active'
            ORDER BY b.id DESC
        """), {"cid": company_id, "pk": customer_pk}).fetchall()
        blocks = []
        for r in rows:
            blocks.append({
                "id": r[0], "name": r[1] or "",
                "domains": [d.strip() for d in (r[2] or "").split(",") if d.strip()],
                "status": r[3], "last_applied": r[4] or "",
                "nas_id": r[5], "nas_name": r[6] or "",
                "snapshot_ip": r[7] or "",
            })
        return {"ok": True,
                "customer": {"id": cust.id, "customer_id": cust.customer_id,
                             "username": cust.username,
                             "customer_name": cust.customer_name},
                "nas_devices": nas_list,
                "default_nas_id": default_nas,
                "blocks": blocks}


    @app.post("/api/website-blocks/save")
    async def api_blocks_save(request: Request,
                               db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = (request.session.get("company_id") or "").strip()
        if not company_id:
            return JSONResponse({"ok": False, "error": "no company in session"},
                                status_code=401)
        try:
            payload = await request.json()
        except Exception:
            payload = {}

        nas_id = int(payload.get("nas_id") or 0)
        nas = db.query(NasDevice).filter(
            NasDevice.id == nas_id,
            NasDevice.company_id == company_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                status_code=404)

        domains = [d.strip() for d in (payload.get("domains") or [])
                   if d and d.strip()]
        if not domains:
            return JSONResponse({"ok": False,
                "error": "at least one domain is required"},
                status_code=400)

        customer_pks = [int(p) for p in (payload.get("customer_pks") or [])
                        if str(p).strip().isdigit()]
        if not customer_pks:
            return JSONResponse({"ok": False,
                "error": "select at least one customer"},
                status_code=400)

        # Tenant-scope: re-fetch the customers and reject any pk that
        # does NOT belong to this company.
        custs = db.query(Customer).filter(
            Customer.id.in_(customer_pks),
            Customer.company_id == company_id,
            Customer.status.notin_(EXCLUDE_STATES),
        ).all()
        cust_map = {c.id: c for c in custs}
        for pk in customer_pks:
            if pk not in cust_map:
                return JSONResponse({"ok": False,
                    "error": f"customer pk={pk} not found in your company"},
                    status_code=400)

        now = datetime.now(timezone.utc).isoformat()
        block_name = (payload.get("name") or "").strip() or \
            f"Block {len(domains)} site(s) for {len(custs)} customer(s)"

        # Insert parent
        cfg_id = None
        try:
            res = db.execute(_t("""
                INSERT INTO website_blocks
                  (company_id, nas_id, name, domains, status, created_at,
                   last_applied)
                VALUES (:cid, :nas, :nm, :dm, 'Active', :t, :t)
            """), {
                "cid": company_id, "nas": nas_id, "nm": block_name,
                "dm": ",".join(domains), "t": now,
            })
            cfg_id = res.lastrowid
            db.commit()
        except Exception as e:
            db.rollback()
            return JSONResponse({"ok": False, "error": f"DB insert: {e}"},
                                status_code=500)

        # Snapshot IPs: query live router for online users + use
        # static_ip_address for static-IP customers.
        live_map = {}
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                for r in rc._api.path("ppp/active"):
                    n = (r.get("name") or "").strip()
                    ip = (r.get("address") or "").strip()
                    if n and ip:
                        live_map[n] = ip
                for r in rc._api.path("ip/hotspot/active"):
                    n = (r.get("user") or "").strip()
                    ip = (r.get("address") or "").strip()
                    if n and ip:
                        live_map[n] = ip
        except Exception:
            pass

        targets = []
        offline = []
        for pk in customer_pks:
            c = cust_map[pk]
            uname = (c.username or "").strip()
            sip = (getattr(c, "static_ip_address", "") or "").strip()
            if sip:
                ip = sip
            elif uname and uname in live_map:
                ip = live_map[uname]
            else:
                ip = (getattr(c, "ip_address", "") or "").strip()
            if not ip:
                offline.append(c.customer_name or c.customer_id)
                continue
            targets.append({
                "ip": ip,
                "label": uname or (c.customer_id or ""),
                "customer_pk": pk,
                "customer_username": uname,
                "customer_name": c.customer_name or "",
            })

        if not targets:
            db.execute(_t("DELETE FROM website_blocks WHERE id=:id"),
                       {"id": cfg_id})
            db.commit()
            return JSONResponse({"ok": False,
                "error": "no usable IP found for any selected customer "
                         "(all offline + no static IP). Try again when at "
                         "least one is online, or assign static IPs.",
                "offline": offline}, status_code=400)

        # Insert child rows
        for t in targets:
            db.execute(_t("""
                INSERT INTO website_block_targets
                  (block_id, company_id, customer_pk, customer_username,
                   customer_name, snapshot_ip, status, created_at)
                VALUES (:bid, :cmp, :pk, :un, :nm, :ip, 'Active', :t)
            """), {"bid": cfg_id, "cmp": company_id,
                   "pk": t["customer_pk"], "un": t["customer_username"],
                   "nm": t["customer_name"], "ip": t["ip"], "t": now})
        db.commit()

        # Push to router
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                wb = blocks_provision.WebsiteBlocker(rc)
                result = wb.apply(cfg_id, domains, targets)
        except Exception as e:
            db.execute(_t(
                "UPDATE website_blocks SET status='Error', last_error=:err "
                "WHERE id=:id"), {"err": str(e), "id": cfg_id})
            db.commit()
            return JSONResponse({"ok": False, "error": str(e),
                "config_id": cfg_id}, status_code=500)

        if not result.get("ok"):
            db.execute(_t(
                "UPDATE website_blocks SET status='Error', last_error=:err "
                "WHERE id=:id"),
                {"err": result.get("error", "unknown"), "id": cfg_id})
            db.commit()
            return JSONResponse({"ok": False,
                "error": result.get("error"),
                "config_id": cfg_id,
            }, status_code=500)

        db.execute(_t("""
            UPDATE website_blocks
               SET status='Active', last_applied=:t, last_error='',
                   pushed_summary=:sum
             WHERE id=:id
        """), {"t": now, "sum": " | ".join(result.get("pushed") or []),
               "id": cfg_id})
        db.commit()

        return {"ok": True, "config_id": cfg_id,
                "pushed": result.get("pushed") or [],
                "src_count": result.get("src_count", 0),
                "dst_count": result.get("dst_count", 0),
                "offline_skipped": offline}

    @app.post("/api/website-blocks/{cfg_id}/refresh-ips")
    async def api_blocks_refresh_ips(cfg_id: int, request: Request,
                                      db: Session = Depends(get_db)):
        """Re-snapshot live IPs for every target then re-push to router."""
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = (request.session.get("company_id") or "").strip()
        if not company_id:
            return JSONResponse({"ok": False, "error": "no company in session"},
                                status_code=401)

        row = db.execute(_t(
            "SELECT * FROM website_blocks WHERE id=:id AND company_id=:cid"
        ), {"id": cfg_id, "cid": company_id}).mappings().first()
        if not row:
            return JSONResponse({"ok": False, "error": "not_found"},
                                status_code=404)
        nas = db.query(NasDevice).filter(
            NasDevice.id == row["nas_id"],
            NasDevice.company_id == company_id).first()
        if not nas:
            return JSONResponse({"ok": False, "error": "nas_not_found"},
                                status_code=404)
        targets_rows = db.execute(_t(
            "SELECT * FROM website_block_targets "
            "WHERE block_id=:bid AND company_id=:cid"
        ), {"bid": cfg_id, "cid": company_id}).mappings().all()
        if not targets_rows:
            return JSONResponse({"ok": False,
                "error": "no targets stored"}, status_code=400)

        domains = [d.strip() for d in (row.get("domains") or "").split(",")
                   if d.strip()]

        live_map = {}
        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                for r in rc._api.path("ppp/active"):
                    n = (r.get("name") or "").strip()
                    ip = (r.get("address") or "").strip()
                    if n and ip:
                        live_map[n] = ip
                for r in rc._api.path("ip/hotspot/active"):
                    n = (r.get("user") or "").strip()
                    ip = (r.get("address") or "").strip()
                    if n and ip:
                        live_map[n] = ip
        except Exception as e:
            return JSONResponse({"ok": False,
                "error": f"router unreachable: {e}"}, status_code=500)

        # Re-resolve IPs from DB customers (handles re-allocations)
        new_targets = []
        offline = []
        for t in targets_rows:
            cust = db.query(Customer).filter(
                Customer.id == t["customer_pk"],
                Customer.company_id == company_id).first()
            if not cust:
                continue
            uname = (cust.username or "").strip()
            sip = (getattr(cust, "static_ip_address", "") or "").strip()
            ip = sip or live_map.get(uname) or \
                 (getattr(cust, "ip_address", "") or "").strip()
            if not ip:
                offline.append(cust.customer_name or cust.customer_id)
                continue
            new_targets.append({
                "ip": ip, "label": uname,
                "customer_pk": cust.id,
                "customer_username": uname,
                "customer_name": cust.customer_name or "",
            })
            db.execute(_t(
                "UPDATE website_block_targets SET snapshot_ip=:ip "
                "WHERE id=:tid"
            ), {"ip": ip, "tid": t["id"]})
        db.commit()

        if not new_targets:
            return JSONResponse({"ok": False,
                "error": "no usable IP — all targets offline",
                "offline": offline}, status_code=400)

        try:
            with rp.RouterOSClient(nas, dry_run=False) as rc:
                wb = blocks_provision.WebsiteBlocker(rc)
                result = wb.apply(cfg_id, domains, new_targets)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)},
                                status_code=500)
        if not result.get("ok"):
            return JSONResponse({"ok": False,
                "error": result.get("error")}, status_code=500)

        db.execute(_t("""
            UPDATE website_blocks
               SET status='Active',
                   last_applied=:t, last_error='', pushed_summary=:sum
             WHERE id=:id
        """), {"t": datetime.now(timezone.utc).isoformat(),
               "sum": " | ".join(result.get("pushed") or []),
               "id": cfg_id})
        db.commit()
        return {"ok": True, "refreshed": len(new_targets),
                "offline_skipped": offline}

    @app.post("/api/website-blocks/{cfg_id}/disable")
    async def api_blocks_disable(cfg_id: int, request: Request,
                                  db: Session = Depends(get_db)):
        auth_check = require_auth(request)
        if auth_check:
            return auth_check
        company_id = (request.session.get("company_id") or "").strip()
        if not company_id:
            return JSONResponse({"ok": False, "error": "no company in session"},
                                status_code=401)

        row = db.execute(_t(
            "SELECT id, nas_id FROM website_blocks "
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
                    wb = blocks_provision.WebsiteBlocker(rc)
                    purged = wb.disable(cfg_id).get("purged", 0)
            except Exception as e:
                err = str(e)
        try:
            db.execute(_t(
                "DELETE FROM website_block_targets "
                "WHERE block_id=:id AND company_id=:cid"
            ), {"id": cfg_id, "cid": company_id})
            db.execute(_t(
                "DELETE FROM website_blocks WHERE id=:id AND company_id=:cid"
            ), {"id": cfg_id, "cid": company_id})
            db.commit()
        except Exception:
            db.rollback()
        return {"ok": True, "purged": purged, "router_error": err}
