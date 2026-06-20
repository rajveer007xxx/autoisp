"""Apply all accumulated 40j → 40l backend patches to the FRESH network_map_routes.py."""
import re, sys

p = '/opt/ispbilling/admin-portal/network_map_routes.py'
s = open(p).read()

# ── 40j.1: Add olts lat/lng migration inside _ensure_schema ──
# Replace ONLY the module-scope call (preceded by two newlines)
old = '\n\n_ensure_schema()\n'
migration = '''\n\n# Legacy olt-lat migration (safe idempotent)
try:
    with engine.begin() as _c:
        try: _c.exec_driver_sql("ALTER TABLE olts ADD COLUMN latitude REAL")
        except Exception: pass
        try: _c.exec_driver_sql("ALTER TABLE olts ADD COLUMN longitude REAL")
        except Exception: pass
except Exception:
    pass

'''
if 'Legacy olt-lat migration' not in s:
    s = s.replace(old, migration + '\n_ensure_schema()\n', 1)
    print('OK: olts lat/lng migration added')

# ── 40j.2: Synthesize pon_ports rows from ONUs when empty ──
old2 = '''        rows = conn.exec_driver_sql(
            "SELECT port_index,name,tx_power,admin_up,oper_up,"
            "       total_onus,online_onus "
            "FROM pon_ports WHERE olt_id=? ORDER BY port_index",
            (olt_id,)).fetchall()'''
new2 = '''        rows = conn.exec_driver_sql(
            "SELECT port_index,name,tx_power,admin_up,oper_up,"
            "       total_onus,online_onus "
            "FROM pon_ports WHERE olt_id=? ORDER BY port_index",
            (olt_id,)).fetchall()
        # Synthesize from ONUs when pon_ports is empty (e.g. mock OLT)
        if not rows:
            ports = conn.exec_driver_sql(
                "SELECT DISTINCT pon_port_index FROM onus "
                "WHERE olt_id=? AND pon_port_index IS NOT NULL "
                "ORDER BY pon_port_index", (olt_id,)).fetchall()
            for (pi,) in ports:
                conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO pon_ports "
                    "(olt_id,port_index,name,admin_up,oper_up) "
                    "VALUES (?,?,?,1,1)",
                    (olt_id, pi, f"gpon0/{pi}"))
            rows = conn.exec_driver_sql(
                "SELECT port_index,name,tx_power,admin_up,oper_up,"
                "       total_onus,online_onus "
                "FROM pon_ports WHERE olt_id=? ORDER BY port_index",
                (olt_id,)).fetchall()'''
if 'Synthesize from ONUs when pon_ports' not in s and old2 in s:
    s = s.replace(old2, new2, 1)
    print('OK: pon-status synth')

# ── 40k.1: Enrich ONU hardware pins in /items ──
old3 = '''        # Customers (broadband subscribers with lat/lng)
        try:
            rows = conn.exec_driver_sql(
                "SELECT customer_id,customer_name,latitude,longitude,"
                "       status,sub_lco_id "
                "FROM customers "
                "WHERE company_id=? AND latitude IS NOT NULL "
                "  AND longitude IS NOT NULL "
                "LIMIT 5000",
                (cid,)).fetchall()
            for r in rows:
                out["customers"].append({
                    "id": r[0], "name": r[1],
                    "lat": r[2], "lng": r[3],
                    "status": r[4], "sub_lco_id": r[5],
                })
        except Exception:
            pass'''
new3 = '''        # Customers (broadband subscribers with lat/lng)
        try:
            rows = conn.exec_driver_sql(
                "SELECT customer_id,customer_name,latitude,longitude,"
                "       status,sub_lco_id "
                "FROM customers "
                "WHERE company_id=? AND latitude IS NOT NULL "
                "  AND longitude IS NOT NULL "
                "LIMIT 5000",
                (cid,)).fetchall()
            for r in rows:
                out["customers"].append({
                    "id": r[0], "name": r[1],
                    "lat": r[2], "lng": r[3],
                    "status": r[4], "sub_lco_id": r[5],
                })
        except Exception:
            pass
        # ── Enrich ONU hardware pins with live RX power, status, name ──
        try:
            onu_rows = conn.exec_driver_sql(
                "SELECT id, name, serial, customer_id, status, rx_power, "
                "       tx_power, last_seen "
                "FROM onus WHERE company_id=?", (cid,)).fetchall()
            onu_map = {r[0]: {"name": r[1], "serial": r[2],
                                "customer_id": r[3], "status": r[4],
                                "rx_power": r[5], "tx_power": r[6],
                                "last_seen": r[7]} for r in onu_rows}
            for h in out["hardware"]:
                if h["kind"] == "onu" and h.get("ref_onu_id"):
                    info = onu_map.get(h["ref_onu_id"])
                    if info:
                        h["props"] = {**(h.get("props") or {}),
                                        "rx_power": info["rx_power"],
                                        "tx_power": info["tx_power"],
                                        "status":   info["status"],
                                        "serial":   info["serial"],
                                        "last_seen": info["last_seen"]}
                        if not h.get("name") and info.get("name"):
                            h["name"] = info["name"]
        except Exception:
            pass'''
if 'Enrich ONU hardware pins' not in s:
    if old3 not in s:
        print('FAIL: customers-loop pattern missing'); sys.exit(1)
    s = s.replace(old3, new3, 1)
    print('OK: ONU enrichment')

# ── 40l.1: fiber_splice schema ──
splice_schema = '''        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS fiber_splice (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id   TEXT NOT NULL,
              node_hw_id   INTEGER NOT NULL,
              src_fiber_id INTEGER,
              src_core     INTEGER,
              dst_fiber_id INTEGER,
              dst_core     INTEGER,
              mode         TEXT DEFAULT 'thru',
              loss_db      REAL,
              notes        TEXT,
              created_by   TEXT,
              created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_splice_node "
            "ON fiber_splice(company_id, node_hw_id)")
'''
# Insert right before the final conn.exec_driver_sql that creates idx_netfiber_company
target = '        conn.exec_driver_sql(\n            "CREATE INDEX IF NOT EXISTS idx_netfiber_company "\n            "ON network_fiber(company_id)")'
if 'CREATE TABLE IF NOT EXISTS fiber_splice' not in s:
    if target not in s:
        print('FAIL: idx_netfiber target not found'); sys.exit(1)
    s = s.replace(target, target + '\n' + splice_schema, 1)
    print('OK: fiber_splice schema')

# ── 40l.2: import vendor_adapters ──
if 'import vendor_adapters' not in s:
    s = s.replace(
        'from olt_routes import engine, _require_scope, _portal_context, templates  # type: ignore',
        'from olt_routes import engine, _require_scope, _portal_context, templates  # type: ignore\nimport vendor_adapters',
        1)
    print('OK: vendor_adapters import')

# ── 40l.3: upgrade pon-toggle ──
old_toggle = '''    # Vendor-specific dispatch (best-effort)
    msg = "PON status flipped in DB."
    vendor = (olt[1] or "mock").lower()
    if vendor != "mock":
        msg = (f"PON status flipped in DB. Live SNMP/CLI dispatch to "
               f"{vendor.upper()} OLT '{olt[5]}' is queued for the vendor "
               f"adapter (will execute on next poll cycle).")
    return {"ok": True, "admin_up": body.enable, "message": msg}'''
new_toggle = '''    # Vendor-specific dispatch via vendor_adapters.dispatch_pon_toggle
    olt_row: Dict[str, Any] = {}
    try:
        with engine.begin() as c2:
            row = c2.exec_driver_sql(
                "SELECT * FROM olts WHERE id=? AND company_id=?",
                (olt_id, cid)).mappings().first()
            if row: olt_row = dict(row)
    except Exception: pass
    dispatch = vendor_adapters.dispatch_pon_toggle(
        olt_row, port_index, body.enable)
    msg = (f"DB flipped. Vendor dispatch [{dispatch.get('mode')}]: "
             f"{'OK' if dispatch.get('ok') else 'FAILED'} — "
             f"{(dispatch.get('details') or '')[:300]}")
    return {"ok": True, "admin_up": body.enable, "message": msg,
             "vendor_dispatch": dispatch}'''
if 'dispatch_pon_toggle' not in s:
    if old_toggle not in s:
        print('FAIL: pon-toggle pattern not found'); sys.exit(1)
    s = s.replace(old_toggle, new_toggle, 1)
    print('OK: pon-toggle upgraded')

# ── 40l.4: append Phase-2 endpoints ──
addendum = r'''

# ═══ _S40l_ Phase-2 endpoints ═══════════════════════════════════════════
class FiberPatch(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    core_count: Optional[int] = None
    polyline: Optional[List[List[float]]] = None
    src_hw_id: Optional[int] = None
    dst_hw_id: Optional[int] = None


@router.patch("/api/admin/network-map/fiber/{fid}")
def api_fiber_patch(request: Request, fid: int, body: FiberPatch):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id FROM network_fiber WHERE id=? AND company_id=?",
            (fid, cid)).fetchone()
        if not row: raise HTTPException(404, "Fiber not found")
        sets, vals = [], []
        if body.name is not None: sets.append("name=?"); vals.append(body.name)
        if body.color is not None: sets.append("color=?"); vals.append(body.color.lower())
        if body.core_count is not None:
            sets.append("core_count=?"); vals.append(body.core_count)
        if body.src_hw_id is not None:
            sets.append("src_hw_id=?"); vals.append(body.src_hw_id)
        if body.dst_hw_id is not None:
            sets.append("dst_hw_id=?"); vals.append(body.dst_hw_id)
        if body.polyline is not None:
            if len(body.polyline) < 2:
                raise HTTPException(400, "polyline must have >=2 points")
            sets.append("polyline_json=?"); vals.append(json.dumps(body.polyline))
        if not sets: return {"ok": True, "no_changes": True}
        vals.extend([fid, cid])
        conn.exec_driver_sql(
            f"UPDATE network_fiber SET {','.join(sets)} "
            f"WHERE id=? AND company_id=?", tuple(vals))
    return {"ok": True}


class SpliceIn(BaseModel):
    node_hw_id: int
    src_fiber_id: Optional[int] = None
    src_core: Optional[int] = None
    dst_fiber_id: Optional[int] = None
    dst_core: Optional[int] = None
    mode: Optional[str] = "thru"
    loss_db: Optional[float] = None
    notes: Optional[str] = None


@router.get("/api/admin/network-map/splice/{hw_id}")
def api_splice_list(request: Request, hw_id: int):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        node = conn.exec_driver_sql(
            "SELECT id,kind,name,props_json FROM network_hardware "
            "WHERE id=? AND company_id=?", (hw_id, cid)).fetchone()
        if not node: raise HTTPException(404, "Node not found")
        fibers = conn.exec_driver_sql(
            "SELECT id,name,color,core_count,src_hw_id,dst_hw_id "
            "FROM network_fiber WHERE company_id=? "
            "  AND (src_hw_id=? OR dst_hw_id=?) ORDER BY id",
            (cid, hw_id, hw_id)).fetchall()
        splices = conn.exec_driver_sql(
            "SELECT id,src_fiber_id,src_core,dst_fiber_id,dst_core,"
            "       mode,loss_db,notes,created_at "
            "FROM fiber_splice WHERE company_id=? AND node_hw_id=? "
            "ORDER BY id", (cid, hw_id)).fetchall()
    return {
        "ok": True,
        "node": {"id": node[0], "kind": node[1], "name": node[2],
                  "props": json.loads(node[3]) if node[3] else {}},
        "fibers": [{"id":f[0],"name":f[1],"color":f[2],"core_count":f[3],
                     "src_hw_id":f[4],"dst_hw_id":f[5],
                     "side": "in" if f[5]==hw_id else "out"} for f in fibers],
        "splices": [{"id":s[0],"src_fiber_id":s[1],"src_core":s[2],
                      "dst_fiber_id":s[3],"dst_core":s[4],"mode":s[5],
                      "loss_db":s[6],"notes":s[7],"created_at":s[8]}
                     for s in splices],
    }


@router.post("/api/admin/network-map/splice")
def api_splice_create(request: Request, body: SpliceIn):
    sc = _require_scope(request); cid = sc["company_id"]; actor = sc["actor"]
    with engine.begin() as conn:
        node = conn.exec_driver_sql(
            "SELECT id FROM network_hardware WHERE id=? AND company_id=?",
            (body.node_hw_id, cid)).fetchone()
        if not node: raise HTTPException(404, "Node not found")
        r = conn.exec_driver_sql(
            "INSERT INTO fiber_splice "
            "(company_id,node_hw_id,src_fiber_id,src_core,dst_fiber_id,"
            " dst_core,mode,loss_db,notes,created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, body.node_hw_id, body.src_fiber_id, body.src_core,
             body.dst_fiber_id, body.dst_core, body.mode or "thru",
             body.loss_db, body.notes, actor))
    return {"ok": True, "id": r.lastrowid}


@router.delete("/api/admin/network-map/splice/{splice_id}")
def api_splice_delete(request: Request, splice_id: int):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM fiber_splice WHERE id=? AND company_id=?",
            (splice_id, cid))
    return {"ok": True}


from fastapi import UploadFile, File


def _kind_from_props(p: dict) -> str:
    kind = (p or {}).get("kind") or (p or {}).get("type")
    if not kind: return "jc_box"
    k = str(kind).lower().replace("-", "_").replace(" ", "_")
    valid = {"olt","onu","jc_box","splitter_1x4","splitter_1x8",
             "splitter_1x16","pole","manhole"}
    return k if k in valid else "jc_box"


def _parse_geojson(text: str) -> dict:
    data = json.loads(text)
    feats = data.get("features") if data.get("type") == "FeatureCollection" else [data]
    return {"features": feats or []}


def _parse_kml(text: str) -> dict:
    import xml.etree.ElementTree as ET
    ns = "{http://www.opengis.net/kml/2.2}"
    root = ET.fromstring(text)
    feats = []
    for pm in root.iter(ns + "Placemark"):
        name_el = pm.find(ns + "name")
        name = name_el.text if name_el is not None else None
        props = {"name": name}
        ed = pm.find(ns + "ExtendedData")
        if ed is not None:
            for dat in ed.iter(ns + "Data"):
                key = dat.attrib.get("name")
                val_el = dat.find(ns + "value")
                if key and val_el is not None:
                    props[key] = val_el.text
        pt = pm.find(".//" + ns + "Point/" + ns + "coordinates")
        if pt is not None and pt.text:
            parts = pt.text.strip().split(",")
            lng, lat = float(parts[0]), float(parts[1])
            feats.append({"type":"Feature","properties":props,
                           "geometry":{"type":"Point",
                                       "coordinates":[lng, lat]}})
            continue
        ls = pm.find(".//" + ns + "LineString/" + ns + "coordinates")
        if ls is not None and ls.text:
            pts = []
            for token in ls.text.strip().split():
                p = token.strip().split(",")
                if len(p) >= 2: pts.append([float(p[0]), float(p[1])])
            if len(pts) >= 2:
                feats.append({"type":"Feature","properties":props,
                               "geometry":{"type":"LineString",
                                           "coordinates":pts}})
    return {"features": feats}


@router.post("/api/admin/network-map/import")
async def api_import(request: Request, file: UploadFile = File(...)):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]; actor = sc["actor"]
    blob = await file.read()
    try: text = blob.decode("utf-8", errors="replace")
    except Exception: text = ""
    fn = (file.filename or "").lower()
    if fn.endswith(".kml"):
        try: data = _parse_kml(text)
        except Exception as e: raise HTTPException(400, f"KML parse failed: {e}")
    else:
        try: data = _parse_geojson(text)
        except Exception as e: raise HTTPException(400, f"GeoJSON parse failed: {e}")
    hw_count = fb_count = 0
    with engine.begin() as conn:
        for ft in data.get("features", []):
            props = ft.get("properties") or {}
            geom = ft.get("geometry") or {}
            if geom.get("type") == "Point":
                coords = geom.get("coordinates") or [None, None]
                lng, lat = coords[0], coords[1]
                if lat is None or lng is None: continue
                kind = _kind_from_props(props)
                name = props.get("name") or kind.upper()
                conn.exec_driver_sql(
                    "INSERT INTO network_hardware "
                    "(company_id,kind,name,lat,lng,props_json,created_by) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (cid, kind, name, lat, lng,
                     json.dumps(props), actor))
                hw_count += 1
            elif geom.get("type") == "LineString":
                coords = geom.get("coordinates") or []
                pts = [[c[1], c[0]] for c in coords if len(c) >= 2]
                if len(pts) < 2: continue
                color = (props.get("color") or "blue").lower()
                cores = int(props.get("core_count") or 12)
                conn.exec_driver_sql(
                    "INSERT INTO network_fiber "
                    "(company_id,name,color,core_count,polyline_json,"
                    " created_by) "
                    "VALUES (?,?,?,?,?,?)",
                    (cid, props.get("name"), color, cores,
                     json.dumps(pts), actor))
                fb_count += 1
    return {"ok": True, "hardware_imported": hw_count,
             "fibers_imported": fb_count}
'''

if '_S40l_ Phase-2 endpoints' not in s:
    s = s.rstrip() + '\n' + addendum
    print('OK: Phase-2 endpoints appended')

open(p, 'w').write(s)
print('DONE.')
