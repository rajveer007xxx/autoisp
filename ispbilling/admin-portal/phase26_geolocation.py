"""
Phase 2.6 (Feb 2026) — ONU/Customer Geolocation
Method A: Manual/GPS pin (web — drag-to-correct + manual lat/lng input)
Method B: Auto-geocode from customer address via OpenStreetMap Nominatim
"""
from __future__ import annotations
import os, time, random, threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from fastapi import Request, Depends, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import text

# Nominatim global rate limit: 1 req/sec
_GEO_LOCK = threading.Lock()
_GEO_LAST = [0.0]


def _nominatim_geocode(query: str) -> Optional[Dict[str, Any]]:
    """Geocode a free-text address via OpenStreetMap Nominatim.
    Returns {lat, lng, confidence, display_name} or None on failure.
    Hard rate-limited to 1 req/sec per OSM ToS."""
    if not query or len(query.strip()) < 5:
        return None
    try:
        import requests
    except Exception:
        return None
    with _GEO_LOCK:
        now = time.time()
        wait = 1.05 - (now - _GEO_LAST[0])
        if wait > 0:
            time.sleep(wait)
        _GEO_LAST[0] = time.time()
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": query, "format": "json", "limit": 1,
                        "addressdetails": 1, "countrycodes": "in"},
                headers={"User-Agent": "AutoISPBilling/1.0 (geocoder@autoispbilling.com)"},
                timeout=8)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data: return None
            top = data[0]
            # Nominatim "importance" 0..1 ~ confidence proxy
            return {"lat": float(top["lat"]),
                    "lng": float(top["lon"]),
                    "confidence": float(top.get("importance", 0.5)),
                    "display_name": top.get("display_name", "")}
        except Exception as e:
            print(f"[geocode] failed for {query!r}: {e}")
            return None


def _build_address(cust) -> str:
    """Concatenate the most useful customer fields into a geocoder query.
    Order matters for Nominatim: street -> locality -> city -> pincode -> state."""
    parts = []
    for f in ("address", "locality", "city", "pincode", "state"):
        v = getattr(cust, f, None)
        if v:
            v = str(v).strip().replace(chr(10), " ").replace("  ", " ")
            if v: parts.append(v)
    # Dedup nearby duplicates
    seen = set(); out = []
    for p in parts:
        k = p.lower()
        if k in seen: continue
        seen.add(k); out.append(p)
    return ", ".join(out)


def _geocode_with_fallback(addr: str):
    """Retry with progressively shorter address if first lookup fails."""
    res = _nominatim_geocode(addr)
    if res: return res
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) > 2:
        # try drop last token (state), then drop last 2 tokens
        for drop in (1, 2):
            sub = ", ".join(parts[:-drop])
            if not sub or sub == addr: continue
            r = _nominatim_geocode(sub)
            if r:
                r["fallback_query"] = sub
                return r
    return None


def _geocode_customer(db: Session, cust, force: bool = False) -> Dict[str, Any]:
    """Geocode a customer's address. Skips if already done unless force=True."""
    if not force and cust.lat is not None and cust.lng is not None and \
       (cust.location_source or "address") == "address":
        return {"success": True, "skipped": True,
                "lat": cust.lat, "lng": cust.lng}
    addr = _build_address(cust)
    if not addr:
        return {"success": False, "error": "No address fields populated"}
    res = _geocode_with_fallback(addr)
    if not res:
        return {"success": False, "error": "Geocoder returned nothing", "address": addr}
    # Add small jitter (±5m) to avoid pin overlap when many customers share an address.
    jitter_lat = (random.random() - 0.5) * 0.0001
    jitter_lng = (random.random() - 0.5) * 0.0001
    cust.lat = res["lat"] + jitter_lat
    cust.lng = res["lng"] + jitter_lng
    cust.geocode_confidence = res["confidence"]
    cust.geocoded_at = datetime.utcnow()
    if not (cust.location_source or "").startswith(("gps", "manual")):
        cust.location_source = "address"
    db.commit()
    return {"success": True, "lat": cust.lat, "lng": cust.lng,
            "confidence": res["confidence"],
            "display_name": res["display_name"], "address": addr}


def _propagate_to_onu(db: Session, customer_id: str, company_id: str):
    """If the customer has lat/lng and one of their ONUs has none (or address-source),
    inherit the customer's coords."""
    try:
        from olt_routes import engine as _olt_eng
    except Exception:
        return 0
    cust = db.execute(text("""SELECT lat, lng, location_source FROM customers
                                WHERE customer_id=:i AND company_id=:c"""),
                      {"i": customer_id, "c": company_id}).fetchone()
    if not cust or cust[0] is None: return 0
    with _olt_eng.begin() as c:
        rows = c.exec_driver_sql(
            """SELECT id FROM onus
                WHERE company_id=? AND customer_id=?
                  AND (lat IS NULL OR location_source='address')""",
            (company_id, customer_id)).fetchall()
        for (oid,) in rows:
            c.exec_driver_sql(
                """UPDATE onus SET lat=?, lng=?, location_source='address',
                                   location_set_at=datetime('now')
                    WHERE id=?""",
                (cust[0], cust[1], oid))
    return len(rows)


def register(app, templates, get_db, require_auth, get_admin_context, _emp_has_perm):

    # ─────────── Auto-geocode batch endpoint ───────────
    @app.post("/api/admin/customers/{cust_id}/geocode")
    async def api_geocode_one(cust_id: str, request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        from database import Customer
        cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                          Customer.company_id == company_id).first()
        if not cust:
            return {"success": False, "message": "Customer not found"}
        # Sub-LCO/Employee scope check
        utype = (request.session.get("user_type") or "").lower()
        if utype == "sub_lco":
            slco = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
            if cust.sub_lco_id != slco:
                return {"success": False, "message": "Out of scope"}
        elif utype == "employee":
            from employee_scope import resolve_locations
            emp_id = int(request.session.get("employee_id") or 0)
            locs = resolve_locations(db, company_id, emp_id)
            cl = (cust.locality or "").upper().strip()
            if cl not in locs:
                return {"success": False, "message": "Out of scope"}
        res = _geocode_customer(db, cust, force=True)
        if res.get("success"):
            propagated = _propagate_to_onu(db, cust_id, company_id)
            res["onus_updated"] = propagated
        return res

    @app.post("/api/admin/customers/geocode-batch")
    async def api_geocode_batch(request: Request, db: Session = Depends(get_db)):
        """Background-runs Nominatim across all un-geocoded customers in scope.
        Rate-limited internally to 1 req/sec. Returns count enqueued."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() not in ("admin", "superadmin"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        limit = int(data.get("limit") or 50)

        from database import Customer
        cs = db.query(Customer).filter(Customer.company_id == company_id,
                                        Customer.lat.is_(None)).limit(limit).all()
        if not cs:
            return {"success": True, "queued": 0, "message": "Nothing to geocode"}
        # Spawn a daemon thread so the request returns instantly
        def worker(cust_list, comp):
            from database import SessionLocal  # type: ignore
            inner_db = SessionLocal()
            try:
                done = 0
                for cust in cust_list:
                    fresh = inner_db.query(Customer).filter(
                        Customer.customer_id == cust.customer_id,
                        Customer.company_id == comp).first()
                    if not fresh: continue
                    r = _geocode_customer(inner_db, fresh, force=False)
                    if r.get("success") and not r.get("skipped"):
                        _propagate_to_onu(inner_db, fresh.customer_id, comp)
                        done += 1
                print(f"[geocode-batch] company={comp} done={done}/{len(cust_list)}")
            finally:
                inner_db.close()
        t = threading.Thread(target=worker, args=(cs, company_id), daemon=True)
        t.start()
        return {"success": True, "queued": len(cs),
                "message": f"Geocoding {len(cs)} customers in background. Refresh map in ~{len(cs)} seconds."}

    # ─────────── ONU manual location endpoints ───────────
    @app.put("/api/admin/onus/{onu_id}/location")
    async def api_onu_set_location(onu_id: int, request: Request, db: Session = Depends(get_db)):
        """Set/correct an ONU's lat/lng manually (admin drag-to-correct)."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        utype = (request.session.get("user_type") or "").lower()
        if utype not in ("admin", "superadmin", "sub_lco", "employee"):
            return {"success": False, "message": "Forbidden"}
        company_id = request.session.get("company_id")
        try: data = await request.json()
        except Exception: data = dict(await request.form())
        lat = data.get("lat"); lng = data.get("lng")
        try:
            lat = float(lat); lng = float(lng)
        except Exception:
            return {"success": False, "message": "lat/lng required (numeric)"}
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return {"success": False, "message": "Out of range"}
        accuracy = data.get("accuracy_m")
        try: accuracy = float(accuracy) if accuracy is not None else None
        except Exception: accuracy = None
        source = (data.get("source") or "manual").lower()
        if source not in ("gps", "manual", "address"): source = "manual"

        try:
            from olt_routes import engine as _olt_eng
        except Exception as e:
            return {"success": False, "message": f"OLT module not loaded: {e}"}
        actor = str(request.session.get("user_id") or "")
        with _olt_eng.begin() as c:
            row = c.exec_driver_sql(
                "SELECT customer_id FROM onus WHERE id=? AND company_id=?",
                (onu_id, company_id)).fetchone()
            if not row:
                return {"success": False, "message": "ONU not found"}
            # Sub-LCO/Employee scope check via customer
            cust_id = row[0] or ""
            if utype in ("sub_lco", "employee") and cust_id:
                from database import Customer
                cust = db.query(Customer).filter(Customer.customer_id == cust_id,
                                                  Customer.company_id == company_id).first()
                if utype == "sub_lco":
                    slco = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
                    if cust and cust.sub_lco_id != slco:
                        return {"success": False, "message": "Out of scope"}
                else:  # employee
                    from employee_scope import resolve_locations
                    emp_id = int(request.session.get("employee_id") or 0)
                    locs = resolve_locations(db, company_id, emp_id)
                    if cust and (cust.locality or "").upper().strip() not in locs:
                        return {"success": False, "message": "Out of scope"}
            c.exec_driver_sql(
                """UPDATE onus
                      SET lat=?, lng=?, location_accuracy_m=?,
                          location_source=?, location_set_at=datetime('now'),
                          location_set_by=?
                    WHERE id=?""",
                (lat, lng, accuracy, source, actor, onu_id))
        # _v4734_  Mirror to network_hardware after admin drag-to-correct.
        try:
            from main import _v4734_upsert_onu_pin as _upin
            _upin(db, company_id, onu_id, lat, lng, source=source, actor=actor)
        except Exception as _ue:
            print(f"[admin onu/location] hw-pin mirror failed (non-fatal): {_ue}")
        return {"success": True, "lat": lat, "lng": lng, "source": source}

    # ─────────── Map data endpoint ───────────
    @app.get("/api/admin/network-map/onus")
    async def api_onus_for_map(request: Request, db: Session = Depends(get_db)):
        """All ONUs with valid lat/lng for the company, RBAC-scoped.
        Returned as GeoJSON-ish list for Leaflet."""
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        company_id = request.session.get("company_id")
        utype = (request.session.get("user_type") or "").lower()
        try:
            from olt_routes import engine as _olt_eng
        except Exception as e:
            return {"success": False, "message": f"OLT module not loaded: {e}"}

        with _olt_eng.begin() as c:
            rows = c.exec_driver_sql(
                """SELECT o.id, o.serial, o.customer_id, o.olt_id, o.pon_port_index,
                          o.lat, o.lng, o.location_source, o.location_accuracy_m,
                          o.status, o.rx_power, o.last_seen
                     FROM onus o
                    WHERE o.company_id=? AND o.lat IS NOT NULL AND o.lng IS NOT NULL""",
                (company_id,)).fetchall()
        # Resolve customer name + RBAC scope
        from database import Customer
        cmap = {}
        if rows:
            cust_ids = list({r[2] for r in rows if r[2]})
            for c_ in db.query(Customer).filter(Customer.company_id == company_id,
                                                Customer.customer_id.in_(cust_ids)).all():
                cmap[c_.customer_id] = c_
        items = []
        for r in rows:
            cust = cmap.get(r[2])
            if utype == "sub_lco":
                slco = request.session.get("sub_lco_id") or request.session.get("user_id_int") or -1
                if cust and cust.sub_lco_id != slco: continue
                if not cust: continue
            elif utype == "employee":
                from employee_scope import resolve_locations
                emp_id = int(request.session.get("employee_id") or 0)
                locs = resolve_locations(db, company_id, emp_id)
                if not cust or (cust.locality or "").upper().strip() not in locs: continue
            items.append({
                "id": r[0], "serial": r[1] or "",
                "customer_id": r[2] or "", "customer_name": cust.customer_name if cust else "",
                "locality": cust.locality if cust else "",
                "olt_id": r[3], "pon_port": r[4],
                "lat": r[5], "lng": r[6],
                "location_source": r[7] or "address",
                "accuracy_m": r[8],
                "status": (r[9] or "unknown").lower(),
                "rx_dbm": r[10], "last_seen": str(r[11]) if r[11] else None,
            })
        return {"success": True, "count": len(items), "items": items}

    # ─────────── Unmapped ONUs (admin / sub-lco / employee) ───────────
    def _unmapped_query_items(request, db, company_id):
        """Returns scoped list of unmapped ONUs for current session role."""
        try:
            from olt_routes import engine as _olt_eng
        except Exception:
            return None
        with _olt_eng.begin() as c:
            rows = c.exec_driver_sql(
                "SELECT o.id, o.serial, o.customer_id, o.olt_id, o.pon_port_index, "
                "o.location_source, o.lat FROM onus o WHERE o.company_id=? AND "
                "(o.lat IS NULL OR o.lng IS NULL OR o.location_source='address')",
                (company_id,)).fetchall()
        from database import Customer
        cmap = {}
        cust_ids = list({r[2] for r in rows if r[2]})
        if cust_ids:
            for c_ in db.query(Customer).filter(Customer.company_id == company_id,
                                                Customer.customer_id.in_(cust_ids)).all():
                cmap[c_.customer_id] = c_
        utype = (request.session.get("user_type") or "").lower()
        emp_locs = None
        slco_id = None
        if utype == "sub_lco":
            slco_id = (request.session.get("sub_lco_id")
                       or request.session.get("sub_lco_db_id")
                       or request.session.get("user_id_int") or -1)
            try: slco_id = int(slco_id)
            except Exception: slco_id = -1
        elif utype == "employee":
            try:
                from employee_scope import resolve_locations
                emp_id = int(request.session.get("employee_id") or 0)
                emp_locs = resolve_locations(db, company_id, emp_id) or set()
            except Exception:
                emp_locs = set()
        items = []
        for r in rows:
            cust = cmap.get(r[2])
            if utype == "sub_lco":
                if not cust or (cust.sub_lco_id or 0) != slco_id:
                    continue
            elif utype == "employee":
                cl = ((cust.locality or "") if cust else "").upper().strip()
                if not cust or cl not in (emp_locs or set()):
                    continue
            items.append({
                "onu_id": r[0], "serial": r[1] or "",
                "customer_id": r[2] or "",
                "customer_name": cust.customer_name if cust else "",
                "address": cust.address if cust else "",
                "locality": cust.locality if cust else "",
                "olt_id": r[3], "pon_port": r[4],
                "current_source": r[5] or "none",
                "has_pin": r[6] is not None,
            })
        return items

    def _unmapped_ctx_for(request, db, role):
        try:
            from olt_routes import _portal_context, _require_scope
            sc = _require_scope(request)
            ctx = _portal_context(request, sc, "unmapped_onus")
        except Exception:
            ctx = get_admin_context(request, db, active_page="unmapped_onus")
        ctx["request"] = request
        ctx["layout"] = {"admin": "base_admin.html", "sub_lco": "base_sub_lco.html",
                         "employee": "base_employee.html"}.get(role, "base_admin.html")
        ctx["api_prefix"] = {"admin": "/api/admin", "sub_lco": "/api/sub-lco",
                             "employee": "/api/employee"}.get(role, "/api/admin")
        ctx["role"] = role
        ctx["active_page"] = "unmapped_onus"
        return ctx

    @app.get("/admin/unmapped-onus", response_class=HTMLResponse)
    async def admin_unmapped_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        ctx = _unmapped_ctx_for(request, db, "admin")
        return templates.TemplateResponse("admin_unmapped_onus.html", ctx)

    @app.get("/sub-lco/unmapped-onus", response_class=HTMLResponse)
    async def sublco_unmapped_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        if (request.session.get("user_type") or "").lower() != "sub_lco":
            return RedirectResponse("/login", 302)
        ctx = _unmapped_ctx_for(request, db, "sub_lco")
        return templates.TemplateResponse("admin_unmapped_onus.html", ctx)

    @app.get("/employee/unmapped-onus", response_class=HTMLResponse)
    async def employee_unmapped_page(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return RedirectResponse("/login", 302)
        if (request.session.get("user_type") or "").lower() != "employee":
            return RedirectResponse("/login", 302)
        ctx = _unmapped_ctx_for(request, db, "employee")
        return templates.TemplateResponse("admin_unmapped_onus.html", ctx)

    @app.get("/api/admin/unmapped-onus")
    async def api_unmapped(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        items = _unmapped_query_items(request, db, request.session.get("company_id"))
        if items is None:
            return {"success": False, "message": "OLT module missing"}
        return {"success": True, "count": len(items), "items": items}

    @app.get("/api/sub-lco/unmapped-onus")
    async def api_unmapped_sublco(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() != "sub_lco":
            return {"success": False, "message": "Forbidden"}
        items = _unmapped_query_items(request, db, request.session.get("company_id"))
        if items is None:
            return {"success": False, "message": "OLT module missing"}
        return {"success": True, "count": len(items), "items": items}

    @app.get("/api/employee/unmapped-onus")
    async def api_unmapped_emp(request: Request, db: Session = Depends(get_db)):
        if require_auth(request):
            return {"success": False, "message": "Unauthorized"}
        if (request.session.get("user_type") or "").lower() != "employee":
            return {"success": False, "message": "Forbidden"}
        items = _unmapped_query_items(request, db, request.session.get("company_id"))
        if items is None:
            return {"success": False, "message": "OLT module missing"}
        return {"success": True, "count": len(items), "items": items}

    print("[phase26_geolocation] registered: geocoding + ONU pin endpoints + unmapped UI (admin/sub-lco/employee)")
