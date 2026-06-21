"""
OLT/PON Manager — Session OLT-1
Owns:
  • Idempotent SQLite tables: olts, pon_ports, onus, olt_alerts,
    olt_alert_rules, olt_polls, olt_settings
  • Admin web pages at /admin/olt/{dashboard,onus,alerts,system-config}
  • JSON API at /api/admin/olt/* (used by both web UI and the mobile bridge)
  • Vendor adapter pattern (huawei / vsol / syrotech / nokia / mock)
  • A lightweight background poller that fans out to vendor adapters,
    detects fiber-cut events, and writes alerts.

Tenant isolation: every query is scoped by request.session["company_id"].
"""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, HTTPException, Body, Query

# __s56AZ_timeout_and_rpc__ — helper to run a blocking function with a
# hard timeout so it cannot stall the request beyond nginx's 60s window.
# Returns either the function's result, or a sentinel dict with ok=False
# when the timeout trips. Defensive: catches every exception from the
# inner call so callers don't have to.
import concurrent.futures as _s56az_futures
_s56az_pool = _s56az_futures.ThreadPoolExecutor(max_workers=8,
                                                thread_name_prefix="s56az-blkio")
def _s56az_with_timeout(fn, args=(), kwargs=None, *, timeout: float = 10.0,
                         fallback_msg: str = "operation timed out"):
    """Run `fn(*args, **kwargs)` in a worker thread with a hard timeout.


    On success: returns fn's result.
    On timeout / any exception inside fn: returns
        {"ok": False, "error": <reason>, "timeout": <bool>}
    so the caller can treat all failure modes uniformly. This is the ONLY
    behaviour change to the wifi/wan handlers — when the blocking call
    succeeds (the common path), behaviour is exactly as before. """
    if kwargs is None: kwargs = {}
    fut = _s56az_pool.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout)
    except _s56az_futures.TimeoutError:
        # Note: thread keeps running; we just stop waiting. CLI/HTTP socket
        # in the worker will eventually error out — that's fine.
        return {"ok": False, "error": fallback_msg, "timeout": True,
                "timeout_sec": timeout}
    except Exception as _e:
        return {"ok": False, "error": str(_e)[:300], "timeout": False}

from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

from database import engine

router = APIRouter()

# _S53F_ORPHAN_SCRUB — wipe orphan ONUs and network_hardware rows on
# module import. Cheap (single DELETE w/ subquery) and runs once per
# uvicorn worker startup.

# _S57_ZTP_BULK_JOBS_  SQLite-backed bulk-push job registry. Shared
# across all 4 uvicorn workers via WAL-mode SQLite (the same DB the
# rest of the app uses).
import uuid as _uuid_s57
import threading as _thr_s57
import json as _json_s57
_BULK_LOCK_ = _thr_s57.Lock()

def _bulk_db_init():
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS bulk_push_jobs (
                id          TEXT PRIMARY KEY,
                company_id  TEXT,
                olt_id      INTEGER,
                actor       TEXT,
                status      TEXT,
                started     TEXT,
                updated     TEXT,
                total       INTEGER DEFAULT 0,
                pushed      INTEGER DEFAULT 0,
                failed      INTEGER DEFAULT 0,
                results     TEXT,
                error       TEXT
            )""")
try: _bulk_db_init()
except Exception: pass

def _bulk_job_create(jid, cid, olt_id, actor, total):
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO bulk_push_jobs (id, company_id, olt_id, actor, "
            "status, started, updated, total, results) VALUES "
            "(?,?,?,?, 'queued', strftime('%Y-%m-%dT%H:%M:%S','now'), "
            "strftime('%Y-%m-%dT%H:%M:%S','now'), ?, '[]')",
            (jid, cid, olt_id, actor, total))

def _bulk_job_update(jid, **fields):
    if "results_append" in fields:
        item = fields.pop("results_append")
        # Read-modify-write under app-lock + DB-lock.
        with _BULK_LOCK_:
            with engine.begin() as conn:
                row = conn.exec_driver_sql(
                    "SELECT results FROM bulk_push_jobs WHERE id=?",
                    (jid,)).fetchone()
                if row:
                    arr = _json_s57.loads(row[0] or "[]")
                    arr.append(item)
                    conn.exec_driver_sql(
                        "UPDATE bulk_push_jobs SET results=?, "
                        "updated=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id=?",
                        (_json_s57.dumps(arr), jid))
    if not fields: return
    sets = ", ".join(f"{k}=?" for k in fields.keys())
    vals = tuple(list(fields.values()) + [jid])
    with engine.begin() as conn:
        conn.exec_driver_sql(
            f"UPDATE bulk_push_jobs SET {sets}, updated="
            "strftime('%Y-%m-%dT%H:%M:%S','now') WHERE id=?", vals)

def _bulk_job_get(jid):
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, company_id, olt_id, actor, status, started, "
            "updated, total, pushed, failed, results, error "
            "FROM bulk_push_jobs WHERE id=?", (jid,)).fetchone()
        if not row: return None
        return {"id": row[0], "company_id": row[1], "olt_id": row[2],
                "actor": row[3], "status": row[4], "started": row[5],
                "updated": row[6], "total": row[7], "pushed": row[8],
                "failed": row[9],
                "results": _json_s57.loads(row[10] or "[]"),
                "error": row[11]}

def _scrub_orphans_once():
    try:
        with engine.begin() as conn:
            n1 = conn.exec_driver_sql(
                "DELETE FROM onus WHERE olt_id NOT IN "
                "(SELECT id FROM olts)").rowcount
            n2 = conn.exec_driver_sql(
                "DELETE FROM network_hardware WHERE ref_olt_id IS NOT NULL "
                "AND ref_olt_id NOT IN (SELECT id FROM olts)").rowcount
            n3 = conn.exec_driver_sql(
                "DELETE FROM network_hardware WHERE ref_onu_id IS NOT NULL "
                "AND ref_onu_id NOT IN (SELECT id FROM onus)").rowcount
        if n1 or n2 or n3:
            print(f"[orphan-scrub] onus={n1} hw_by_olt={n2} hw_by_onu={n3}",
                  flush=True)
    except Exception as _e:
        pass

_scrub_orphans_once()


templates = Jinja2Templates(directory="templates")


# ──────────────────────────────────────────────────────────────────────────
#  Schema
# ──────────────────────────────────────────────────────────────────────────

VENDORS = ["huawei", "nokia", "optilink", "vsol",
           "syrotech", "syrotech_epon", "netlink", "netlink_epon",
           "cdata_epon", "zte", "fiberhome", "mock"]

ALERT_LEVELS = ["info", "warn", "critical"]
ALERT_KINDS = [
    "olt_offline", "olt_recovered",
    "fiber_cut", "fiber_recovered",
    "onu_offline", "onu_recovered",
    "low_rx", "high_rx",
    "high_temp", "high_cpu",
    "vendor_error",
]


def _ensure_schema() -> None:
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS olts (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id    TEXT NOT NULL,
              name          TEXT NOT NULL,
              vendor        TEXT NOT NULL DEFAULT 'mock',
              model         TEXT,
              host          TEXT NOT NULL,
              snmp_port     INTEGER DEFAULT 161,
              snmp_community TEXT DEFAULT 'public',
              snmp_version  TEXT DEFAULT 'v2c',
              cli_port      INTEGER DEFAULT 23,
              cli_username  TEXT,
              cli_password  TEXT,
              location      TEXT,
              poll_interval INTEGER DEFAULT 60,
              enabled       INTEGER NOT NULL DEFAULT 1,
              status        TEXT DEFAULT 'unknown',
              uptime_sec    INTEGER DEFAULT 0,
              cpu_pct       REAL DEFAULT 0,
              mem_pct       REAL DEFAULT 0,
              temp_c        REAL DEFAULT 0,
              total_onus    INTEGER DEFAULT 0,
              online_onus   INTEGER DEFAULT 0,
              last_polled   TEXT,
              last_seen_up  TEXT,
              created_at    TEXT DEFAULT (datetime('now')),
              created_by    TEXT
            )
        """)
        # _v4727_  Add VPN/connection columns idempotently
        for col, typ in [
            ("connection_mode", "TEXT NOT NULL DEFAULT 'public'"),
            ("vpn_address",     "TEXT"),
            ("vpn_peer_pubkey", "TEXT"),
            ("vpn_peer_privkey","TEXT"),
            ("vpn_psk",         "TEXT"),
        ]:
            try:
                conn.exec_driver_sql(f"ALTER TABLE olts ADD COLUMN {col} {typ}")
            except Exception: pass
        # s39: per-tenant WireGuard slice mapping (multi-tenant isolation)
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS wg_tenant_slices (
              company_id   TEXT PRIMARY KEY,
              slice_cidr   TEXT NOT NULL,
              allocated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS pon_ports (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              olt_id      INTEGER NOT NULL,
              port_index  INTEGER NOT NULL,
              name        TEXT,
              tx_power    REAL,
              admin_up    INTEGER DEFAULT 1,
              oper_up     INTEGER DEFAULT 1,
              total_onus  INTEGER DEFAULT 0,
              online_onus INTEGER DEFAULT 0,
              UNIQUE(olt_id, port_index)
            )
        """)
        # _S47F_  per-OLT telnet throttle to reduce log spam.
        try:
            conn.exec_driver_sql(
                "ALTER TABLE olts ADD COLUMN last_telnet_at TEXT")
        except Exception:
            pass
        try:
            conn.exec_driver_sql(
                "ALTER TABLE olts ADD COLUMN telnet_interval_sec INTEGER "
                "DEFAULT 300")
        except Exception:
            pass
                # _S47C_  pon_ports schema extension for real OLT-side optics.
        for _col in ("temperature_c REAL", "voltage_v REAL", "bias_current_ma REAL"):
            try:
                conn.exec_driver_sql(f"ALTER TABLE pon_ports ADD COLUMN {_col}")
            except Exception:
                pass
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS onus (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id      TEXT NOT NULL,
              olt_id          INTEGER NOT NULL,
              pon_port_index  INTEGER,
              onu_index       INTEGER,
              serial          TEXT,
              mac             TEXT,
              vendor          TEXT,
              model           TEXT,
              name            TEXT,
              customer_id     TEXT,
              status          TEXT DEFAULT 'unknown',
              rx_power        REAL,
              tx_power        REAL,
              distance_m      INTEGER,
              uptime_sec      INTEGER,
              last_seen       TEXT,
              last_offline    TEXT,
              offline_reason  TEXT,
              wifi_ssid       TEXT,
              wifi_password   TEXT,
              wan_ip          TEXT,
              wan_status      TEXT,
              notes           TEXT,
              created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_onus_company "
            "ON onus(company_id, olt_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_onus_status "
            "ON onus(company_id, status)"
        )
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS olt_alerts (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              company_id  TEXT NOT NULL,
              olt_id      INTEGER,
              onu_id      INTEGER,
              kind        TEXT NOT NULL,
              level       TEXT NOT NULL DEFAULT 'warn',
              title       TEXT NOT NULL,
              message     TEXT,
              meta_json   TEXT,
              acked       INTEGER NOT NULL DEFAULT 0,
              acked_by    TEXT,
              acked_at    TEXT,
              created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_alerts_company_acked "
            "ON olt_alerts(company_id, acked, created_at DESC)"
        )
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS olt_settings (
              company_id      TEXT PRIMARY KEY,
              rx_warn_dbm     REAL DEFAULT -25,
              rx_crit_dbm     REAL DEFAULT -28,
              fiber_cut_pct   REAL DEFAULT 50,
              fiber_cut_min   INTEGER DEFAULT 5,
              poll_interval   INTEGER DEFAULT 60,
              wa_enabled      INTEGER DEFAULT 1,
              wa_target       TEXT,
              email_enabled   INTEGER DEFAULT 0,
              email_target    TEXT,
              updated_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS olt_polls (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              olt_id      INTEGER NOT NULL,
              ts          TEXT DEFAULT (datetime('now')),
              ok          INTEGER DEFAULT 1,
              total_onus  INTEGER DEFAULT 0,
              online_onus INTEGER DEFAULT 0,
              cpu_pct     REAL DEFAULT 0,
              avg_rx      REAL,
              error       TEXT
            )
        """)
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_polls_olt_ts "
            "ON olt_polls(olt_id, ts DESC)"
        )




        # _OLT34_MANUAL_PIN — when set, poller won't overwrite vendor/model
        try:
            conn.exec_driver_sql(
                "ALTER TABLE onus ADD COLUMN manual_pin INTEGER DEFAULT 0"
            )
        except Exception:
            pass
        # _OLT3_PON_TYPE — EPON/GPON column on olts (idempotent)
        try:
            conn.exec_driver_sql(
                "ALTER TABLE olts ADD COLUMN pon_type TEXT DEFAULT 'GPON'"
            )
        except Exception:
            pass
        # _OLT3_GENIEACS — TR-069 columns (idempotent)
        for col, ddl in [
            ("genieacs_url",      "TEXT"),
            ("genieacs_username", "TEXT"),
            ("genieacs_password", "TEXT"),
            # _S40_AUTOPROV_ — when 1 the bridge auto-pushes Wi-Fi /
            # WAN / PPPoE updates to ACS as soon as they're saved.
            ("genieacs_auto_provision", "INTEGER DEFAULT 1"),
        ]:
            try:
                conn.exec_driver_sql(f"ALTER TABLE olt_settings ADD COLUMN {col} {ddl}")
            except Exception:
                pass
        # _S40b_PUSHLOG_ — audit trail for every TR-069 auto-push.
        try:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS acs_push_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id TEXT NOT NULL,
                    onu_id INTEGER,
                    onu_serial TEXT,
                    customer_id TEXT,
                    reason TEXT,
                    ok INTEGER NOT NULL,
                    skip TEXT,
                    error TEXT,
                    params_json TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_acs_push_log_co ON acs_push_log (company_id, id DESC)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_acs_push_log_onu ON acs_push_log (onu_id, id DESC)")
        except Exception:
            pass
        # _OLT2_WAN — WAN config columns (idempotent)
        for col, ddl in [
            ("wan_mode",         "TEXT"),
            ("wan_username",     "TEXT"),
            ("wan_password",     "TEXT"),
            ("wan_static_ip",    "TEXT"),
            ("wan_netmask",      "TEXT"),
            ("wan_gateway",      "TEXT"),
            ("wan_dns",          "TEXT"),
            ("wan_vlan",         "INTEGER"),
            ("wan_service_name", "TEXT"),
            # _S40c_DUALBAND_ — separate 5 GHz Wi-Fi config + advanced
            # radio options for both bands. Existing wifi_ssid /
            # wifi_password remain the 2.4 GHz primary.
            ("wifi_band_split",      "INTEGER DEFAULT 0"),  # 1 = different SSID for 5 GHz
            ("wifi_ssid_5g",         "TEXT"),
            ("wifi_password_5g",     "TEXT"),
            ("wifi_radio_24_enabled","INTEGER DEFAULT 1"),
            ("wifi_radio_5_enabled", "INTEGER DEFAULT 1"),
            ("wifi_auto_24",         "INTEGER DEFAULT 1"),
            ("wifi_auto_5",          "INTEGER DEFAULT 1"),
            ("wifi_channel_24",      "INTEGER"),     # 1..13
            ("wifi_channel_5",       "INTEGER"),     # 36/40/44/48/149/153/157/161/165
            ("wifi_bw_24",           "TEXT"),        # 20MHz | 40MHz | Auto
            ("wifi_bw_5",            "TEXT"),        # 20MHz | 40MHz | 80MHz | 160MHz | Auto
        ]:
            try:
                conn.exec_driver_sql(f"ALTER TABLE onus ADD COLUMN {col} {ddl}")
            except Exception:
                pass


_ensure_schema()


# ──────────────────────────────────────────────────────────────────────────
#  Auth (mirrors expenses_routes pattern)
# ──────────────────────────────────────────────────────────────────────────

def _require_scope(request: Request):
    """Returns dict {company_id, role, actor, layout, prefix}.
    Allowed roles: admin / superadmin / sub_lco (read OLT, write ONU) /
                   employee (read OLT, write ONU)."""
    sess = request.session
    cid = sess.get("company_id")
    ut = (sess.get("user_type") or "").lower()
    if not cid:
        raise HTTPException(401, "Not authenticated")
    role_map = {
        "admin":      ("admin",      "base_admin.html",      "/admin"),
        "superadmin": ("admin",      "base_admin.html",      "/admin"),
        "sublco":     ("sub_lco",    "base_sub_lco.html",    "/sub-lco"),
        "sub_lco":    ("sub_lco",    "base_sub_lco.html",    "/sub-lco"),
        "employee":   ("employee",   "base_employee.html",   "/employee"),
    }
    if ut not in role_map:
        raise HTTPException(403, "Forbidden")
    role, layout, prefix = role_map[ut]
    actor = sess.get("user_name") or sess.get("user_id") or role or "user"
    return {"company_id": cid, "role": role, "actor": str(actor),
            "layout": layout, "prefix": prefix}


def _require_admin(request: Request):
    """Backwards-compat shim — admin-only writes."""
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only action")
    return sc["company_id"], sc["actor"]


def _company_brand(cid: str) -> dict:
    """Logo / name / address for the company-context strip."""
    try:
        with engine.begin() as conn:
            r = conn.exec_driver_sql(
                "SELECT company_id, company_name, logo_path "
                "FROM companies WHERE company_id=?", (cid,)
            ).fetchone()
            if r:
                return {"company_id": r[0], "company_name": r[1] or "",
                        "logo_path": r[2] or ""}
    except Exception:
        pass
    return {"company_id": cid, "company_name": "", "logo_path": ""}


# ──────────────────────────────────────────────────────────────────────────
#  Vendor adapter pattern
# ──────────────────────────────────────────────────────────────────────────

class VendorResult(BaseModel):
    ok: bool = True
    error: Optional[str] = None
    olt: Dict[str, Any] = {}
    onus: List[Dict[str, Any]] = []


def _adapter_mock(olt: dict) -> VendorResult:
    """Deterministic but plausible mock — ideal for demos until real OLTs
    are wired. Generates 4 PON ports * 8 ONUs each."""
    rng = random.Random(int(olt["id"]))
    res = VendorResult()
    cpu_pct = round(rng.uniform(8, 35), 1)
    temp_c  = round(rng.uniform(38, 55), 1)
    res.olt = {
        "uptime_sec": int(time.time()) - int(olt.get("created_at_epoch") or
                                              time.time() - 86400 * 7),
        "cpu_pct": cpu_pct,
        "mem_pct": round(rng.uniform(35, 60), 1),
        "temp_c": temp_c,
        "status": "online",
    }
    onus: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for p in range(1, 5):
        for n in range(1, 9):
            sn_rng = random.Random(int(olt["id"]) * 1000 + p * 100 + n)
            base_rx = sn_rng.uniform(-26.5, -16.0)
            # 5% chance offline; 8% chance weak signal
            offline = sn_rng.random() < 0.05
            weak = (not offline) and sn_rng.random() < 0.08
            rx = -99.9 if offline else (base_rx - 4 if weak else base_rx)
            onus.append({
                "pon_port_index": p,
                "onu_index": n,
                "serial":  f"MOCK{olt['id']:02d}{p}{n:02d}",
                "mac":     ":".join("%02X" % sn_rng.randint(0, 255)
                                    for _ in range(6)),
                "vendor":  ("Huawei", "Nokia", "VSOL", "Syrotech")[sn_rng.randint(0, 3)],
                "model":   ("HG8310M", "G-1425", "V2802", "OS5GP")[sn_rng.randint(0, 3)],
                "rx_power": round(rx, 2),
                "tx_power": round(sn_rng.uniform(2.0, 3.5), 2),
                "distance_m": int(sn_rng.uniform(180, 4200)),
                "status": "offline" if offline else "online",
                "uptime_sec": 0 if offline else int(sn_rng.uniform(3600, 86400 * 14)),
                "last_seen": now,
            })
    res.onus = onus
    return res


# _S53E_VSOL_WEBMETA — short-TTL cache of VSOL Web UI metadata so we
# don't hammer the OLT's /action/main.html endpoint.
_VSOL_WEBMETA_CACHE = {}    # host -> (ts, dict)
_VSOL_WEBMETA_TTL   = 60    # seconds

def _vsol_webmeta(olt: dict) -> dict:
    """Returns {} on any failure. Otherwise the dict from
       olt_vsol_webmeta.fetch_vsol_web_meta (cpu_pct, mem_pct,
       uptime_sec). Honours a 60s per-host cache."""
    host = (olt.get("host") or "").strip()
    if not host:
        return {}
    now = time.time()
    cached = _VSOL_WEBMETA_CACHE.get(host)
    if cached and (now - cached[0]) < _VSOL_WEBMETA_TTL:
        return cached[1] or {}
    try:
        import olt_vsol_webmeta as _vwm
        r = _vwm.fetch_vsol_web_meta(
            host,
            olt.get("cli_username") or "admin",
            olt.get("cli_password") or "admin",
            port=int(olt.get("web_port") or 80),
            https=False, timeout=3.5)
        if r and r.get("ok"):
            _VSOL_WEBMETA_CACHE[host] = (now, r)
            return r
    except Exception:
        pass
    # Cache "no" too so we don't spam during outages.
    _VSOL_WEBMETA_CACHE[host] = (now, {})
    return {}


def _adapter_snmp_generic(olt: dict) -> VendorResult:
    """Real SNMP adapter — delegates to olt_vendors.poll_real. _S46I_:
    For EPON OLTs whose MIB has no optical readings, fall back to a
    telnet CLI scrape that fills rx_power / tx_power per ONU."""
    try:
        import olt_vendors
        ok, err, meta, onus = olt_vendors.poll_real(olt)
        # _S51_OLT_TEMP_AVG + _S52_DIST_MERGE — enrich meta
        # with per-PON telnet temps; also splice tel.distance,
        # tel.exchange.auth_state, and tel.name_by_pi back
        # into the ONU list (VSOL EPON SNMP MIB lacks all
        # three).
        try:
            tel = None
            need_temp = ok and not meta.get('temp_c')
            need_dist = ok and bool(onus)
            if need_temp or need_dist:
                tel = olt_vendors.poll_via_telnet(olt) or {}
            if tel and need_temp:
                pm  = tel.get('pon_metrics') or {}
                derived = _derive_olt_meta_from_pons(pm)
                if derived.get('temp_c'):
                    meta['temp_c'] = derived['temp_c']
                if pm:
                    meta['pon_metrics'] = pm
            # _S53E_VSOL_WEBMETA — fold CPU/MEM/uptime from VSOL Web UI.
            try:
                if (olt.get('vendor') or '').lower() in {
                        'vsol','vsol_epon','netlink','netlink_epon',
                        'syrotech','syrotech_epon','cdata','cdata_epon'}:
                    web = _vsol_webmeta(olt)
                    if web:
                        if web.get('cpu_pct') is not None and not meta.get('cpu_pct'):
                            meta['cpu_pct'] = float(web['cpu_pct'])
                        if web.get('mem_pct') is not None and not meta.get('mem_pct'):
                            meta['mem_pct'] = float(web['mem_pct'])
                        if web.get('uptime_sec') and not meta.get('uptime_sec'):
                            meta['uptime_sec'] = int(web['uptime_sec'])
            except Exception:
                pass
            # _S58P_CLI_CPU_  When SNMP + Web both report 0 / missing CPU,
            # last-resort: scrape from the OLT CLI.
            try:
                if not meta.get('cpu_pct') or not meta.get('mem_pct'):
                    cli = olt_vendors._cli_cpu_mem_scrape(olt) or {}
                    if cli.get('cpu_pct') is not None and not meta.get('cpu_pct'):
                        meta['cpu_pct'] = float(cli['cpu_pct'])
                    if cli.get('mem_pct') is not None and not meta.get('mem_pct'):
                        meta['mem_pct'] = float(cli['mem_pct'])
            except Exception:
                pass
            if tel and need_dist:
                dist = tel.get('distance') or {}
                exch = tel.get('exchange') or {}
                names = tel.get('name_by_pi') or {}
                opt   = tel.get('optical') or {}
                onu_m = tel.get('onu_metrics') or {}
                for o in onus:
                    k = (int(o.get('pon_port_index') or 0),
                         int(o.get('onu_index') or 0))
                    d = dist.get(k)
                    if d and not o.get('distance_m'):
                        o['distance_m'] = float(d)
                    if names.get(k) and not o.get('name_hint'):
                        o['name_hint'] = names[k]
                    em = exch.get(k) or {}
                    st_raw = (em.get('auth_state') or '').lower()
                    if any(x in st_raw for x in ('success','online',
                            'active','auth ok','los_ok')):
                        o['status'] = 'online'
                    rx_tx = opt.get(k)
                    if rx_tx and o.get('rx_power') in (None, 0):
                        o['rx_power'] = rx_tx[0]
                        o['tx_power'] = rx_tx[1]
                    mm = onu_m.get(k) or {}
                    for kk in ('temperature_c','voltage_v',
                                'bias_current_ma'):
                        if mm.get(kk) is not None and not o.get(kk):
                            o[kk] = mm[kk]
        except Exception:
            pass
    except Exception as e:
        return VendorResult(ok=False, error=f"vendor module load error: {e}")
    # _S49A_TELNET_FALLBACK — when SNMP fails on a VSOL-family
    # OLT, attempt a telnet-only poll. The persistent pool
    # makes this cheap; we synthesise minimal metadata from
    # the OLT auth-info table when meta is empty.
    if not ok:
        _v = (olt.get("vendor") or "").lower()
        if _v in ("vsol","vsol_epon","netlink","netlink_epon",
                  "syrotech","syrotech_epon","cdata","cdata_epon"):
            try:
                _tel = olt_vendors.poll_via_telnet(olt) or {}
                _name_pi = _tel.get("name_by_pi") or {}
                _dist    = _tel.get("distance") or {}
                _opt     = _tel.get("optical") or {}
                _exch    = _tel.get("exchange") or {}
                _onu_m   = _tel.get("onu_metrics") or {}
                _pon_m   = _tel.get("pon_metrics") or {}
                # __s56_fallback_propagate__ : per-(pon,idx) OLT-side
                # register/deregister/reason from `show onu status`
                _status  = _tel.get("status_by_pi") or {}
                # Build minimal ONU list from auth-info dump.
                _onus = []
                _seen = set()
                for (p, i), em in _exch.items():
                    _seen.add((p, i))
                    st_raw = (em.get("auth_state") or "").lower()
                    status = "online" if any(
                        k in st_raw for k in ("success","online",
                        "active","auth ok","los_ok")) else "offline"
                    rx_tx = _opt.get((p, i)) or (None, None)
                    mm = _onu_m.get((p, i)) or {}
                    _sbp = _status.get((p, i)) or {}
                    _onus.append({
                        "pon_port_index": int(p),
                        "onu_index": int(i),
                        "global_id": 0,
                        "serial": "",
                        "mac": _sbp.get("mac") or "",
                        "vendor": _v,
                        "model": "",
                        "rx_power": rx_tx[0],
                        "tx_power": rx_tx[1],
                        "distance_m": float(_dist.get((p, i)) or 0),
                        "status": status,
                        "uptime_sec": 0,
                        "name_hint": _name_pi.get((p, i)),
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                        "temperature_c": mm.get("temperature_c"),
                        "voltage_v":     mm.get("voltage_v"),
                        "bias_current_ma": mm.get("bias_current_ma"),
                        # __s56_fallback_propagate__
                        "olt_register_raw":   _sbp.get("last_register_raw")   or "",
                        "olt_deregister_raw": _sbp.get("last_deregister_raw") or "",
                        "olt_reason_raw":     _sbp.get("deregister_reason")   or "",
                    })
                # __s56_fallback_propagate__ : ONUs missing from _exch but
                # present in status_by_pi (active in show onu status table)
                for (p, i), _sbp in _status.items():
                    if (p, i) in _seen:
                        continue
                    rx_tx = _opt.get((p, i)) or (None, None)
                    mm    = _onu_m.get((p, i)) or {}
                    _sst  = (_sbp.get("status") or "").lower()
                    _onus.append({
                        "pon_port_index": int(p),
                        "onu_index":     int(i),
                        "global_id": 0,
                        "serial": "",
                        "mac": _sbp.get("mac") or "",
                        "vendor": _v, "model": "",
                        "rx_power": rx_tx[0], "tx_power": rx_tx[1],
                        "distance_m": float(_dist.get((p, i)) or 0),
                        "status": "online" if _sst == "online" else "offline",
                        "uptime_sec": 0,
                        "name_hint": _name_pi.get((p, i)),
                        "last_seen": datetime.now(timezone.utc).isoformat(),
                        "temperature_c": mm.get("temperature_c"),
                        "voltage_v":     mm.get("voltage_v"),
                        "bias_current_ma": mm.get("bias_current_ma"),
                        "olt_register_raw":   _sbp.get("last_register_raw")   or "",
                        "olt_deregister_raw": _sbp.get("last_deregister_raw") or "",
                        "olt_reason_raw":     _sbp.get("deregister_reason")   or "",
                    })
                # Synthesise OLT meta from per-PON metrics
                temps = [v.get("temp_c") for v in _pon_m.values()
                         if v.get("temp_c") is not None]
                _meta = {
                    "status": "online" if (_onus or _pon_m) else "online",
                    "uptime_sec": 0,
                    "cpu_pct": 0.0,
                    "mem_pct": 0.0,
                    "temp_c": (round(sum(temps)/len(temps), 1)
                              if temps else 0.0),
                    "sys_descr": "Telnet-only mode (SNMP unreachable)",
                    "pon_metrics": _pon_m,
                }
                if _onus or _pon_m:
                    _nm = olt.get("name")
                    print(f"[OLT poll] SNMP failed on {_nm} "
                          f"({err}) - telnet fallback ok: "
                          f"{len(_onus)} ONUs, {len(_pon_m)} PONs.")
                    return VendorResult(ok=True, olt=_meta, onus=_onus)
            except Exception as _tfe:
                print(f"[OLT poll] telnet fallback failed: {_tfe}")
        return VendorResult(ok=False, error=err or "unknown SNMP error")
    # _S46J_  Telnet fallback — fills RX/TX (when CLI exposes them) AND
    # ONU description / Wi-Fi SSID / WAN config (from running-config).
    # _S47F_  Telnet throttle — only log into the OLT every N seconds
    # (configurable per OLT, default 300 s) to avoid hammering its CLI and
    # filling the OLT's log file with session entries.
    skip_telnet = False
    try:
        with engine.begin() as _t_c:
            row_t = _t_c.exec_driver_sql(
                "SELECT last_telnet_at, COALESCE(telnet_interval_sec,300) "
                "FROM olts WHERE id=?",
                (olt["id"],)).fetchone()
        # _S47G_  Two-stage gate: (a) when interval==0 → telnet is
        # DISABLED for this OLT entirely (SNMP-only mode, default); (b)
        # otherwise rate-limit to once per interval seconds.
        interval = int(row_t[1] if row_t and row_t[1] is not None else 0)
        if interval <= 0:
            skip_telnet = True
        elif row_t and row_t[0]:
            from datetime import datetime as _dt, timezone as _tz
            last_dt = _dt.fromisoformat(row_t[0])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_tz.utc)
            delta = (_dt.now(_tz.utc) - last_dt).total_seconds()
            if delta < float(interval):
                skip_telnet = True
    except Exception:
        skip_telnet = False
    try:
        if skip_telnet:
            tel = {}
        else:
            tel = olt_vendors.poll_via_telnet(olt) or {}
            try:
                with engine.begin() as _t_c:
                    _t_c.exec_driver_sql(
                        "UPDATE olts SET last_telnet_at=? WHERE id=?",
                        (datetime.now(timezone.utc).isoformat(), olt["id"]))
            except Exception: pass
        opt = tel.get("optical") or {}
        cfg = tel.get("config")  or {}
        onu_metrics = tel.get("onu_metrics") or {}
        pon_metrics = tel.get("pon_metrics") or {}
        # _S47A_  Attach per-PON OLT-side optical to olt_meta so the
        # dashboard can show a per-PON temperature/Tx panel.
        if pon_metrics:
            meta["pon_metrics"] = pon_metrics
            try:
                # Average across active ports for a quick OLT-level temp.
                temps = [v.get("temp_c") for v in pon_metrics.values()
                          if v.get("temp_c") is not None]
                if temps and not meta.get("temp_c"):
                    meta["temp_c"] = round(sum(temps) / len(temps), 1)
            except Exception:
                pass
        # _S47D_  per-ONU distance from VSOL SNMP OID 5.12.1.17.1.3.<pon>.<onu>
        dist_map = tel.get("distance") or {}
        # _S47E_  Per-(pon,idx) exchange map for friendly deregister reason
        exch_map = tel.get("exchange") or {}
        # _S47E_  Per-(pon,idx) ONU description (name) from auth-info
        name_by_pi = tel.get("name_by_pi") or {}
        # __s56_status_by_pi__ : OLT-reported register/deregister/reason
        status_by_pi = tel.get("status_by_pi") or {}
        for o in onus:
            key = (int(o["pon_port_index"]), int(o["onu_index"]))
            # _S47E_  Prefer the auth-info description as ONU name when
            # running-config didn't expose it (most ONUs).
            if (not o.get("name")) and key in name_by_pi:
                o["name"] = name_by_pi[key]
            # __s56_status_by_pi__ : copy OLT-authoritative status row in
            sbp = status_by_pi.get(key)
            if sbp:
                if sbp.get("name") and not o.get("name"):
                    o["name"] = sbp["name"]
                if sbp.get("last_register_raw"):
                    o["olt_register_raw"]   = sbp["last_register_raw"]
                if sbp.get("last_deregister_raw"):
                    o["olt_deregister_raw"] = sbp["last_deregister_raw"]
                if sbp.get("deregister_reason"):
                    o["olt_reason_raw"]     = sbp["deregister_reason"]
                    o["offline_reason"]     = sbp["deregister_reason"]
            if key in opt:
                rx, tx = opt[key]
                o["rx_power"] = rx
                o["tx_power"] = tx
            if key in dist_map:
                o["distance_m"] = float(dist_map[key])
            # _S47A_  Real per-ONU metrics from `show onu opm-diag`
            mm = onu_metrics.get(key)
            if mm:
                if mm.get("temperature_c") is not None:
                    o["temperature_c"] = mm["temperature_c"]
                if mm.get("voltage_v") is not None:
                    o["voltage_v"] = mm["voltage_v"]
                if mm.get("bias_current_ma") is not None:
                    o["bias_current_ma"] = mm["bias_current_ma"]
            # _S46K_  Running-config references `onu N` where N is
            # the OLT-wide global ONU id (1..total). Use that as the
            # primary join key; fall back to per-PON onu_index only
            # if the parser path didn't capture a global_id.
            gid = int(o.get("global_id") or 0)
            md = cfg.get(gid) or {}
            for k in ("name", "wifi_ssid", "wan_mode", "wan_username",
                      "wan_static_ip"):
                if md.get(k) and not o.get(k):
                    o[k] = md[k]
            # _S47F_  Simplified reason — user asked for ONLY two states:
            #   "Fiber Cut" → LOS (Loss of Signal) on the fiber link
            #   "Power Off" → everything else that left the link (dying gasp,
            #                 MPCP DEREG / timeout, OAM lost, unauth).
            #   ""          → online (no reason to display)
            em = exch_map.get(key) or {}
            exch = (em.get("exchange") or md.get("exchange") or "").strip()
            if exch:
                low = exch.lower()
                if "finish" in low or "online" in low:
                    reason_t = ""
                elif "los" in low or "loss of signal" in low:
                    reason_t = "Fiber Cut"
                else:
                    reason_t = "Power Off"
                if reason_t or o.get("status") == "online":
                    o["offline_reason"] = reason_t
                o["__exchange_raw"] = exch
    except Exception:
        pass
    return VendorResult(ok=True, olt=meta, onus=onus)


def _run_adapter(olt: dict) -> VendorResult:
    v = (olt.get("vendor") or "mock").lower()
    if v == "mock":
        return _adapter_mock(olt)
    # _S39R5L_VENDOR — vendor-specific drivers (Nokia, Optilink, VSOL,
    # Syrotech, ZTE, Fiberhome, Huawei). They overlay vendor-private MIBs
    # (CPU%, temp, fan, ONU optical power) on top of the generic walk.
    try:
        import olt_vendor_drivers as _vd
        adapter = _vd.get_adapter(v)
        if adapter is not _vd.GenericAdapter:
            base = _adapter_snmp_generic(olt)
            if base.ok:
                env = adapter.poll_environment(
                    olt.get("ip"), olt.get("snmp_community") or "public",
                    timeout=int(olt.get("snmp_timeout") or 5))
                if env.get("ok"):
                    for k in ("cpu_pct", "temp_c", "voltage_v", "fan_status",
                              "uptime_s", "sys_name"):
                        v = env.get(k)
                        if v is None:
                            continue
                        # _S52_  Numeric metrics: skip when 0 (means OID
                        # doesn't exist on this firmware — keep the
                        # value we already derived from per-PON telnet).
                        if k in ("cpu_pct", "temp_c", "voltage_v",
                                 "uptime_s") and not v:
                            continue
                        base.olt[k if k != "uptime_s" else "uptime_sec"] = v
            # # __s56z_vendor_onu_fallback__
            # When the generic SNMP walk returned ZERO ONUs but the
            # vendor adapter has its own poll_onus (e.g. VsolAdapter
            # ifTable parser for V1600D/V1600G OEMs that hide vendor
            # OIDs), call it and remap into the dashboard's expected
            # row shape.
            if base.ok and not base.onus and hasattr(adapter, "poll_onus"):
                try:
                    vendor_onus = adapter.poll_onus(
                        olt.get("ip"),
                        olt.get("snmp_community") or "public",
                        timeout=int(olt.get("snmp_timeout") or 10),
                    ) or []
                except Exception as _e:
                    print(f"[vendor poll_onus] {_e}")
                    vendor_onus = []
                mapped = []
                for o in vendor_onus:
                    mapped.append({
                        "pon_port_index": int(o.get("pon") or 0),
                        "onu_index":      int(o.get("onu_id") or 0),
                        "serial":         o.get("sn"),
                        "mac":            o.get("mac"),
                        "vendor":         (olt.get("vendor") or "").upper(),
                        "model":          o.get("model"),
                        "rx_power":       o.get("rx_power"),
                        "tx_power":       o.get("tx_power"),
                        "distance_m":     o.get("distance_m"),
                        "status":         o.get("status") or "unknown",
                        "uptime_sec":     0,
                        "name":           o.get("name", ""),
                        "temperature_c":  o.get("temperature_c"),
                        "voltage_v":      o.get("voltage_v"),
                        "bias_current_ma": o.get("bias_current_ma"),
                        "wifi_ssid": "", "wan_mode": "",
                        "wan_username": "", "wan_static_ip": "",
                    })
                base.onus = mapped
            return base
    except Exception:
        pass
    return _adapter_snmp_generic(olt)


# ──────────────────────────────────────────────────────────────────────────
#  Polling worker
# ──────────────────────────────────────────────────────────────────────────

def _settings_for(cid: str) -> dict:
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT * FROM olt_settings WHERE company_id=?", (cid,)
        ).fetchone()
        if row:
            cols = [c[1] for c in conn.exec_driver_sql(
                "PRAGMA table_info(olt_settings)").fetchall()]
            return dict(zip(cols, row))
            # NB: legacy tuple-fallback unused below
            return dict(zip(keys, row))
        # Defaults
        return {"company_id": cid, "rx_warn_dbm": -25, "rx_crit_dbm": -28,
                "fiber_cut_pct": 50, "fiber_cut_min": 5,
                "poll_interval": 60, "wa_enabled": 1, "wa_target": None,
                "email_enabled": 0, "email_target": None,
                "genieacs_url": "", "genieacs_username": "",
                "genieacs_password": "",
                "genieacs_auto_provision": 1}



# ── WhatsApp alert dispatch (double-gated: superadmin Twilio creds + per-
# company whatsapp_config.is_active) ────────────────────────────────────────
def _whatsapp_can_send(cid: str) -> bool:
    """Returns True only if BOTH gates are open."""
    try:
        # Gate 1: SuperAdmin/system level — Twilio creds must be present.
        import os as _os
        if not (_os.environ.get("TWILIO_ACCOUNT_SID") and
                _os.environ.get("TWILIO_AUTH_TOKEN") and
                _os.environ.get("TWILIO_WHATSAPP_FROM")):
            return False
    except Exception:
        return False
    # Gate 2: Company level — whatsapp_config.is_active must be 1.
    try:
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT is_active FROM whatsapp_config "
                "WHERE id IN (SELECT MAX(id) FROM whatsapp_config) LIMIT 1"
            ).fetchone()
            if not row or not int(row[0] or 0):
                return False
    except Exception:
        return False
    return True


def _whatsapp_send_alert(cid: str, *, target: str, title: str,
                         message: str) -> None:
    if not target:
        return
    if not _whatsapp_can_send(cid):
        return
    try:
        # __MSG91_OLT_ALERT__
        import msg91_whatsapp as _mw
        ph = _mw.normalise_phone(target)
        if not ph:
            return
        addr = ""; cname = "AUTO ISP BILLING"
        try:
            import sqlite3 as _sl
            con = _sl.connect("/var/lib/autoispbilling/autoispbilling.db", timeout=3.0)
            r = con.execute(
                "SELECT company_name, company_address "
                "FROM companies WHERE company_id = ? LIMIT 1",
                (cid,)).fetchone()
            con.close()
            if r:
                cname = r[0] or cname
                addr = r[1] or ""
        except Exception:
            pass
        _mw.send_olt_critical_alert(
            phone=ph, company_name=cname,
            alert_title=title, alert_details=(message or "")[:400],
            company_address=addr, company_id=cid,
        )
    except Exception as e:
        print(f"[OLT WA send] {e}")


# _S58AZ_TELEGRAM_  Telegram alert dispatch — runs UNCONDITIONALLY
# whenever the tenant has telegram_bot_token + telegram_admin_chat_id
# configured on their profile (no separate enable/disable flag). Falls
# back to the env-var defaults so single-tenant deployments work.
def _telegram_send_alert(cid: str, *, title: str, message: str) -> None:
    """Best-effort Telegram message dispatch. Never raises."""
    if not (title or message):
        return
    try:
        import urllib.request, urllib.parse, json as _j, os as _os
        # Read per-tenant Telegram config from companies table.
        token = chat_id = None
        try:
            from database import engine as _eng
            with _eng.begin() as conn:
                # PG/SQLite — exec_driver_sql is portable
                row = conn.exec_driver_sql(
                    "SELECT telegram_bot_token, telegram_admin_chat_id "
                    "  FROM companies WHERE company_id=? LIMIT 1", (cid,)
                ).fetchone()
                if row:
                    token, chat_id = (row[0] or None), (row[1] or None)
        except Exception as _le:
            print(f"[telegram alert] tenant config lookup failed: {_le}")
        # Fallback to env vars for system-wide alerts.
        token   = token   or _os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = chat_id or _os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
        if not token or not chat_id:
            return
        # Compose message — Telegram supports basic HTML.
        text = (f"<b>{title}</b>\n{message}")[:3800]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            r.read(256)
    except Exception as e:
        print(f"[telegram alert send] {e}")


# __S44J__  Alert-kind → per-OLT toggle column map (matches S44D modal).
_S44J_KIND_TO_COL = {
    "olt_offline":     "alert_unit_offline",
    "fiber_cut":       "alert_unit_offline",
    "signal_critical": "alert_signal_critical",
    "signal_warning":  "alert_signal_warning",
    "high_power":      "alert_high_power",
    "uplink_down":     "alert_uplink_down",
    "uplink_change":   "alert_uplink_down",
}


def _emit_alert(cid: str, *, olt_id: Optional[int], onu_id: Optional[int],
                kind: str, level: str, title: str, message: str = "",
                meta: Optional[dict] = None) -> None:
    # __S44J__  Honor per-OLT toggle: if the admin unchecked this alert
    # category on the OLT's modal, silently skip both DB insert AND WA
    # fan-out. OLT-less alerts (onu_id only) and OLTs without the column
    # always pass through (back-compat with old rows).
    if olt_id is not None:
        col = _S44J_KIND_TO_COL.get(kind)
        if col:
            try:
                with engine.begin() as conn:
                    row = conn.exec_driver_sql(
                        f"SELECT {col} FROM olts WHERE id=?", (olt_id,)
                    ).fetchone()
                if row and int(row[0] or 0) == 0:
                    return  # admin explicitly silenced this alert kind
            except Exception:
                pass  # column missing on legacy DB → keep emitting
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO olt_alerts (company_id, olt_id, onu_id, kind, "
            "level, title, message, meta_json) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, olt_id, onu_id, kind, level, title, message,
             json.dumps(meta or {})),
        )
    # __S44J__  WhatsApp fan-out now covers ALL alert kinds the modal
    # exposes (when the per-OLT toggle is on). Previously only
    # critical/fiber_cut/olt_offline triggered a WA send.
    wa_kinds = ("fiber_cut", "olt_offline", "signal_critical",
                "signal_warning", "high_power", "uplink_down",
                "uplink_change")
    if level == "critical" or kind in wa_kinds:
        try:
            s = _settings_for(cid)
            if int(s.get("wa_enabled") or 0) and s.get("wa_target"):
                _whatsapp_send_alert(cid, target=s["wa_target"],
                                     title=title, message=message or "")
        except Exception as _wa_e:
            print(f"[OLT alert WA fan-out] {_wa_e}")
        # _S58AZ_TELEGRAM_  Always fire a parallel Telegram alert — no
        # per-tenant enable flag (the presence of bot_token+chat_id IS
        # the enable). Operators wanted alerts even when WA is off.
        try:
            _telegram_send_alert(cid, title=title, message=message or "")
        except Exception as _tg_e:
            print(f"[OLT alert TG fan-out] {_tg_e}")




# _S51_OLT_TEMP_AVG  When the OLT-vendor SNMP profile does not expose
# system temperature / CPU OIDs (true for VSOL/Netlink/Syrotech EPON
# firmwares) we derive the OLT chassis temperature as the average of
# the per-PON transceiver temperatures we already polled via telnet.
# Returns a dict with whatever keys we managed to compute; caller
# merges into the existing meta dict (only overwrites zeros).
def _derive_olt_meta_from_pons(pon_metrics: dict) -> dict:
    """pon_metrics: { pon_idx: {temp_c, voltage_v, bias_current_ma, tx_power} }"""
    out = {}
    if not pon_metrics:
        return out
    temps = [v.get("temp_c") for v in pon_metrics.values()
             if isinstance(v, dict) and v.get("temp_c") is not None]
    if temps:
        out["temp_c"] = round(sum(temps) / len(temps), 1)
    volts = [v.get("voltage_v") for v in pon_metrics.values()
             if isinstance(v, dict) and v.get("voltage_v") is not None]
    if volts:
        out["voltage_v"] = round(sum(volts) / len(volts), 2)
    return out


def _poll_one_olt(olt: dict) -> dict:
    cid = olt["company_id"]
    s = _settings_for(cid)
    res = _run_adapter(olt)
    now = datetime.now(timezone.utc).isoformat()

    if not res.ok:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE olts SET last_polled=?, status='error' WHERE id=?",
                (now, olt["id"]),
            )
            conn.exec_driver_sql(
                "INSERT INTO olt_polls (olt_id, ok, error) VALUES (?, 0, ?)",
                (olt["id"], (res.error or "")[:500]),
            )
        if (olt.get("status") or "") != "error":
            _emit_alert(cid, olt_id=olt["id"], onu_id=None,
                        kind="vendor_error", level="warn",
                        title=f"OLT {olt['name']} adapter error",
                        message=res.error or "")
        return {"ok": False, "error": res.error}

    # Persist OLT health
    with engine.begin() as conn:
        conn.exec_driver_sql(
            # _S52F_  Race-safe persist. When this worker got 0 (because
# another worker held the telnet pool lock and we lost the
# read race) we MUST NOT overwrite the last-good value.
"UPDATE olts SET status=?, "
            "uptime_sec=COALESCE(NULLIF(?,0), uptime_sec), "
            "cpu_pct=COALESCE(NULLIF(?,0), cpu_pct), "
            "mem_pct=COALESCE(NULLIF(?,0), mem_pct), "
            "temp_c=COALESCE(NULLIF(?,0), temp_c), "
            "last_polled=?, last_seen_up=? WHERE id=?",
            (res.olt.get("status", "online"),
             int(res.olt.get("uptime_sec") or 0),
             float(res.olt.get("cpu_pct") or 0),
             float(res.olt.get("mem_pct") or 0),
             float(res.olt.get("temp_c") or 0),
             now, now, olt["id"]),
        )

        # Upsert ONUs by (olt_id, pon_port_index, onu_index)
        prev_status: Dict[tuple, str] = {}
        prev_streak: Dict[tuple, int] = {}    # _S47E_
        prev_register: Dict[tuple, str] = {}  # _S47F_  last_register_time
        for row in conn.exec_driver_sql(
            "SELECT pon_port_index, onu_index, status, "
            "       COALESCE(offline_streak,0), "
            "       COALESCE(last_register_time,'') "
            "FROM onus WHERE olt_id=?",
            (olt["id"],)
        ).fetchall():
            prev_status[(row[0], row[1])] = row[2] or "unknown"
            prev_streak[(row[0], row[1])] = int(row[3] or 0)
            prev_register[(row[0], row[1])] = row[4] or ""

        rx_values = []
        online = 0
        new_offline = []
        new_online = []
        for o in res.onus:
            key = (o["pon_port_index"], o["onu_index"])
            prev = prev_status.get(key)
            if o["status"] == "online":
                online += 1
                if o.get("rx_power") is not None and o["rx_power"] > -90:
                    rx_values.append(o["rx_power"])
                o["__streak_reset"] = True   # offline_streak → 0
                if prev == "offline" or prev == "unknown" or prev is None:
                    o["__register_now"] = True
                    if prev == "offline":
                        new_online.append(o)
            else:
                # _S47E_  Debounce — only count as "newly offline" when the
                # ONU has been offline for 2+ consecutive polls. This kills
                # the fake "fiber cut suspected" bursts triggered by a
                # transient SNMP/Telnet glitch where one poll returns a
                # partial ONU snapshot.
                o["__streak_inc"] = True
                if prev == "online":
                    o["__deregister_now"] = True
                    if not o.get("offline_reason"):
                        o["offline_reason"] = "Link Down"
                # __s56c_clear_stale_rx__ : when the OLT says the ONU is
                # offline, any RX/TX reading we got from SNMP is stale
                # (the OLT keeps the last-seen optical reading after
                # MPCP-DEREG/dying-gasp until the next deregister event
                # clears the cache). Force-null it so the UI shows the
                # red “no-RX” dot consistently.
                o["rx_power"] = None
                o["tx_power"] = None
                # Only emit alerts after the second offline poll
                # in a row (offline_streak will be >= 1 by then).
                # _S47F_  Don't alert for ONUs that were never online to
            # begin with (first-time seen as offline).
            never_online = (
                prev == "unknown" or prev is None
                or not prev_register.get(
                    (o["pon_port_index"], o["onu_index"])))
            if (not never_online and
                    prev_streak.get((o["pon_port_index"], o["onu_index"]), 0)
                        >= 1):
                    new_offline.append(o)

            existing = conn.exec_driver_sql(
                "SELECT id FROM onus WHERE olt_id=? AND pon_port_index=? "
                "AND onu_index=?",
                (olt["id"], key[0], key[1])
            ).fetchone()
            if existing:
                conn.exec_driver_sql(
                    "UPDATE onus SET serial=?, mac=?, "
                    "vendor=CASE WHEN COALESCE(manual_pin,0)=1 THEN vendor ELSE ? END, "
                    "model=CASE WHEN COALESCE(manual_pin,0)=1 THEN model ELSE ? END, "
                    # _S50_  Preserve last good RX/TX until the
                    # next poll returns a real reading. Stops the
                    # "rx_power=blank" flicker between cycles.
                    "rx_power=COALESCE(?,rx_power), "
                    "tx_power=COALESCE(?,tx_power), "
                    "distance_m=?, status=?, "
                    "uptime_sec=?, last_seen=?, "
                    # _S47A_  Real per-ONU optical metrics from `show onu opm-diag`
                    "temperature_c=COALESCE(?,temperature_c), "
                    "voltage_v=COALESCE(?,voltage_v), "
                    "bias_current_ma=COALESCE(?,bias_current_ma), "
                    # _S46J_  Telnet-discovered metadata. Only overwrite
                    # when the DB field is currently empty so manual
                    # edits via UI win.
                    "name=COALESCE(NULLIF(name,''), ?), "
                    "wifi_ssid=COALESCE(NULLIF(wifi_ssid,''), ?), "
                    "wan_mode=COALESCE(NULLIF(wan_mode,''), ?), "
                    "wan_username=COALESCE(NULLIF(wan_username,''), ?), "
                    "wan_static_ip=COALESCE(NULLIF(wan_static_ip,''), ?), "
                    "last_offline=CASE WHEN ?='offline' "
                    "THEN ? ELSE last_offline END, "
                    # _S47D_  register/deregister timestamps + reason
                    "last_register_time=CASE WHEN ?=1 THEN ? ELSE "
                    "                    last_register_time END, "
                    # _S47E_  Always refresh offline_reason when we have a
                    # fresh value from auth-info; also set on transition.
                    "offline_reason=CASE "
                    "    WHEN ?!='' THEN ? "
                    "    WHEN ?=1 THEN ? "
                    "    ELSE offline_reason END, "
                    # _S47E_  offline_streak: reset to 0 when online,
                    # increment otherwise.
                    "offline_streak=CASE WHEN ?=1 THEN 0 "
                    "                    WHEN ?=1 THEN COALESCE(offline_streak,0)+1 "
                    "                    ELSE COALESCE(offline_streak,0) END, "
                    # __s56_status_by_pi__ raw OLT-reported strings
                    "olt_register_raw   = COALESCE(NULLIF(?, ''), olt_register_raw), "
                    "olt_deregister_raw = COALESCE(NULLIF(?, ''), olt_deregister_raw), "
                    "olt_reason_raw     = COALESCE(NULLIF(?, ''), olt_reason_raw) "
                    "WHERE id=?",
                    (o.get("serial"), o.get("mac"), o.get("vendor"),
                     o.get("model"), o.get("rx_power"), o.get("tx_power"),
                     o.get("distance_m"), o["status"],
                     int(o.get("uptime_sec") or 0), now,
                     o.get("temperature_c"), o.get("voltage_v"),
                     o.get("bias_current_ma"),
                     o.get("name"), o.get("wifi_ssid"),
                     o.get("wan_mode"), o.get("wan_username"),
                     o.get("wan_static_ip"),
                     o["status"], now,
                     1 if o.get("__register_now") else 0, now,
                     # _S47E_ offline_reason: (auth-info value) OR
                     # (deregister-flag-driven default)
                     (o.get("offline_reason") if o.get("__exchange_raw")
                         else ""),
                     (o.get("offline_reason") if o.get("__exchange_raw")
                         else ""),
                     1 if o.get("__deregister_now") else 0,
                     (o.get("offline_reason") or ""),
                     # _S47E_ offline_streak flags
                     1 if o.get("__streak_reset") else 0,
                     1 if o.get("__streak_inc") else 0,
                     # __s56_status_by_pi__ : OLT raw strings
                     o.get("olt_register_raw")   or "",
                     o.get("olt_deregister_raw") or "",
                     o.get("olt_reason_raw")     or "",
                     existing[0]),
                )
            else:
                conn.exec_driver_sql(
                    "INSERT INTO onus (company_id, olt_id, pon_port_index, "
                    "onu_index, serial, mac, vendor, model, rx_power, "
                    "tx_power, distance_m, status, uptime_sec, last_seen, "
                    # _S47A_  optical metrics
                    "temperature_c, voltage_v, bias_current_ma, "
                    # _S47D_  first-seen timestamp
                    "last_register_time, "
                    "name, wifi_ssid, wan_mode, wan_username, wan_static_ip, "
                    # __s56_status_by_pi__
                    "olt_register_raw, olt_deregister_raw, olt_reason_raw) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, olt["id"], key[0], key[1], o.get("serial"),
                     o.get("mac"), o.get("vendor"), o.get("model"),
                     o.get("rx_power"), o.get("tx_power"),
                     o.get("distance_m"), o["status"],
                     int(o.get("uptime_sec") or 0), now,
                     o.get("temperature_c"), o.get("voltage_v"),
                     o.get("bias_current_ma"),
                     now,  # _S47D_ last_register_time
                     o.get("name"), o.get("wifi_ssid"),
                     o.get("wan_mode"), o.get("wan_username"),
                     o.get("wan_static_ip"),
                     # __s56_status_by_pi__
                     o.get("olt_register_raw")   or "",
                     o.get("olt_deregister_raw") or "",
                     o.get("olt_reason_raw")     or ""),
                )

        total = len(res.onus)
        avg_rx = (sum(rx_values) / len(rx_values)) if rx_values else None
        conn.exec_driver_sql(
            "UPDATE olts SET total_onus=?, online_onus=? WHERE id=?",
            (total, online, olt["id"]),
        )
        # _S47C_  Persist OLT-side PON optical (from `show pon optical
        # transceiver`) into pon_ports so the dashboard tile + detail
        # page show real OLT TX, not ONU TX average.
        try:
            pm = (res.olt or {}).get("pon_metrics") or {}
            for p_idx, pm_v in pm.items():
                conn.exec_driver_sql(
                    "INSERT INTO pon_ports (olt_id, port_index, name, "
                    "tx_power, admin_up, oper_up, temperature_c, "
                    "voltage_v, bias_current_ma) "
                    "VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?) "
                    "ON CONFLICT(olt_id, port_index) DO UPDATE SET "
                    "tx_power=excluded.tx_power, "
                    "temperature_c=excluded.temperature_c, "
                    "voltage_v=excluded.voltage_v, "
                    "bias_current_ma=excluded.bias_current_ma",
                    (olt["id"], int(p_idx),
                     (("epon0/%d" % int(p_idx))
                       if (olt.get("olt_tech") or "EPON").upper()=="EPON"
                       else ("gpon0/%d" % int(p_idx))),
                     (float(pm_v.get("tx_power"))
                       if pm_v.get("tx_power") is not None else None),
                     (float(pm_v.get("temp_c"))
                       if pm_v.get("temp_c") is not None else None),
                     (float(pm_v.get("voltage_v"))
                       if pm_v.get("voltage_v") is not None else None),
                     (float(pm_v.get("bias_current_ma"))
                       if pm_v.get("bias_current_ma") is not None else None)
                    ))
        except Exception:
            pass
        conn.exec_driver_sql(
            "INSERT INTO olt_polls (olt_id, ok, total_onus, online_onus, "
            "cpu_pct, avg_rx) VALUES (?, 1, ?, ?, ?, ?)",
            (olt["id"], total, online,
             float(res.olt.get("cpu_pct") or 0), avg_rx),
        )

        # ─── _S60B_POLL_HOOK_  push samples into smartnet_* tables ─────────
        try:
            from smart_network import poll_hook_persist  # noqa
            poll_hook_persist(conn, cid=cid, olt=olt, res=res,
                               online=online, total=total, avg_rx=avg_rx)
        except Exception as _snh_e:
            print(f"[smartnet poll-hook] {_snh_e}")
        # ─── _S60B_AUTO_RECOVER_  wire _maybe_auto_recover_onu into loop ────
        try:
            for _ar_row in conn.exec_driver_sql(
                "SELECT id, auto_recovery_enabled, customer_id, "
                "       last_provisioned_at, wan_username, wan_static_ip, "
                "       wifi_ssid, wifi_ssid_5g "
                "FROM onus WHERE olt_id=? AND COALESCE(auto_recovery_enabled,0)=1 "
                "  AND customer_id IS NOT NULL", (olt["id"],)).fetchall():
                _ar = {"id": _ar_row[0], "auto_recovery_enabled": _ar_row[1],
                       "customer_id": _ar_row[2], "last_provisioned_at": _ar_row[3],
                       "wan_username": _ar_row[4], "wan_static_ip": _ar_row[5],
                       "wifi_ssid": _ar_row[6], "wifi_ssid_5g": _ar_row[7]}
                _maybe_auto_recover_onu(conn, cid, _ar)
        except Exception as _ar_e:
            print(f"[auto-recover hook] {_ar_e}")

    # ── Alerting (after commit so they read the new state cleanly)
    # _S47E_  Thin-snapshot guard: if THIS poll returned <80 % of the ONU
    # count we had a moment ago (likely a transient SNMP partial read or
    # Telnet rate-limit), skip the drop-alert generation entirely. The
    # ONUs that legitimately went offline will be picked up next poll
    # (their offline_streak only grows).
    # `total` here is the count of ONUs in this poll snapshot.
    if total and total > 0:
        try:
            with engine.begin() as _c3:
                prev_total = int(_c3.exec_driver_sql(
                    "SELECT COUNT(*) FROM onus WHERE olt_id=?",
                    (olt["id"],)).fetchone()[0] or 0)
            if prev_total > 0 and total < int(prev_total * 0.8):
                # Thin snapshot — suppress drop alerts for this cycle.
                print(f"[OLT poll] thin snapshot {total}/{prev_total} on "
                      f"OLT {olt.get('name')}, suppressing drop alerts.")
                new_offline = []
        except Exception:
            pass
    # Fiber-cut detection
    if new_offline:
        # Group by pon_port_index — if N% of a port's ONUs went offline at
        # once, treat it as a fiber cut on that port.
        port_groups: Dict[int, List[dict]] = {}
        for o in new_offline:
            port_groups.setdefault(o["pon_port_index"], []).append(o)
        # _S47F_  Dedupe fiber_cut alerts: one alert per (olt, pon)
        # while there is still an OPEN (un-acked) fiber_cut alert for that
        # pon. Re-fire only if the offline ONU count grew by ≥50 % beyond
        # the previous open alert — i.e. a NEW bunch dropped.
        for port_idx, lst in port_groups.items():
            with engine.begin() as conn:
                port_total = conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM onus WHERE olt_id=? AND "
                    "pon_port_index=?",
                    (olt["id"], port_idx)
                ).fetchone()[0] or 1
                # Look up the most recent OPEN fiber_cut alert for this PON.
                open_fc = conn.exec_driver_sql(
                    "SELECT meta_json FROM olt_alerts WHERE olt_id=? "
                    "AND kind='fiber_cut' AND COALESCE(acked,0)=0 "
                    "AND json_extract(meta_json,'$.port')=? "
                    "ORDER BY id DESC LIMIT 1",
                    (olt["id"], port_idx)
                ).fetchone()
            prev_off = 0
            if open_fc:
                try:
                    prev_off = int(json.loads(open_fc[0] or '{}').get(
                        "offline_count") or 0)
                except Exception:
                    prev_off = 0
            pct = 100.0 * len(lst) / port_total
            if (pct >= s["fiber_cut_pct"] and
                    len(lst) >= s["fiber_cut_min"]):
                # Re-fire only when the new offline count is ≥50 % larger
                # than the previous OPEN alert's count → "different bunch".
                if open_fc and len(lst) <= int(prev_off * 1.5):
                    pass   # already alerted; skip
                else:
                    _emit_alert(
                        cid, olt_id=olt["id"], onu_id=None,
                        kind="fiber_cut", level="critical",
                        title=f"Fiber cut suspected — OLT {olt['name']} "
                              f"PON {port_idx}",
                        message=f"{len(lst)} of {port_total} ONUs ({pct:.0f}%) "
                                f"on PON port {port_idx} dropped together.",
                        meta={"port": port_idx, "offline_count": len(lst),
                              "port_total": port_total},
                    )
            else:
                # Per-ONU offline — dedupe so we don't spam every poll.
                with engine.begin() as conn:
                    open_off = set()
                    for r in conn.exec_driver_sql(
                        "SELECT meta_json FROM olt_alerts WHERE olt_id=? "
                        "AND kind='onu_offline' AND COALESCE(acked,0)=0 ",
                        (olt["id"],)
                    ).fetchall():
                        try:
                            m = json.loads(r[0] or '{}')
                            open_off.add((int(m.get("port") or -1),
                                          int(m.get("onu_index") or -1)))
                        except Exception:
                            pass
                for o in lst:
                    if (port_idx, o["onu_index"]) in open_off:
                        continue  # already alerted, skip
                    _emit_alert(
                        cid, olt_id=olt["id"], onu_id=None,
                        kind="onu_offline", level="warn",
                        title=f"ONU offline — {o.get('serial')}",
                        message=f"OLT {olt['name']} PON{port_idx} "
                                f"ONU{o['onu_index']}",
                        meta={"port": port_idx, "onu_index": o["onu_index"]},
                    )

    if new_online:
        for o in new_online:
            _emit_alert(
                cid, olt_id=olt["id"], onu_id=None,
                kind="onu_recovered", level="info",
                title=f"ONU back online — {o.get('serial')}",
                message=f"OLT {olt['name']} PON{o['pon_port_index']} "
                        f"ONU{o['onu_index']}",
            )

    # Low-RX alerts: emit only when no OPEN low_rx alert exists for the
    # same ONU (dedupe so we don't spam every poll cycle).
    with engine.begin() as conn:
        already = set()
        for r in conn.exec_driver_sql(
            "SELECT meta_json FROM olt_alerts WHERE company_id=? AND "
            "olt_id=? AND kind='low_rx' AND acked=0",
            (cid, olt["id"])
        ).fetchall():
            try:
                m = json.loads(r[0] or '{}')
                if 'serial' in m:
                    already.add(m['serial'])
            except Exception:
                pass
    for o in res.onus:
        if (o["status"] == "online" and o.get("rx_power") is not None
                and o["rx_power"] != -99.9
                and o["rx_power"] <= s["rx_warn_dbm"]
                and o.get("serial") not in already):
            level = "critical" if o["rx_power"] <= s["rx_crit_dbm"] else "warn"
            _emit_alert(
                cid, olt_id=olt["id"], onu_id=None,
                kind="low_rx", level=level,
                title=f"Weak RX {o['rx_power']} dBm — {o.get('serial')}",
                message=f"OLT {olt['name']} PON{o['pon_port_index']} "
                        f"ONU{o['onu_index']}",
                meta={"rx_power": o["rx_power"], "serial": o.get("serial")},
            )

    return {"ok": True, "online": online, "total": len(res.onus)}


_poller_started = {"v": False}


def _poller_loop():
    while True:
        try:
            with engine.begin() as conn:
                rows = conn.exec_driver_sql(
                    "SELECT id, company_id, name, vendor, host, "
                    "snmp_community, status, poll_interval, "
                    "strftime('%s', created_at) AS created_at_epoch, "
                    # _S46L_  CLI creds + tech for telnet scrape
                    "cli_username, cli_password, telnet_port, olt_tech, "
                    "snmp_port, snmp_version "
                    "FROM olts WHERE enabled=1"
                ).fetchall()
            keys = ("id", "company_id", "name", "vendor", "host",
                    "snmp_community", "status", "poll_interval",
                    "created_at_epoch",
                    "cli_username", "cli_password", "telnet_port",
                    "olt_tech", "snmp_port", "snmp_version")
            for r in rows:
                olt = dict(zip(keys, r))
                try:
                    _poll_one_olt(olt)
                except Exception as e:
                    print(f"[OLT poll fail] {olt.get('name')}: {e}")
        except Exception as e:
            print(f"[OLT poller] outer err: {e}")
        time.sleep(int(os.environ.get("OLT_POLL_TICK", "30")))


def _start_poller():
    if _poller_started["v"]:
        return
    _poller_started["v"] = True
    t = threading.Thread(target=_poller_loop, daemon=True,
                         name="olt-poller")
    t.start()
    print("[+] OLT poller started")


# Seed PON type on existing demo OLTs (one-shot — only updates rows where
# pon_type is NULL).
def _seed_pon_type_once():
    try:
        with engine.begin() as _conn:
            rows = _conn.exec_driver_sql(
                "SELECT id FROM olts WHERE pon_type IS NULL OR pon_type=''"
            ).fetchall()
            for r in rows:
                t = "EPON" if (r[0] % 2 == 0) else "GPON"
                _conn.exec_driver_sql(
                    "UPDATE olts SET pon_type=? WHERE id=?", (t, r[0])
                )
    except Exception:
        pass

_seed_pon_type_once()
_start_poller()


# ──────────────────────────────────────────────────────────────────────────
#  Pydantic models
# ──────────────────────────────────────────────────────────────────────────

class OltIn(BaseModel):
    name: str
    vendor: str = "mock"
    model: Optional[str] = ""
    host: str
    snmp_port: int = 161
    snmp_community: str = "public"
    snmp_version: str = "v2c"
    cli_port: int = 23
    cli_username: Optional[str] = ""
    cli_password: Optional[str] = ""
    location: Optional[str] = ""
    poll_interval: int = 60
    enabled: int = 1
    pon_type: str = "GPON"
    # _v4727_  Connection mode: public IP or VPN tunnel
    connection_mode: str = "public"   # 'public' | 'vpn'
    vpn_address: Optional[str] = ""
    # __S44D__ Extended fields (all optional — back-compat preserved)
    telnet_port: int = 23
    ssh_port: int = 22
    web_port: int = 80
    pon_port_count: int = 16
    uplink_port_count: int = 16
    olt_tech: str = "GPON"           # GPON | EPON
    scan_profile: str = "Generic"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Alert toggles (default 1)
    alert_unit_offline: int = 1
    alert_signal_critical: int = 1
    alert_signal_warning: int = 1
    alert_high_power: int = 1
    alert_uplink_down: int = 1
    # Telegram / WhatsApp (per-OLT)
    telegram_bot_token: Optional[str] = ""
    telegram_chat_id: Optional[str] = ""
    whatsapp_instance_id: Optional[str] = ""
    whatsapp_api_key: Optional[str] = ""   # raw — will be encrypted before storage
    # VPN protocol expansion
    vpn_type: str = "none"           # none | wireguard | openvpn | l2tp_ipsec | pptp
    vpn_username: Optional[str] = ""
    vpn_password: Optional[str] = ""  # raw — will be encrypted before storage
    vpn_endpoint: Optional[str] = ""
    # # __s56q_via_nas_full__  via_nas mode: the parent NAS that carries the WG tunnel
    parent_nas_id: Optional[int] = None


class OnuPatch(BaseModel):
    name: Optional[str] = None
    customer_id: Optional[str] = None
    notes: Optional[str] = None
    wifi_ssid: Optional[str] = None
    wifi_password: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None


class SettingsIn(BaseModel):
    rx_warn_dbm: float = -25
    rx_crit_dbm: float = -28
    fiber_cut_pct: float = 50
    fiber_cut_min: int = 5
    poll_interval: int = 60
    wa_enabled: int = 1
    wa_target: Optional[str] = ""
    email_enabled: int = 0
    email_target: Optional[str] = ""
    genieacs_url: Optional[str] = ""
    genieacs_username: Optional[str] = ""
    genieacs_password: Optional[str] = ""
    genieacs_auto_provision: Optional[int] = 1


# ──────────────────────────────────────────────────────────────────────────
#  Helpers used by both web and JSON
# ──────────────────────────────────────────────────────────────────────────


def _temp_label(temp_c: float) -> str:
    if temp_c is None:
        return "Unknown"
    if temp_c >= 70:
        return "Critical"
    if temp_c >= 60:
        return "Overheat"
    return "Normal"


def _fan_label(temp_c: float, cpu_pct: float) -> str:
    """Heuristic: high temp ⇒ high fan; very high temp ⇒ malfunctional;
    very low temp ⇒ slow; otherwise Normal."""
    if temp_c is None:
        return "Unknown"
    if temp_c >= 70:
        return "Malfunctional"
    if temp_c >= 60:
        return "High"
    if temp_c < 35 and (cpu_pct or 0) < 10:
        return "Slow"
    return "Normal"


def _row_to_olt(r) -> dict:
    return {
        "id": r[0], "name": r[1], "vendor": r[2], "model": r[3] or "",
        "host": r[4], "location": r[5] or "", "enabled": int(r[6] or 0),
        "status": r[7] or "unknown", "uptime_sec": int(r[8] or 0),
        "cpu_pct": float(r[9] or 0), "mem_pct": float(r[10] or 0),
        "temp_c": float(r[11] or 0), "total_onus": int(r[12] or 0),
        "online_onus": int(r[13] or 0), "last_polled": r[14] or "",
        "poll_interval": int(r[15] or 60),
        "snmp_community": r[16] or "", "snmp_port": int(r[17] or 161),
        "cli_port": int(r[18] or 23), "cli_username": r[19] or "",
        "snmp_version": r[20] or "v2c",
        "pon_type": (r[21] if len(r) > 21 else "GPON") or "GPON",
        # s39: VPN status for the row-level pill + revoke button.
        "connection_mode": (r[22] if len(r) > 22 else "public") or "public",
        "vpn_address":     (r[23] if len(r) > 23 else "") or "",
        "vpn_peer_pubkey": (r[24] if len(r) > 24 else "") or "",
        # _S46M_  Topology + VPN fields
        "telnet_port":       int(r[25] or 23) if len(r) > 25 else 23,
        "ssh_port":          int(r[26] or 22) if len(r) > 26 else 22,
        "web_port":          int(r[27] or 80) if len(r) > 27 else 80,
        "pon_port_count":    int(r[28] or 16) if len(r) > 28 else 16,
        "uplink_port_count": int(r[29] or 16) if len(r) > 29 else 16,
        "olt_tech":          (r[30] if len(r) > 30 else "GPON") or "GPON",
        "scan_profile":      (r[31] if len(r) > 31 else "Generic") or "Generic",
        "latitude":          (r[32] if len(r) > 32 else None),
        "longitude":         (r[33] if len(r) > 33 else None),
        # _S50_  Indices were off-by-2 — vpn_type sits at r[42]
        # because we added 9 alert/notify cols (r[34..41]) before
        # the VPN trio in _OLT_COLS.
        "vpn_type":          (r[42] if len(r) > 42 else "none") or "none",
        "vpn_username":      (r[43] if len(r) > 43 else "") or "",
        "vpn_endpoint":      (r[44] if len(r) > 44 else "") or "",
        "parent_nas_id":     int(r[45] or 0) if len(r) > 45 else 0,
        "temp_label": _temp_label(float(r[11] or 0)),
        "fan_status": _fan_label(float(r[11] or 0), float(r[9] or 0)),
        "cpu_load": float(r[9] or 0),
    }


_OLT_COLS = (
    "id, name, vendor, model, host, location, enabled, status, "
    "uptime_sec, cpu_pct, mem_pct, temp_c, total_onus, online_onus, "
    "last_polled, poll_interval, snmp_community, snmp_port, cli_port, "
    "cli_username, snmp_version, COALESCE(pon_type,'GPON') AS pon_type, "
    "COALESCE(connection_mode,'public') AS connection_mode, "
    "COALESCE(vpn_address,'') AS vpn_address, "
    "COALESCE(vpn_peer_pubkey,'') AS vpn_peer_pubkey, "
    # __S44D__
    "COALESCE(telnet_port,23) AS telnet_port, "
    "COALESCE(ssh_port,22) AS ssh_port, "
    "COALESCE(web_port,80) AS web_port, "
    "COALESCE(pon_port_count,16) AS pon_port_count, "
    "COALESCE(uplink_port_count,16) AS uplink_port_count, "
    "COALESCE(olt_tech,'GPON') AS olt_tech, "
    "COALESCE(scan_profile,'Generic') AS scan_profile, "
    "latitude, longitude, "
    "COALESCE(alert_unit_offline,1)    AS alert_unit_offline, "
    "COALESCE(alert_signal_critical,1) AS alert_signal_critical, "
    "COALESCE(alert_signal_warning,1)  AS alert_signal_warning, "
    "COALESCE(alert_high_power,1)      AS alert_high_power, "
    "COALESCE(alert_uplink_down,1)     AS alert_uplink_down, "
    "COALESCE(telegram_bot_token,'')  AS telegram_bot_token, "
    "COALESCE(telegram_chat_id,'')    AS telegram_chat_id, "
    "COALESCE(whatsapp_instance_id,'') AS whatsapp_instance_id, "
    "COALESCE(vpn_type,'none')        AS vpn_type, "
    "COALESCE(vpn_username,'')        AS vpn_username, "
    "COALESCE(vpn_endpoint,'')        AS vpn_endpoint, "
    "COALESCE(parent_nas_id, 0)       AS parent_nas_id"  # __s56v_olt_ui_cleanup__
)


def _list_olts(cid: str) -> List[dict]:
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            f"SELECT {_OLT_COLS} FROM olts WHERE company_id=? "
            "ORDER BY name",
            (cid,),
        ).fetchall()
    return [_row_to_olt(r) for r in rows]


def _dashboard_payload(cid: str) -> dict:
    olts = _list_olts(cid)
    online_olts = sum(1 for o in olts if o["status"] == "online")
    total_onus = sum(o["total_onus"] for o in olts)
    online_onus = sum(o["online_onus"] for o in olts)
    offline_onus = max(0, total_onus - online_onus)

    with engine.begin() as conn:
        recent_alerts = conn.exec_driver_sql(
            "SELECT id, olt_id, onu_id, kind, level, title, message, "
            "acked, created_at FROM olt_alerts WHERE company_id=? "
            "ORDER BY id DESC LIMIT 20",
            (cid,),
        ).fetchall()
        worst = conn.exec_driver_sql(
            # _S47D_  Only ONUs in warn or critical bucket (≤ -25 dBm).
            "SELECT n.id, n.olt_id, n.pon_port_index, n.onu_index, "
            "n.serial, n.rx_power, n.status, "
            "COALESCE(o.name, 'OLT#' || n.olt_id) AS olt_name "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.company_id=? AND n.status='online' "
            "AND n.rx_power IS NOT NULL AND n.rx_power > -90 "
            "AND n.rx_power <= -25 "
            "ORDER BY n.rx_power ASC LIMIT 10",
            (cid,),
        ).fetchall()
        active_alerts = conn.exec_driver_sql(
            "SELECT COUNT(*), SUM(CASE WHEN level='critical' THEN 1 ELSE 0 END) "
            "FROM olt_alerts WHERE company_id=? AND acked=0",
            (cid,),
        ).fetchone()
        # _S46G_OPTICAL_HEALTH — bucket online ONUs by RX power.
        opt_buckets = conn.exec_driver_sql(
            "SELECT "
            "  SUM(CASE WHEN rx_power > -25 THEN 1 ELSE 0 END) AS stable, "
            "  SUM(CASE WHEN rx_power <= -25 AND rx_power > -28 THEN 1 ELSE 0 END) AS warn, "
            "  SUM(CASE WHEN rx_power <= -28 THEN 1 ELSE 0 END) AS crit "
            "FROM onus WHERE company_id=? AND status='online' AND rx_power IS NOT NULL",
            (cid,),
        ).fetchone()
        # OLT Environment aggregate (avg across reachable OLTs).
        env_row = conn.exec_driver_sql(
            "SELECT AVG(cpu_pct), AVG(mem_pct), AVG(temp_c) "
            "FROM olts WHERE company_id=? AND status='online'",
            (cid,),
        ).fetchone()
        optical_total = (int(opt_buckets[0] or 0)
                         + int(opt_buckets[1] or 0)
                         + int(opt_buckets[2] or 0))
        # technology mix
        tech_row = conn.exec_driver_sql(
            "SELECT COALESCE(olt_tech,'GPON'), COUNT(*) FROM olts "
            "WHERE company_id=? GROUP BY COALESCE(olt_tech,'GPON') "
            "ORDER BY 2 DESC LIMIT 1",
            (cid,),
        ).fetchone()

    return {
        "ok": True,
        "kpis": {
            "olts_total": len(olts),
            "olts_online": online_olts,
            "olts_offline": len(olts) - online_olts,
            "onus_total": total_onus,
            "onus_online": online_onus,
            "onus_offline": offline_onus,
            "alerts_open": int((active_alerts or [0])[0] or 0),
            "alerts_critical": int((active_alerts or [0, 0])[1] or 0),
        },
        # _S46G_  aspbilling-style optical health buckets + environment
        "optical": {
            "stable":   int(opt_buckets[0] or 0),
            "warning":  int(opt_buckets[1] or 0),
            "critical": int(opt_buckets[2] or 0),
            "total":    optical_total,
            "tech":     (tech_row[0] if tech_row else "GPON"),
            "limit":    -25 if (tech_row and tech_row[0] == "EPON") else -28,
        },
        "environment": {
            "cpu":  round(float(env_row[0] or 0), 1) if env_row else 0,
            "mem":  round(float(env_row[1] or 0), 1) if env_row else 0,
            "temp": round(float(env_row[2] or 0), 1) if env_row else 0,
        },
        "olts": olts,
        "recent_alerts": [
            {"id": r[0], "olt_id": r[1], "onu_id": r[2], "kind": r[3],
             "level": r[4], "title": r[5], "message": r[6] or "",
             "acked": int(r[7] or 0), "created_at": r[8]}
            for r in recent_alerts
        ],
        "worst_rx": [
            {"id": r[0], "olt_id": r[1], "pon_port_index": r[2],
             "onu_index": r[3], "serial": r[4] or "",
             "rx_power": float(r[5] or 0), "status": r[6] or "",
             "olt_name": r[7] if len(r) > 7 else None}
            for r in worst
        ],
    }


# ──────────────────────────────────────────────────────────────────────────
#  Web pages
# ──────────────────────────────────────────────────────────────────────────

def _portal_context(request: Request, sc: dict, active: str) -> dict:
    """Defer to the portal's native context helper so the topbar gets
    company_logo / company_name / admin_name / profile_image / etc.
    Falls back to a minimal session-derived dict if the helper isn't
    importable for any reason."""
    role = sc["role"]
    db = None
    try:
        from database import SessionLocal
        db = SessionLocal()
    except Exception:
        db = None
    ctx = None
    try:
        if role == "admin":
            from main import get_admin_context
            ctx = get_admin_context(request, db, active)
        elif role == "employee":
            from main import get_employee_context
            ctx = get_employee_context(request, db, active)
        elif role == "sub_lco":
            from sub_lco import get_sub_lco_context
            ctx = get_sub_lco_context(request, db, active)
    except Exception as e:
        print(f"[OLT ctx fallback] {role}: {e}")
    finally:
        if db is not None:
            try: db.close()
            except Exception: pass
    if not isinstance(ctx, dict):
        sess = request.session
        ctx = {"request": request,
               "user_id": sess.get("user_id", ""),
               "user_name": sess.get("user_name", ""),
               "user_type": sess.get("user_type", role),
               "company_id": sess.get("company_id", ""),
               "company_name": sess.get("company_name", ""),
               "company_logo": sess.get("company_logo"),
               "admin_name": sess.get("user_name", ""),
               "profile_image": sess.get("profile_image"),
               "active_page": active}
    ctx["request"] = request
    ctx["scope"] = sc
    ctx["layout_template"] = sc["layout"]
    ctx["olt_url_prefix"] = sc["prefix"] + "/olt"
    ctx["active_page"] = active
    return ctx



# ═══ _S40zα_  Scope-check helper for per-ONU mutation endpoints ════════════
def _enforce_onu_scope(request: Request, sc: dict, onu_id: int):
    """Allow admin always; sub-LCO only their customers' ONUs;
       employee only ONUs of customers they personally created."""
    role = sc["role"]
    if role == "admin":
        return
    cid = sc["company_id"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT c.sub_lco_id, c.created_by_employee_id FROM onus n "
            "LEFT JOIN customers c ON c.customer_id=n.customer_id "
            "AND c.company_id=n.company_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    sess = request.session
    if role == "sub_lco":
        sid = sess.get("sub_lco_db_id")
        if not sid or row[0] != int(sid):
            raise HTTPException(403, "ONU not in your sub-LCO scope")
    elif role == "employee":
        eid = sess.get("employee_id")
        if not eid:
            raise HTTPException(403, "Not authenticated")
        # _S40zβ_  Employee can act on their own customer's ONU OR any ONU
        # under the sub-LCO they belong to.
        if row[1] == int(eid):
            return
        with engine.begin() as eng_z:
            er = eng_z.exec_driver_sql(
                "SELECT sub_lco_id FROM employees WHERE id=? AND company_id=?",
                (int(eid), cid)).fetchone()
        if er and er[0] and row[0] == int(er[0]):
            return
        raise HTTPException(403, "ONU not in your employee scope")



# ─── Admin portal pages ─────────────────────────────────────────────────
@router.get("/admin/olt", response_class=HTMLResponse)
@router.get("/admin/olt/dashboard", response_class=HTMLResponse)
def page_dashboard_admin(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only — use /sub-lco/olt or "
                                  "/employee/olt for your role")
    ctx = _portal_context(request, sc, "olt_dashboard")
    ctx["data"] = _dashboard_payload(sc["company_id"])
    return templates.TemplateResponse("admin_olt_dashboard.html", ctx)


@router.get("/admin/olt/{olt_id:int}/detail", response_class=HTMLResponse)
def page_olt_detail_admin(olt_id: int, request: Request):
    """__S44U__ Per-OLT detail dashboard (aspbilling-style layout)."""
    sc = _require_scope(request)
    cid = sc["company_id"]
    with engine.begin() as conn:
        olt = conn.exec_driver_sql(
            "SELECT id, name, vendor, host, status, model, location, "
            "       cpu_pct, temp_c, last_polled, pon_port_count, uplink_port_count, "
            "       olt_tech, online_onus, total_onus "
            "FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not olt:
            raise HTTPException(404, "OLT not found")
        # ONU counts (defensive)
        try:
            onu_rows = conn.exec_driver_sql(
                "SELECT COALESCE(status,'unknown') st, COUNT(*) "
                "FROM onus WHERE olt_id=? GROUP BY status",
                (olt_id,)).fetchall()
        except Exception:
            onu_rows = []
        # Optics (defensive)
        try:
            opt_rows = conn.exec_driver_sql(
                "SELECT pon_port_index, rx_power, tx_power "
                "FROM onus WHERE olt_id=? AND rx_power IS NOT NULL",
                (olt_id,)).fetchall()
        except Exception:
            opt_rows = []
        # Recent ONU activity (defensive — schema varies per tenant)
        try:
            recent = conn.exec_driver_sql(
                # _S47C_  schema fix: use last_seen (real col) not updated_at
                "SELECT COALESCE(NULLIF(serial,''), mac, '') AS serial, "
                "       status, pon_port_index, onu_index, rx_power, "
                "       COALESCE(last_seen, created_at, '') AS upd "
                "FROM onus WHERE olt_id=? "
                "ORDER BY upd DESC LIMIT 20", (olt_id,)).fetchall()
        except Exception:
            recent = []
        # Open alerts (table may not exist on older tenants)
        try:
            alerts = conn.exec_driver_sql(
                # _S47C_  schema fix: column is `acked` not `resolved`. Also
                # surface critical/warn first.
                # _S47D_  Many existing alerts have onu_id=NULL; resolve ONU
                # by extracting MAC/serial from the alert title and matching
                # against onus.mac / onus.serial for the same OLT.
                "SELECT a.level, a.title, a.created_at, "
                "       n.pon_port_index, n.onu_index, "
                "       COALESCE(n.name,'') AS onu_name, "
                "       COALESCE(NULLIF(n.serial,''), n.mac, '') AS onu_serial "
                "FROM olt_alerts a "
                "LEFT JOIN onus n ON ("
                "      n.id=a.onu_id "
                "   OR (a.onu_id IS NULL AND n.olt_id=a.olt_id "
                "       AND ( instr(a.title, n.mac) > 0 "
                "          OR (n.serial IS NOT NULL AND n.serial!='' "
                "              AND instr(a.title, n.serial) > 0))) "
                ") "
                "WHERE a.olt_id=? AND COALESCE(a.acked,0)=0 "
                "ORDER BY CASE LOWER(level) WHEN 'critical' THEN 0 "
                "         WHEN 'warn' THEN 1 ELSE 2 END, "
                "a.created_at DESC LIMIT 10", (olt_id,)).fetchall()
        except Exception:
            alerts = []

    onu_by = {r[0]: r[1] for r in onu_rows}
    total_onu  = sum(onu_by.values())
    online_onu = onu_by.get("online", 0)
    offline_onu = onu_by.get("offline", 0)
    # _S47C_  count from DB instead of from the LIMIT-10 sample
    try:
        with engine.begin() as _conn2:
            crit_alerts = int(_conn2.exec_driver_sql(
                "SELECT COUNT(*) FROM olt_alerts WHERE olt_id=? "
                "AND LOWER(level)='critical' AND COALESCE(acked,0)=0",
                (olt_id,)).fetchone()[0] or 0)
    except Exception:
        crit_alerts = sum(1 for a in alerts if (a[0] or "").lower() == "critical")

    # Optics rollups per PON
    pon_optics = {}
    for pidx, rx, tx in opt_rows:
        pon_optics.setdefault(pidx, {"rx": [], "tx": []})
        if rx is not None: pon_optics[pidx]["rx"].append(float(rx))
        if tx is not None: pon_optics[pidx]["tx"].append(float(tx))

    pon_count = int(olt[10] or 8)
    uplink_count = int(olt[11] or 2)

    # _S46A_  Honour the configured pon_port_count by filtering out
    # ONUs that report a bogus pon_port_index > real PON count.
    opt_rows = [r for r in opt_rows
                if r[0] is not None and 1 <= int(r[0]) <= pon_count]

    # _S47C_  Use real OLT-side PON optical (tx_power, temperature, …)
    # from the pon_ports table (refreshed each poll).
    real_pon = {}
    try:
        with engine.begin() as _c:
            for r in _c.exec_driver_sql(
                "SELECT port_index, tx_power, temperature_c, voltage_v, "
                "bias_current_ma, name FROM pon_ports WHERE olt_id=?",
                (olt_id,)).fetchall():
                real_pon[int(r[0])] = {"tx": r[1], "temp_c": r[2],
                                        "v": r[3], "bias": r[4],
                                        "name": r[5]}
    except Exception:
        real_pon = {}
    pon_ports = []
    for i in range(1, pon_count + 1):
        d = pon_optics.get(i, {"rx": [], "tx": []})
        n_onus = sum(1 for r in opt_rows if r[0] == i)
        avg_rx = (sum(d["rx"])/len(d["rx"])) if d["rx"] else None
        rp = real_pon.get(i, {})
        olt_tx = rp.get("tx")
        avg_tx = (float(olt_tx) if olt_tx is not None
                  else (sum(d["tx"])/len(d["tx"]) if d["tx"] else None))
        if not d["rx"] and not n_onus:
            status = "idle"
        elif avg_rx is not None and avg_rx <= -28:
            status = "critical"
        elif avg_rx is not None and avg_rx <= -25:
            status = "warn"
        else:
            status = "online"
        pon_ports.append({
            "index": i, "n_onus": n_onus,
            "avg_rx": avg_rx, "avg_tx": avg_tx,
            "olt_tx": olt_tx,
            "temp_c": rp.get("temp_c"),
            "voltage_v": rp.get("v"),
            "bias_ma": rp.get("bias"),
            "status": status,
            "name": rp.get("name") or f"epon0/{i}",
        })

    # _S47C_REAL_UPLINKS — walk ifDescr / ifOperStatus / ifAlias /
    # ifHighSpeed on standard MIBs. Return ALL Ethernet ports we find
    # (not capped at the declared uplink_count — that value is often
    # stale). Also auto-update olts.uplink_port_count when the device
    # reports a different number.
    import re as _re
    uplink_ports = []
    try:
        from olt_vendors import _snmp_walk
# _S47C_  olt tuple index 14 was total_onus (INTEGER), not community.
        # Fetch SNMP community from DB by olt_id instead.
        comm = "public"
        try:
            with engine.begin() as _ccc:
                _cv = _ccc.exec_driver_sql(
                    "SELECT snmp_community FROM olts WHERE id=?",
                    (olt_id,)).fetchone()
                if _cv and _cv[0]: comm = _cv[0]
        except Exception:
            pass
        # _S47C2_  Use ifName (clean port names) + ifType (filter eth-only)
        # because ifDescr on VSOL OLTs is polluted with ONU descriptions.
        desc = dict(_snmp_walk(olt[3], comm, 161,
                                "1.3.6.1.2.1.31.1.1.1.1", "v2c", timeout=2.5))
        if not desc:
            desc = dict(_snmp_walk(olt[3], comm, 161,
                                    "1.3.6.1.2.1.2.2.1.2", "v2c", timeout=2.5))
        oper = dict(_snmp_walk(olt[3], comm, 161,
                                "1.3.6.1.2.1.2.2.1.8", "v2c", timeout=2.5))
        iftype = dict(_snmp_walk(olt[3], comm, 161,
                                "1.3.6.1.2.1.2.2.1.3", "v2c", timeout=2.0))
        try:
            alias = dict(_snmp_walk(olt[3], comm, 161,
                                    "1.3.6.1.2.1.31.1.1.1.18", "v2c",
                                    timeout=2.0))
        except Exception:
            alias = {}
        try:
            hispeed = dict(_snmp_walk(olt[3], comm, 161,
                                    "1.3.6.1.2.1.31.1.1.1.15", "v2c",
                                    timeout=2.0))
        except Exception:
            hispeed = {}
        candidates = []
        for ifidx, label in desc.items():
            lab_low = (label or "").lower()
            # Filter on ifType when available: 6=ethernetCsmacd.
            if iftype and (iftype.get(ifidx) or "").strip() != "6":
                # Allow only if iftype map was empty (legacy OLTs)
                continue
            # Drop synthetic / control / VLAN ifaces by name as well.
            if any(t in lab_low for t in ("pon", "epon", "gpon")):
                continue
            if any(t in lab_low for t in ("loopback", "null", "console",
                                           "vlan", "trunk")) or lab_low == "lo":
                continue
            # Accept anything that looks like a real ethernet port.
            if not any(t in lab_low for t in ("ge", "gig", "eth",
                                                "xe", "te", "100g")):
                # If iftype said 6 but label is bizarre, still accept.
                if iftype.get(ifidx, "").strip() != "6":
                    continue
            st_raw = (oper.get(ifidx) or "0").strip()
            st = ("up" if st_raw == "1" else "down" if st_raw == "2"
                  else "unknown")
            spd = (hispeed.get(ifidx) or "").strip()
            try:
                sm = int(spd)
                if sm >= 1000:
                    speed = (f"{sm//1000}G" if sm % 1000 == 0
                              else f"{sm/1000:.1f}G")
                elif sm > 0:
                    speed = f"{sm}M"
                else:
                    speed = "1G"
            except Exception:
                speed = "1G"
            candidates.append({
                "ifindex": int(ifidx),
                "label": label or f"GE{len(candidates)+1}",
                "alias": (alias.get(ifidx) or "").strip(),
                "status": st,
                "speed": speed,
            })
        def _sort_key(c):
            m = _re.search(r"(\d+)$", c["label"])
            return (int(m.group(1)) if m else 9999, c["ifindex"])
        candidates.sort(key=_sort_key)
        for i, c in enumerate(candidates, 1):
            uplink_ports.append({
                "index": i, "label": c["label"],
                "alias": c["alias"], "status": c["status"],
                "speed": c["speed"],
            })
        if uplink_ports and len(uplink_ports) != uplink_count:
            try:
                with engine.begin() as _cnt:
                    _cnt.exec_driver_sql(
                        "UPDATE olts SET uplink_port_count=? WHERE id=?",
                        (len(uplink_ports), olt_id))
                uplink_count = len(uplink_ports)
            except Exception:
                pass
    except Exception:
        uplink_ports = []
    if not uplink_ports:
        for i in range(1, max(1, uplink_count) + 1):
            uplink_ports.append({
                "index": i, "label": f"GE0/{i}",
                "alias": "", "status": "unknown", "speed": "1G",
            })

    rxs = [float(r[1]) for r in opt_rows if r[1] is not None]
    avg_rx = (sum(rxs)/len(rxs)) if rxs else None
    worst_rx = min(rxs) if rxs else None

    ctx = _portal_context(request, sc, "olt_dashboard")
    ctx["olt"] = {
        "id": olt[0], "name": olt[1], "vendor": olt[2], "host": olt[3],
        "status": olt[4], "model": olt[5], "location": olt[6],
        "cpu_pct": olt[7] or 0, "temp_c": olt[8] or 0,
        "last_polled": olt[9], "olt_tech": olt[12] or "EPON",
    }
    ctx["kpis"] = {
        "total_onu": total_onu, "online_onu": online_onu,
        "offline_onu": offline_onu, "crit_alerts": crit_alerts,
    }
    ctx["pon_ports"]    = pon_ports
    ctx["uplink_ports"] = uplink_ports
    ctx["avg_rx"]       = avg_rx
    ctx["worst_rx"]     = worst_rx
    ctx["recent_onus"]  = [
        {"serial": r[0], "status": r[1], "pon": r[2], "idx": r[3],
         "rx": r[4], "when": r[5]} for r in recent
    ]
    ctx["alerts"] = [
        # _S47D_  expose ONU PON/idx + name so the detail page can
        # show "PON 1 / ONU 17 (POTDAR_JEWELLER)" for traceability.
        {"level": a[0], "title": a[1], "when": a[2],
         "pon": a[3], "idx": a[4], "onu_name": a[5],
         "onu_serial": a[6]}
        for a in alerts
    ]
    return templates.TemplateResponse("admin_olt_detail.html", ctx)


@router.get("/admin/olt/onus", response_class=HTMLResponse)
def page_onus_admin(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only path")
    ctx = _portal_context(request, sc, "olt_onus")
    ctx["olts"] = _list_olts(sc["company_id"])
    return templates.TemplateResponse("admin_olt_onus.html", ctx)


@router.get("/admin/olt/alerts", response_class=HTMLResponse)
def page_alerts_admin(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only path")
    ctx = _portal_context(request, sc, "olt_alerts")
    return templates.TemplateResponse("admin_olt_alerts.html", ctx)


@router.get("/admin/olt/system-config", response_class=HTMLResponse)
def page_sysconfig(request: Request):
    cid, _ = _require_admin(request)
    sc = _require_scope(request)
    ctx = _portal_context(request, sc, "olt_system")
    ctx["olts"] = _list_olts(cid)
    ctx["settings"] = _settings_for(cid)
    ctx["vendors"] = VENDORS
    # # __s56p_nas_list_ssr__
    # SSR fallback: prefetch this tenant's NAS devices so the
    # "NAS (Mikrotik) carrying the WG tunnel" dropdown is populated
    # even if the JS fetch path fails (session race, ad-blocker, etc).
    try:
        from radius_network import NasDevice
        from database import SessionLocal as _SL
        _db = _SL()
        try:
            _nas_rows = _db.query(NasDevice).filter(
                NasDevice.company_id == cid,
            ).order_by(NasDevice.id.asc()).all()
            ctx["nas_devices_ssr"] = [
                {"id": n.id, "name": n.name or "", "ip_address": n.ip_address or "",
                 "status": n.status or "", "type": n.type or "Mikrotik",
                 "port": n.port or 8728}
                for n in _nas_rows
            ]
        finally:
            _db.close()
    except Exception as _e:
        ctx["nas_devices_ssr"] = []
        print(f"[olt sysconfig SSR nas] {_e}")
    return templates.TemplateResponse("admin_olt_system.html", ctx)


# ─── Sub-LCO portal pages ──────────────────────────────────────────────
@router.get("/sub-lco/olt", response_class=HTMLResponse)
@router.get("/sub-lco/olt/dashboard", response_class=HTMLResponse)
def page_dashboard_sublco(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "sub_lco":
        raise HTTPException(403, "Sub-LCO path")
    ctx = _portal_context(request, sc, "olt_dashboard")
    ctx["data"] = _dashboard_payload(sc["company_id"])
    return templates.TemplateResponse("admin_olt_dashboard.html", ctx)


@router.get("/sub-lco/olt/onus", response_class=HTMLResponse)
def page_onus_sublco(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "sub_lco":
        raise HTTPException(403, "Sub-LCO path")
    ctx = _portal_context(request, sc, "olt_onus")
    ctx["olts"] = _list_olts(sc["company_id"])
    return templates.TemplateResponse("admin_olt_onus.html", ctx)


@router.get("/sub-lco/olt/alerts", response_class=HTMLResponse)
def page_alerts_sublco(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "sub_lco":
        raise HTTPException(403, "Sub-LCO path")
    ctx = _portal_context(request, sc, "olt_alerts")
    return templates.TemplateResponse("admin_olt_alerts.html", ctx)


# ─── Employee portal pages ─────────────────────────────────────────────
@router.get("/employee/olt", response_class=HTMLResponse)
@router.get("/employee/olt/dashboard", response_class=HTMLResponse)
def page_dashboard_emp(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "employee":
        raise HTTPException(403, "Employee path")
    ctx = _portal_context(request, sc, "olt_dashboard")
    ctx["data"] = _dashboard_payload(sc["company_id"])
    return templates.TemplateResponse("admin_olt_dashboard.html", ctx)


@router.get("/employee/olt/onus", response_class=HTMLResponse)
def page_onus_emp(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "employee":
        raise HTTPException(403, "Employee path")
    ctx = _portal_context(request, sc, "olt_onus")
    ctx["olts"] = _list_olts(sc["company_id"])
    return templates.TemplateResponse("admin_olt_onus.html", ctx)


@router.get("/employee/olt/alerts", response_class=HTMLResponse)
def page_alerts_emp(request: Request):
    sc = _require_scope(request)
    if sc["role"] != "employee":
        raise HTTPException(403, "Employee path")
    ctx = _portal_context(request, sc, "olt_alerts")
    return templates.TemplateResponse("admin_olt_alerts.html", ctx)


# ──────────────────────────────────────────────────────────────────────────
#  JSON API — used by web pages and the mobile bridge
# ──────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/olt/dashboard")
def api_dashboard(request: Request):
    sc = _require_scope(request); cid = sc["company_id"]
    return _dashboard_payload(cid)


@router.get("/api/admin/olt/olts")
def api_list_olts(request: Request):
    sc = _require_scope(request); cid = sc["company_id"]
    return {"ok": True, "items": _list_olts(cid)}


@router.post("/api/admin/olt/olts")
def api_create_olt(request: Request, body: OltIn):
    # __S44D__ Extended INSERT — writes all new fields if provided.
    cid, actor = _require_admin(request)
    if body.vendor not in VENDORS:
        raise HTTPException(400, "Unknown vendor")
    # Encrypt sensitive raw values before persistence
    wa_key_enc = _s44d_encrypt(body.whatsapp_api_key or "")
    vpn_pw_enc = _s44d_encrypt(body.vpn_password or "")
    vpn_type_lc = (body.vpn_type or "none").lower()
    # If user picked a VPN type other than 'none', force connection_mode='vpn'
    # so the OLT row shows the right badge and the dispatcher knows to use
    # the encrypted endpoint instead of the public host.
    conn_mode = (body.connection_mode or "public").lower()
    if vpn_type_lc in ("wireguard", "openvpn", "l2tp_ipsec", "pptp"):
        conn_mode = "vpn"
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO olts (company_id, name, vendor, model, host, "
            " snmp_port, snmp_community, snmp_version, cli_port, "
            " cli_username, cli_password, location, poll_interval, enabled, "
            " pon_type, connection_mode, vpn_address, created_by, "
            " telnet_port, ssh_port, web_port, pon_port_count, "
            " uplink_port_count, olt_tech, scan_profile, latitude, longitude, "
            " alert_unit_offline, alert_signal_critical, alert_signal_warning, "
            " alert_high_power, alert_uplink_down, "
            " telegram_bot_token, telegram_chat_id, "
            " whatsapp_instance_id, whatsapp_api_key_enc, "
            " vpn_type, vpn_username, vpn_password_enc, vpn_endpoint) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, "
            "        ?,?,?,?,?,?,?,?,?, "
            "        ?,?,?,?,?, "
            "        ?,?,?,?, "
            "        ?,?,?,?)",
            (cid, body.name, body.vendor, body.model or "", body.host,
             body.snmp_port, body.snmp_community, body.snmp_version,
             body.cli_port, body.cli_username or "", body.cli_password or "",
             body.location or "", body.poll_interval, int(body.enabled),
             body.pon_type or "GPON", conn_mode, body.vpn_address or "", actor,
             int(body.telnet_port or 23), int(body.ssh_port or 22),
             int(body.web_port or 80), int(body.pon_port_count or 16),
             int(body.uplink_port_count or 16),
             (body.olt_tech or "GPON").upper(),
             body.scan_profile or "Generic",
             body.latitude, body.longitude,
             int(body.alert_unit_offline), int(body.alert_signal_critical),
             int(body.alert_signal_warning), int(body.alert_high_power),
             int(body.alert_uplink_down),
             body.telegram_bot_token or "", body.telegram_chat_id or "",
             body.whatsapp_instance_id or "", wa_key_enc,
             vpn_type_lc, body.vpn_username or "", vpn_pw_enc,
             body.vpn_endpoint or ""),
        )
        new_id = conn.exec_driver_sql("SELECT last_insert_rowid()").fetchone()[0]
        # # __s56q_via_nas_full__
        # via_nas mode: stash the parent NAS the OLT is reached through.
        # Only persist when the user actually picked a NAS — otherwise leave
        # NULL (a future migration could turn this into a FK with cascade).
        if (vpn_type_lc == "via_nas") and body.parent_nas_id:
            conn.exec_driver_sql(
                "UPDATE olts SET parent_nas_id=? WHERE id=?",
                (int(body.parent_nas_id), int(new_id)))
    return {"ok": True, "id": int(new_id)}


@router.patch("/api/admin/olt/olts/{olt_id}")
def api_update_olt(request: Request, olt_id: int, body: OltIn):
    # __S44D__ Extended UPDATE — preserves cli_password / vpn_password_enc /
    # whatsapp_api_key_enc when caller sends empty strings (so user editing
    # the modal without re-typing the password doesn't blank them out).
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        existing = conn.exec_driver_sql(
            "SELECT id FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not existing:
            raise HTTPException(404, "OLT not found")
        wa_key_enc = _s44d_encrypt(body.whatsapp_api_key or "")
        vpn_pw_enc = _s44d_encrypt(body.vpn_password or "")
        vpn_type_lc = (body.vpn_type or "none").lower()
        conn_mode = (body.connection_mode or "public").lower()
        if vpn_type_lc in ("wireguard", "openvpn", "l2tp_ipsec", "pptp"):
            conn_mode = "vpn"
        conn.exec_driver_sql(
            "UPDATE olts SET name=?, vendor=?, model=?, host=?, "
            " snmp_port=?, snmp_community=?, snmp_version=?, cli_port=?, "
            " cli_username=?, "
            " cli_password=COALESCE(NULLIF(?,''), cli_password), "
            " location=?, poll_interval=?, enabled=?, pon_type=?, "
            " connection_mode=?, vpn_address=?, "
            " telnet_port=?, ssh_port=?, web_port=?, "
            " pon_port_count=?, uplink_port_count=?, "
            " olt_tech=?, scan_profile=?, latitude=?, longitude=?, "
            " alert_unit_offline=?, alert_signal_critical=?, "
            " alert_signal_warning=?, alert_high_power=?, alert_uplink_down=?, "
            " telegram_bot_token=?, telegram_chat_id=?, "
            " whatsapp_instance_id=?, "
            " whatsapp_api_key_enc=COALESCE(NULLIF(?,''), whatsapp_api_key_enc), "
            " vpn_type=?, vpn_username=?, "
            " vpn_password_enc=COALESCE(NULLIF(?,''), vpn_password_enc), "
            " vpn_endpoint=?, "
            # _S58BK_UPDATE_PARENT_NAS_  This column was being silently
            # dropped from the SET clause — that's why edit-OLT didn't
            # actually move the OLT to its newly-picked NAS. Now persisted.
            " parent_nas_id=? "
            "WHERE id=? AND company_id=?",
            (body.name, body.vendor, body.model or "", body.host,
             body.snmp_port, body.snmp_community, body.snmp_version,
             body.cli_port, body.cli_username or "", body.cli_password or "",
             body.location or "", body.poll_interval, int(body.enabled),
             body.pon_type or "GPON", conn_mode, body.vpn_address or "",
             int(body.telnet_port or 23), int(body.ssh_port or 22),
             int(body.web_port or 80), int(body.pon_port_count or 16),
             int(body.uplink_port_count or 16),
             (body.olt_tech or "GPON").upper(),
             body.scan_profile or "Generic", body.latitude, body.longitude,
             int(body.alert_unit_offline), int(body.alert_signal_critical),
             int(body.alert_signal_warning), int(body.alert_high_power),
             int(body.alert_uplink_down),
             body.telegram_bot_token or "", body.telegram_chat_id or "",
             body.whatsapp_instance_id or "", wa_key_enc,
             vpn_type_lc, body.vpn_username or "", vpn_pw_enc,
             body.vpn_endpoint or "",
             # _S58BK_UPDATE_PARENT_NAS_  Pass the new NAS id (or 0 if cleared)
             int(body.parent_nas_id or 0),
             olt_id, cid),
        )
    # __s56AK__ sync WG AllowedIPs + kernel routes for via_nas OLTs
    if (vpn_type_lc or "").lower() == "via_nas":
        _s56AK_sync_via_nas(cid, body.parent_nas_id)
    return {"ok": True}


@router.delete("/api/admin/olt/olts/{olt_id}")
def api_delete_olt(request: Request, olt_id: int):
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        # __s56AK__ capture parent_nas_id BEFORE delete so we can resync
        existing = conn.exec_driver_sql(
            "SELECT id, parent_nas_id, vpn_type FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not existing:
            raise HTTPException(404, "OLT not found")
        conn.exec_driver_sql(
            "DELETE FROM olt_alerts WHERE company_id=? AND olt_id=?",
            (cid, olt_id))
        # _S53_MAP_CASCADE — wipe network-map pins (OLT + its ONUs)
        # so deleted hardware doesn't show in the Network Map sidebar
        # or as "unmapped" rows on the network page.
        onu_ids = [r[0] for r in conn.exec_driver_sql(
            "SELECT id FROM onus WHERE company_id=? AND olt_id=?",
            (cid, olt_id)).fetchall()]
        if onu_ids:
            placeholders = ",".join(["?"] * len(onu_ids))
            conn.exec_driver_sql(
                f"DELETE FROM network_hardware WHERE company_id=? "
                f"AND ref_onu_id IN ({placeholders})",
                (cid, *onu_ids))
        conn.exec_driver_sql(
            "DELETE FROM network_hardware WHERE company_id=? "
            "AND (ref_olt_id=? OR (kind='olt' AND ref_olt_id=?))",
            (cid, olt_id, olt_id))
        conn.exec_driver_sql(
            "DELETE FROM onus WHERE company_id=? AND olt_id=?",
            (cid, olt_id))
        conn.exec_driver_sql(
            "DELETE FROM olts WHERE company_id=? AND id=?",
            (cid, olt_id))
    # __s56AK__ re-sync: remove this OLT subnet from peer AllowedIPs if no
    # other via_nas OLT still uses the same parent NAS.
    try:
        if existing and len(existing) >= 3 and (existing[2] or "").lower() == "via_nas":
            _s56AK_sync_via_nas(cid, existing[1])
    except Exception as _e:
        print(f"[s56AK delete-resync FAIL] {_e}")
    return {"ok": True}


@router.post("/api/admin/olt/olts/{olt_id}/poll-now")
def api_poll_now(request: Request, olt_id: int):
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, company_id, name, vendor, host, snmp_community, "
            "status, poll_interval, "
            "strftime('%s', created_at) AS created_at_epoch, "
            # _S46L_  CLI creds + tech for telnet scrape
            "cli_username, cli_password, telnet_port, olt_tech, "
            "snmp_port, snmp_version FROM olts "
            "WHERE id=? AND company_id=?",
            (olt_id, cid)
        ).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    keys = ("id", "company_id", "name", "vendor", "host",
            "snmp_community", "status", "poll_interval",
            "created_at_epoch",
            "cli_username", "cli_password", "telnet_port", "olt_tech",
            "snmp_port", "snmp_version")
    return _poll_one_olt(dict(zip(keys, row)))


@router.get("/api/admin/olt/onus")
def api_list_onus(
    request: Request,
    olt_id: Optional[int] = None,
    status: Optional[str] = None,
    pon_port_index: Optional[int] = None,
    rx_max: Optional[float] = Query(None, description="Max RX dBm filter"),
    q: Optional[str] = "",
    page: int = 1,
    per: int = 100,
):
    """_S40zz_  Scope-aware listing — admin sees all, sub-LCO sees only
       their customers' ONUs, employee sees only ONUs of customers they
       created. Mounted under /admin/* but the route is also reachable
       from /sub-lco/* and /employee/* via the same templates."""
    sc = _require_scope(request); cid = sc["company_id"]
    sql = ("SELECT n.id, n.olt_id, o.name AS olt_name, n.pon_port_index, "
           "n.onu_index, n.serial, n.mac, n.vendor, n.model, n.name, "
           "n.customer_id, n.status, n.rx_power, n.tx_power, n.distance_m, "
           "n.uptime_sec, n.last_seen, n.last_offline, "
           # _S47D_  register/deregister + phone
           "n.last_register_time, n.offline_reason, "
           "c.customer_name AS customer_name, "
           "c.customer_phone AS customer_phone, "
           # _S46A_ Fiber link + TR069 columns + _S46F_ tech & wan_username
           "n.wan_mode, n.wifi_ssid, "
           "COALESCE(o.olt_tech, 'GPON') AS olt_tech, n.wan_username, "
           # __s56_status_by_pi__ raw OLT strings
           "COALESCE(n.olt_register_raw,'')   AS olt_register_raw, "
           "COALESCE(n.olt_deregister_raw,'') AS olt_deregister_raw, "
           "COALESCE(n.olt_reason_raw,'')     AS olt_reason_raw "
           "FROM onus n "
           "INNER JOIN olts o ON o.id=n.olt_id "
           "AND o.company_id=n.company_id "  # _S53_ONU_JOIN — drop orphans
           "LEFT JOIN customers c ON c.customer_id=n.customer_id "
           "AND c.company_id=n.company_id WHERE n.company_id=?")
    args: List[Any] = [cid]
    # Role scoping — sub-LCO and employee see only their own customer base.
    sess = request.session
    if sc["role"] == "sub_lco":
        sid = sess.get("sub_lco_db_id")
        if not sid:
            return {"ok": True, "items": []}
        sql += " AND c.sub_lco_id = ?"
        args.append(int(sid))
    elif sc["role"] == "employee":
        eid = sess.get("employee_id")
        if not eid:
            return {"ok": True, "items": []}
        # _S40zβ_  Employee sees ONUs of: customers they personally created,
        # PLUS all customers under the sub-LCO they belong to (so they can
        # support the whole sub-LCO's customer base — listing only).
        with engine.begin() as eng_z:
            er = eng_z.exec_driver_sql(
                "SELECT sub_lco_id FROM employees WHERE id=? AND company_id=?",
                (int(eid), cid)).fetchone()
        emp_sub_lco = er[0] if er else None
        if emp_sub_lco:
            sql += " AND (c.created_by_employee_id = ? OR c.sub_lco_id = ?)"
            args.extend([int(eid), int(emp_sub_lco)])
        else:
            sql += " AND c.created_by_employee_id = ?"
            args.append(int(eid))
    if olt_id:
        sql += " AND n.olt_id=?"; args.append(olt_id)
    if status:
        sql += " AND n.status=?"; args.append(status)
    if pon_port_index is not None:
        sql += " AND n.pon_port_index=?"; args.append(pon_port_index)
    if rx_max is not None:
        sql += " AND n.rx_power IS NOT NULL AND n.rx_power <= ?"
        args.append(rx_max)
    if q:
        sql += (" AND (n.serial LIKE ? OR n.mac LIKE ? OR n.name LIKE ? "
                "OR n.customer_id LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like, like]
    sql += " ORDER BY n.olt_id, n.pon_port_index, n.onu_index"
    sql += f" LIMIT {int(per)} OFFSET {int(max(page - 1, 0)) * int(per)}"
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(sql, tuple(args)).fetchall()
    keys = ("id", "olt_id", "olt_name", "pon_port_index", "onu_index",
            "serial", "mac", "vendor", "model", "name", "customer_id",
            "status", "rx_power", "tx_power", "distance_m", "uptime_sec",
            "last_seen", "last_offline",
            # _S47D_
            "last_register_time", "offline_reason",
            "customer_name", "customer_phone",
            "wan_mode", "wifi_ssid", "olt_tech", "wan_username",
            # __s56_status_by_pi__
            "olt_register_raw", "olt_deregister_raw", "olt_reason_raw")
    items = []
    for r in rows:
        d = dict(zip(keys, r))
        # _S46A_ Fiber-link health derived from rx_power + status. The
        # "TR069" column reflects whether the ONU was ever auto-pushed
        # to ACS (proxy: wifi_ssid is non-empty since we only push
        # those fields). Distance_m → KM string.
        rx = d.get("rx_power")
        st = (d.get("status") or "").lower()
        if st != "online":
            d["fiber_link"] = "down"
        elif rx is None or rx == 0 or rx == 0.0:
            # _S46N_  Vendor MIB/CLI doesn't expose optical power.
            d["fiber_link"] = "no-rx"
        elif rx <= -28:
            d["fiber_link"] = "critical"
        elif rx <= -25:
            d["fiber_link"] = "warn"
        else:
            d["fiber_link"] = "good"
        dist_m = d.get("distance_m") or 0
        d["fiber_distance_km"] = round(dist_m / 1000.0, 2) if dist_m else None
        d["tr069_enabled"] = bool(d.get("wifi_ssid"))
        items.append(d)
    return {"ok": True, "items": items}



def _push_onu_name_to_olt(host: str, user: str, pwd: str, vendor: str,
                          pon: int, idx: int, new_name: str) -> bool:
    """_S48G_  Pool-aware ONU rename. Delegates to olt_telnet_actions
    which supports VSOL (verified), Huawei, ZTE, Nokia branches and
    REUSES the persistent telnet session instead of opening a new one
    on every call (kills the AAA-login log spam on the OLT)."""
    if not host or pon is None or idx is None or not new_name:
        return False
    try:
        import olt_telnet_actions as _ota
        olt = {"host": host, "cli_username": user or "admin",
               "cli_password": pwd or "admin",
               "telnet_port": 23, "vendor": vendor or ""}
        res = _ota.rename_onu(olt, pon=int(pon), onu_idx=int(idx),
                              name=new_name)
        return bool(res and res.get("ok"))
    except Exception:
        return False


@router.patch("/api/admin/olt/onus/{onu_id}")
def api_patch_onu(request: Request, onu_id: int, body: OnuPatch):
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)   # _S40zα_
    # Validate customer_id (if set) belongs to this company.
    if body.customer_id:
        with engine.begin() as conn:
            ok = conn.exec_driver_sql(
                "SELECT 1 FROM customers WHERE customer_id=? AND company_id=?",
                (body.customer_id, cid)).fetchone()
        if not ok:
            raise HTTPException(404, f"Customer {body.customer_id} not found "
                                     "in this company.")

    # _s61I_PATCH_FIX_  (A) If customer_id is being patched (link OR unlink),
    # FORCE the ONU's wan_username/wan_password to reflect the new state.
    # Empty creds when the new customer has none — never inherit stale
    # values from a prior customer. This prevents the "username updated
    # but password stayed = ali123" bug the user reported.
    _force_blank_wan = False
    if body.customer_id is not None:
        if str(body.customer_id).strip():
            with engine.begin() as conn:
                _c = conn.exec_driver_sql(
                    "SELECT username, pppoe_password FROM customers "
                    "WHERE customer_id=? AND company_id=?",
                    (body.customer_id, cid)).fetchone()
            new_user = (_c[0] or "") if _c else ""
            new_pwd  = (_c[1] or "") if _c else ""
        else:
            # Unlink path: customer_id sent as empty string -> blank creds.
            new_user = ""
            new_pwd  = ""
            _force_blank_wan = True
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "UPDATE onus SET wan_username=?, wan_password=? "
                    "WHERE id=? AND company_id=?",
                    (new_user, new_pwd, onu_id, cid))
        except Exception as _e:
            pass  # best-effort — never block the patch on this
    fields = []
    args: List[Any] = []
    for k in ("name", "customer_id", "notes", "wifi_ssid", "wifi_password",
              "vendor", "model"):
        v = getattr(body, k, None)
        if v is not None:
            fields.append(f"{k}=?")
            args.append(v)
    if body.vendor is not None or body.model is not None:
        # Pin so the poller stops overwriting these on the next cycle.
        fields.append("manual_pin=1")
    if not fields:
        return {"ok": True}
    args += [onu_id, cid]
    with engine.begin() as conn:
        # _S47D_  Fetch OLT + PON/index to push name back to OLT.
        existing = conn.exec_driver_sql(
            "SELECT n.id, n.olt_id, n.pon_port_index, n.onu_index, n.name, "
            "o.host, o.cli_username, o.cli_password, o.vendor "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
        if not existing:
            raise HTTPException(404, "ONU not found")
        conn.exec_driver_sql(
            f"UPDATE onus SET {', '.join(fields)} WHERE id=? AND company_id=?",
            tuple(args)
        )
    # _S47D_  If name changed, push to OLT (best-effort, async-ish).
    if body.name is not None and existing[4] != body.name:
        try:
            _push_onu_name_to_olt(existing[5], existing[6], existing[7],
                                  existing[8], existing[2], existing[3],
                                  body.name)
        except Exception:
            pass  # don't fail the API call
    # _S40_AUTOPROV_ — if wifi or customer_id mutated, push to TR-069
    _wifi_or_link_changed = any(getattr(body, k, None) is not None
                                for k in ("wifi_ssid", "wifi_password",
                                          "customer_id"))
    # _s61I_PATCH_FIX_  (B) Dispatch the auto-push asynchronously so
    # the PATCH endpoint returns within ~50ms instead of blocking
    # the HTTP request for 60-90s while telnet/TR-069 roundtrips
    # complete. nginx upstream-timeout (60s) was turning these into
    # spurious 405 Not Allowed responses to the browser even though
    # the underlying push completed successfully.
    auto = {"async": True}
    if _wifi_or_link_changed:
        try:
            import threading
            _t = threading.Thread(
                target=_genieacs_auto_push,
                args=(cid, onu_id),
                kwargs={"reason": "patch_async_s61I"},
                daemon=True)
            _t.start()
        except Exception as _e:
            auto = {"async": False, "error": str(_e)[:160]}
    auto_legacy = None
    # _S60K_ZTP_SYNC — mirror customer link into ztp_onu_customer_mapping
    # so the new ZTP orchestrator + diagnostic engine see the change.
    if body.customer_id is not None:
        try:
            with engine.begin() as conn:
                _orow = conn.exec_driver_sql(
                    "SELECT serial, olt_id, pon_port_index, onu_index, "
                    "wan_username, wan_password, wan_vlan "
                    "FROM onus WHERE id=? AND company_id=?",
                    (onu_id, cid)).fetchone()
                if _orow and _orow[0]:
                    conn.exec_driver_sql(
                        "INSERT INTO ztp_onu_customer_mapping "
                        "(company_id, customer_id, olt_id, pon_port, "
                        " onu_index, onu_serial, pppoe_username, "
                        " pppoe_password, vlan_id, status) "
                        "VALUES (?,?,?,?,?,?,?,?,?, 'MAPPED') "
                        "ON CONFLICT (company_id, onu_serial) DO UPDATE "
                        "SET customer_id=EXCLUDED.customer_id, "
                        "    olt_id=COALESCE(EXCLUDED.olt_id, "
                        "      ztp_onu_customer_mapping.olt_id), "
                        "    pon_port=COALESCE(EXCLUDED.pon_port, "
                        "      ztp_onu_customer_mapping.pon_port), "
                        "    onu_index=COALESCE(EXCLUDED.onu_index, "
                        "      ztp_onu_customer_mapping.onu_index), "
                        "    pppoe_username=COALESCE(EXCLUDED.pppoe_username,"
                        "      ztp_onu_customer_mapping.pppoe_username), "
                        "    pppoe_password=COALESCE(EXCLUDED.pppoe_password,"
                        "      ztp_onu_customer_mapping.pppoe_password), "
                        "    vlan_id=COALESCE(EXCLUDED.vlan_id, "
                        "      ztp_onu_customer_mapping.vlan_id), "
                        "    status='MAPPED', "
                        "    last_state_change=NOW(), updated_at=NOW()",
                        (cid, body.customer_id, _orow[1], _orow[2], _orow[3],
                         _orow[0], _orow[4], _orow[5], _orow[6]))
        except Exception:
            pass
    # _s61I_PATCH_FIX_ sync auto-push removed - dispatched async earlier in the function
    return {"ok": True, "auto_provision": auto}


class WifiIn(BaseModel):
    # 2.4 GHz primary — kept for backwards-compatibility with the old
    # single-band callers (e.g. mobile app v4.x).
    ssid: Optional[str] = None
    password: Optional[str] = None
    # _S40c_DUALBAND_ — explicit per-band fields. When omitted, the
    # 2.4 GHz values fall through to 5 GHz unless band_split=1.
    band_split: Optional[int] = None     # 0 = same SSID/PW on both, 1 = separate
    ssid_5g: Optional[str] = None
    password_5g: Optional[str] = None
    radio_24_enabled: Optional[int] = None
    radio_5_enabled: Optional[int] = None
    auto_24: Optional[int] = None        # 1 = auto channel
    auto_5: Optional[int] = None
    channel_24: Optional[int] = None
    channel_5: Optional[int] = None
    bw_24: Optional[str] = None          # "20MHz" | "40MHz" | "Auto"
    bw_5: Optional[str] = None           # "20MHz" | "40MHz" | "80MHz" | "160MHz" | "Auto"


class WanIn(BaseModel):
    wan_mode: str  # pppoe | static | dhcp | bridge
    wan_service_name: Optional[str] = None  # PPPoE service-name / VLAN tag label
    wan_username: Optional[str] = None
    wan_password: Optional[str] = None
    wan_static_ip: Optional[str] = None
    wan_netmask: Optional[str] = None
    wan_gateway: Optional[str] = None
    wan_dns: Optional[str] = None
    wan_vlan: Optional[int] = None


@router.post("/api/admin/olt/onus/{onu_id}/wifi")
def api_onu_wifi(request: Request, onu_id: int, body: WifiIn):
    """_S40c_DUALBAND_ — persist 2.4 GHz + 5 GHz Wi-Fi config and
    auto-push to ACS. Backwards-compatible: callers passing only
    `ssid`/`password` (single-band v4.x mobile app) keep working — the
    same creds are applied to both bands when band_split is 0/null."""
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)   # _S40zα_
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT olt_id FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "ONU not found")
        # Build a dynamic UPDATE so each field is COALESCE'd only when
        # the caller actually sent it (None = leave untouched).
        sets = [
            "wifi_ssid=COALESCE(?, wifi_ssid)",
            "wifi_password=COALESCE(?, wifi_password)",
            "wifi_band_split=COALESCE(?, wifi_band_split)",
            "wifi_ssid_5g=COALESCE(?, wifi_ssid_5g)",
            "wifi_password_5g=COALESCE(?, wifi_password_5g)",
            "wifi_radio_24_enabled=COALESCE(?, wifi_radio_24_enabled)",
            "wifi_radio_5_enabled=COALESCE(?, wifi_radio_5_enabled)",
            "wifi_auto_24=COALESCE(?, wifi_auto_24)",
            "wifi_auto_5=COALESCE(?, wifi_auto_5)",
            "wifi_channel_24=COALESCE(?, wifi_channel_24)",
            "wifi_channel_5=COALESCE(?, wifi_channel_5)",
            "wifi_bw_24=COALESCE(?, wifi_bw_24)",
            "wifi_bw_5=COALESCE(?, wifi_bw_5)",
        ]
        args = (
            body.ssid, body.password,
            body.band_split,
            body.ssid_5g, body.password_5g,
            body.radio_24_enabled, body.radio_5_enabled,
            body.auto_24, body.auto_5,
            body.channel_24, body.channel_5,
            body.bw_24, body.bw_5,
            onu_id, cid,
        )
        conn.exec_driver_sql(
            f"UPDATE onus SET {', '.join(sets)} WHERE id=? AND company_id=?",
            args,
        )
    _emit_alert(cid, olt_id=row[0], onu_id=onu_id, kind="info", level="info",
                title=f"Wi-Fi config queued by {actor}",
                message=(f"2.4G SSID={body.ssid or '(unchanged)'}"
                         + (f" | 5G SSID={body.ssid_5g}" if body.ssid_5g else "")),
                meta={"actor": actor, "ssid": body.ssid,
                      "ssid_5g": body.ssid_5g})
    # _S48D_VSOL_WIRE — also push via OLT CLI for VSOL family
    cli_res = None
    try:
        with engine.begin() as _c48:
            _o48 = _c48.exec_driver_sql(
                "SELECT o.vendor, o.host, o.cli_username, o.cli_password, "
                "       o.telnet_port, o.snmp_community, n.pon_port_index, n.onu_index "
                "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
                "WHERE n.id=? AND n.company_id=?",
                (onu_id, cid)).fetchone()
        if _o48 and (_o48[0] or "").lower() in {"vsol","vsol_epon",
                "netlink","netlink_epon","syrotech","syrotech_epon",
                "cdata","cdata_epon"}:
            import olt_telnet_actions as _ota48
            # __s56AZ_timeout_and_rpc__ — bounded 10s CLI push
            cli_res = _s56az_with_timeout(
                _ota48.push_wifi,
                args=({"vendor": _o48[0], "host": _o48[1],
                       "cli_username": _o48[2], "cli_password": _o48[3],
                       "telnet_port": _o48[4], "snmp_community": _o48[5]},),
                kwargs={"pon": _o48[6], "onu_idx": _o48[7],
                        "ssid": body.ssid, "password": body.password,
                        "ssid_5g": body.ssid_5g, "password_5g": body.password_5g,
                        "radio_24_enabled": 1 if body.radio_24_enabled in (1, True, "1") else 0,
                        "radio_5_enabled": 1 if body.radio_5_enabled in (1, True, "1") else 0},
                timeout=10.0,
                fallback_msg="OLT CLI push timed out (OLT unreachable)")
    except Exception as _e48:
        cli_res = {"ok": False, "error": str(_e48)}
    # __s56AZ_timeout_and_rpc__ — bounded 10s ACS push
    auto = _s56az_with_timeout(_genieacs_auto_push,
        args=(cid, onu_id), kwargs={"reason": f"wifi by {actor}"},
        timeout=10.0, fallback_msg="ACS push timed out")
    # _S51_VSOL_VSOL_TR069_SKIP — when the OLT and ONU are both
    # VSOL family, CLI is authoritative and TR-069 is optional.
    try:
        if cli_res and cli_res.get("ok") and (cli_res.get("vendor") or "").lower() \
            in {"vsol","vsol_epon","netlink","netlink_epon",
                "syrotech","syrotech_epon","cdata","cdata_epon"}:
            auto["optional"] = True
    except Exception:
        pass
    # _S57B_AUTOSNAP_  Persist a snapshot of the WiFi payload so we
    # can re-apply it after a factory reset (SmartOLT-style recovery).
    try:
        if (cli_res and cli_res.get("ok")) or (auto and (auto.get("ok") or auto.get("optional"))):
            import onu_snapshot as _snap
            _snap.record_snapshot(cid, onu_id, None, "wifi",
                {"ssid": body.ssid, "password": body.password,
                 "ssid_5g": body.ssid_5g, "password_5g": body.password_5g,
                 "radio_24_enabled": int(bool(body.radio_24_enabled)),
                 "radio_5_enabled":  int(bool(body.radio_5_enabled))},
                pushed_by=actor)
    except Exception:
        pass
    return {"ok": True, "queued": "wifi", "onu_id": onu_id,
            "auto_provision": auto, "cli_push": cli_res}


@router.post("/api/admin/olt/onus/{onu_id}/wan")
def api_onu_wan(request: Request, onu_id: int, body: WanIn):
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)   # _S40zα_
    if body.wan_mode not in ("pppoe", "static", "dhcp", "bridge"):
        raise HTTPException(400, "wan_mode must be pppoe|static|dhcp|bridge")
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT olt_id FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "ONU not found")
        conn.exec_driver_sql(
            "UPDATE onus SET wan_mode=?, wan_username=?, wan_password=?, "
            "wan_static_ip=?, wan_netmask=?, wan_gateway=?, wan_dns=?, "
            "wan_vlan=?, wan_service_name=? WHERE id=? AND company_id=?",
            (body.wan_mode, body.wan_username or "",
             body.wan_password or "", body.wan_static_ip or "",
             body.wan_netmask or "", body.wan_gateway or "",
             body.wan_dns or "", body.wan_vlan,
             body.wan_service_name or "", onu_id, cid),
        )
    _emit_alert(cid, olt_id=row[0], onu_id=onu_id, kind="info", level="info",
                title=f"WAN config queued by {actor}",
                message=f"mode={body.wan_mode} vlan={body.wan_vlan or '-'}",
                meta={"actor": actor, "mode": body.wan_mode})
    # _S48D_VSOL_WIRE — also push via OLT CLI for VSOL family
    cli_res = None
    try:
        with engine.begin() as _c48:
            _o48 = _c48.exec_driver_sql(
                "SELECT o.vendor, o.host, o.cli_username, o.cli_password, "
                "       o.telnet_port, o.snmp_community, n.pon_port_index, n.onu_index "
                "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
                "WHERE n.id=? AND n.company_id=?",
                (onu_id, cid)).fetchone()
        if _o48 and (_o48[0] or "").lower() in {"vsol","vsol_epon",
                "netlink","netlink_epon","syrotech","syrotech_epon",
                "cdata","cdata_epon"}:
            import olt_telnet_actions as _ota48
            # __s56AZ_timeout_and_rpc__ — bounded 10s CLI push
            cli_res = _s56az_with_timeout(
                _ota48.push_wan,
                args=({"vendor": _o48[0], "host": _o48[1],
                       "cli_username": _o48[2], "cli_password": _o48[3],
                       "telnet_port": _o48[4], "snmp_community": _o48[5]},),
                kwargs={"pon": _o48[6], "onu_idx": _o48[7],
                        "mode": body.wan_mode,
                        "username": body.wan_username,
                        "password": body.wan_password,
                        "static_ip": body.wan_static_ip,
                        "netmask": body.wan_netmask,
                        "gateway": body.wan_gateway,
                        "dns": body.wan_dns,
                        "vlan": body.wan_vlan},
                timeout=10.0,
                fallback_msg="OLT CLI push timed out (OLT unreachable)")
    except Exception as _e48:
        cli_res = {"ok": False, "error": str(_e48)}
    # __s56AZ_timeout_and_rpc__ — bounded 10s ACS push
    auto = _s56az_with_timeout(_genieacs_auto_push,
        args=(cid, onu_id), kwargs={"reason": f"wan/{body.wan_mode} by {actor}"},
        timeout=10.0, fallback_msg="ACS push timed out")
    # _S51_VSOL_VSOL_TR069_SKIP — see api_onu_wifi for rationale.
    try:
        if cli_res and cli_res.get("ok") and (cli_res.get("vendor") or "").lower() \
            in {"vsol","vsol_epon","netlink","netlink_epon",
                "syrotech","syrotech_epon","cdata","cdata_epon"}:
            auto["optional"] = True
    except Exception:
        pass
    # _S57B_AUTOSNAP_  Persist a snapshot of the WAN payload
    try:
        if (cli_res and cli_res.get("ok")) or (auto and (auto.get("ok") or auto.get("optional"))):
            import onu_snapshot as _snap
            _snap.record_snapshot(cid, onu_id, None, "wan",
                {"wan_mode": body.wan_mode,
                 "pppoe_user": getattr(body, "pppoe_user", None),
                 "pppoe_pass": getattr(body, "pppoe_pass", None),
                 "vlan_id":    getattr(body, "vlan_id",    None),
                 "static_ip":  getattr(body, "static_ip",  None),
                 "static_mask":getattr(body, "static_mask",None),
                 "static_gw":  getattr(body, "static_gw",  None),
                 "dns1": getattr(body, "dns1", None),
                 "dns2": getattr(body, "dns2", None)},
                pushed_by=actor)
    except Exception:
        pass
    return {"ok": True, "queued": "wan", "onu_id": onu_id,
            "mode": body.wan_mode, "auto_provision": auto,
            "cli_push": cli_res}




@router.get("/api/admin/olt/onus-by-customer/{customer_id}")
def api_onus_by_customer(request: Request, customer_id: str):
    """Used by the customer-detail page to show a Connected-ONU card."""
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT n.id, n.olt_id, o.name AS olt_name, n.pon_port_index, "
            "n.onu_index, n.serial, n.mac, n.vendor, n.model, n.status, "
            "n.rx_power, n.tx_power, n.last_seen "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.company_id=? AND n.customer_id=? "
            "ORDER BY n.id DESC", (cid, customer_id)).fetchall()
    keys = ("id", "olt_id", "olt_name", "pon_port_index", "onu_index",
            "serial", "mac", "vendor", "model", "status", "rx_power",
            "tx_power", "last_seen")
    return {"ok": True, "items": [dict(zip(keys, r)) for r in rows]}


@router.get("/api/admin/olt/customers/search")
def api_customers_search(request: Request, q: str = "", limit: int = 12):
    sc = _require_scope(request); cid = sc["company_id"]
    sql = ("SELECT customer_id, customer_name, username, customer_phone "
           "FROM customers WHERE company_id=?")
    args: list = [cid]
    if q:
        sql += (" AND (customer_id LIKE ? OR customer_name LIKE ? "
                "OR username LIKE ? OR customer_phone LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like, like]
    sql += " ORDER BY customer_name LIMIT ?"
    args.append(int(max(1, min(limit, 50))))
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(sql, tuple(args)).fetchall()
    return {"ok": True, "items": [{"customer_id": r[0],
            "customer_name": r[1] or "", "username": r[2] or "",
            "phone": r[3] or ""} for r in rows]}


@router.get("/api/admin/olt/onus/{onu_id}")
def api_get_onu(request: Request, onu_id: int):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT n.*, o.name AS olt_name FROM onus n "
            "LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not r:
        raise HTTPException(404, "ONU not found")
    # SQLAlchemy row → dict using description from cursor not available here;
    # easier to re-fetch by names.
    with engine.begin() as conn:
        cur = conn.exec_driver_sql(
            "SELECT n.id, n.olt_id, o.name AS olt_name, n.pon_port_index, "
            "n.onu_index, n.serial, n.mac, n.vendor, n.model, n.name, "
            "n.customer_id, n.status, n.rx_power, n.tx_power, n.distance_m, "
            "n.uptime_sec, n.last_seen, n.last_offline, n.wifi_ssid, "
            "n.wifi_password, n.wan_ip, n.wan_status, n.wan_mode, "
            "n.wan_username, n.wan_password, n.wan_static_ip, n.wan_netmask, "
            "n.wan_gateway, n.wan_dns, n.wan_vlan, n.notes, "
            # _S40c_DUALBAND_
            "n.wifi_band_split, n.wifi_ssid_5g, n.wifi_password_5g, "
            "n.wifi_radio_24_enabled, n.wifi_radio_5_enabled, "
            "n.wifi_auto_24, n.wifi_auto_5, n.wifi_channel_24, "
            "n.wifi_channel_5, n.wifi_bw_24, n.wifi_bw_5 "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    keys = ("id", "olt_id", "olt_name", "pon_port_index", "onu_index",
            "serial", "mac", "vendor", "model", "name", "customer_id",
            "status", "rx_power", "tx_power", "distance_m", "uptime_sec",
            "last_seen", "last_offline", "wifi_ssid", "wifi_password",
            "wan_ip", "wan_status", "wan_mode", "wan_username",
            "wan_password", "wan_static_ip", "wan_netmask", "wan_gateway",
            "wan_dns", "wan_vlan", "notes",
            # _S40c_DUALBAND_
            "wifi_band_split", "wifi_ssid_5g", "wifi_password_5g",
            "wifi_radio_24_enabled", "wifi_radio_5_enabled",
            "wifi_auto_24", "wifi_auto_5", "wifi_channel_24",
            "wifi_channel_5", "wifi_bw_24", "wifi_bw_5")
    return {"ok": True, "onu": dict(zip(keys, cur))}




@router.post("/api/admin/olt/onus/{onu_id}/tr069-cli-push")
def api_onu_tr069_cli_push(request: Request, onu_id: int,
                            body: dict = Body(default={})):
    """_S57_ZTP_  Zero-Touch TR-069 ACS push via OLT CLI.

    Unlike `/genieacs-push` (which talks to the GenieACS NBI and requires
    the ONU to already be online with WAN), this endpoint instructs the
    OLT itself to push the ACS URL + credentials to the ONU directly
    over the fibre using EPON-OAM. Works even if the ONU has been
    factory-reset and has NO WAN/internet — perfect for first-boot or
    post-reset provisioning.

    Supported OLT vendors: VSOL, Netlink, Syrotech, CDATA (EPON).

    Body (all optional — falls back to per-company defaults):
      acs_url           – defaults to System Config GenieACS URL
      acs_username      – defaults to System Config GenieACS username
      acs_password      – defaults to System Config GenieACS password
      connreq_username  – defaults to acs_username
      connreq_password  – defaults to acs_password
      inform_interval   – seconds (default 300, range 60..86400)
      use_certificate   – bool (default False = plain http://)
    """
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)

    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id, n.pon_port_index, n.onu_index, "
            "       o.vendor, o.host, o.cli_username, o.cli_password, "
            "       o.telnet_port "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    olt_id, pon, onu_idx, vendor, host, cli_user, cli_pw, telnet_port = row
    vendor_l = (vendor or "").lower()
    # _S58R_VENDOR_ALLOWLIST_  multi-vendor — see olt_telnet_actions.push_tr069_acs
    if vendor_l not in {"vsol","vsol_epon","netlink","netlink_epon","syrotech","syrotech_epon","cdata","cdata_epon","optilink","optilink_epon","raisecom","raisecom_epon","khawahis","khawahis_epon","huawei","huawei_gpon","zte","zte_gpon","zte_c300","zte_c320","fiberhome","fiberhome_gpon","nokia","nokia_isam","alcatel","bdcom","bdcom_gpon"}:
        raise HTTPException(400,
            f"OLT vendor '{vendor}' does not support CLI TR-069 push. "
            "Use /genieacs-push (requires ONU online via TR-069).")

    # Resolve ACS settings: body override -> tr069_acs_url (CPE-facing) ->
    # genieacs_url (NBI fallback). The OLT pushes this URL to the ONU, so
    # it must be reachable FROM the CPE, NOT the internal NBI URL.
    s = _settings_for(cid)
    acs_url = (body.get("acs_url")
               or s.get("tr069_acs_url")
               or s.get("genieacs_url") or "").strip()
    if not acs_url:
        raise HTTPException(400,
            "ACS URL not configured. Set it in Admin → System Config "
            "(TR-069 ACS URL) or pass `acs_url` in the request body.")
    # Safety: if the URL is the internal NBI (127.0.0.1:7557), warn the
    # operator — the ONU CANNOT reach localhost from across the fibre.
    if "127.0.0.1" in acs_url or "localhost" in acs_url:
        raise HTTPException(400,
            f"ACS URL '{acs_url}' is internal-only. Set tr069_acs_url "
            "in olt_settings to the CPE-reachable URL, e.g. "
            "http://acs.autoispbilling.com/cwmp")
    acs_user = (body.get("acs_username")
                or s.get("genieacs_username") or "admin")
    acs_pw   = (body.get("acs_password")
                or s.get("genieacs_password") or "")
    cr_user  = body.get("connreq_username") or acs_user
    cr_pw    = body.get("connreq_password") or acs_pw
    interval = int(body.get("inform_interval") or 300)
    use_cert = bool(body.get("use_certificate") or False)

    import olt_telnet_actions as _ota
    olt_dict = {"vendor": vendor, "host": host,
                "cli_username": cli_user, "cli_password": cli_pw,
                "telnet_port": telnet_port or 23}

    # Bounded execution (10 s) so a hung OLT doesn't block the request.
    res = _s56az_with_timeout(
        _ota.push_tr069_acs,
        args=(olt_dict,),
        kwargs={"pon": pon, "onu_idx": onu_idx,
                "acs_url": acs_url,
                "acs_username": acs_user,
                "acs_password": acs_pw,
                "connreq_username": cr_user,
                "connreq_password": cr_pw,
                "inform_interval": interval,
                "use_certificate": use_cert},
        timeout=30.0,
        fallback_msg="OLT CLI push timed out (OLT unreachable)")
    # Audit-log
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                (cid, olt_id, onu_id, actor, "tr069-cli-push",
                 1 if res.get("ok") else 0,
                 (res.get("error") or res.get("note")
                  or ("ACS pushed via OLT CLI" if res.get("ok") else "FAIL"))[:500]))
    except Exception:
        pass
    _emit_alert(cid, olt_id=olt_id, onu_id=onu_id,
                kind="info", level="info",
                title=f"TR-069 OLT-CLI push by {actor} "
                      f"({'OK' if res.get('ok') else 'FAIL'})",
                message=(res.get("error") or res.get("note")
                          or f"ACS URL = {acs_url}")[:250])
    return res


@router.post("/api/admin/olt/onus/{onu_id}/tr069-cli-clear")
def api_onu_tr069_cli_clear(request: Request, onu_id: int):
    """_S57_ZTP_  Disable TR-069 ACS on an ONU via OLT CLI (tr069_mng disable)."""
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id, n.pon_port_index, n.onu_index, "
            "       o.vendor, o.host, o.cli_username, o.cli_password, "
            "       o.telnet_port "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    olt_id, pon, onu_idx, vendor, host, cli_user, cli_pw, telnet_port = row
    if (vendor or "").lower() not in {"vsol","vsol_epon","netlink",
            "netlink_epon","syrotech","syrotech_epon","cdata","cdata_epon"}:
        raise HTTPException(400,
            f"OLT vendor '{vendor}' does not support CLI TR-069 clear.")
    import olt_telnet_actions as _ota
    olt_dict = {"vendor": vendor, "host": host,
                "cli_username": cli_user, "cli_password": cli_pw,
                "telnet_port": telnet_port or 23}
    res = _s56az_with_timeout(
        _ota.clear_tr069_acs, args=(olt_dict,),
        kwargs={"pon": pon, "onu_idx": onu_idx},
        timeout=30.0, fallback_msg="OLT CLI push timed out")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                (cid, olt_id, onu_id, actor, "tr069-cli-clear",
                 1 if res.get("ok") else 0,
                 (res.get("error") or "TR-069 disabled via OLT CLI")[:500]))
    except Exception:
        pass
    return res




# ────────────────── _S60F_PROFILE_APPLY_  ZTP profile injection ───────────────
def _apply_service_profile(conn, cid: str, onu_id: int,
                            wan: Optional[Dict[str, Any]],
                            wifi: Optional[Dict[str, Any]],
                            tr069: Optional[Dict[str, Any]]
                            ) -> Dict[str, Any]:
    """Pull the ONU's bound service profile (if any) and merge its
    defaults into the wan/wifi/tr069 dicts. Profile fields only fill
    EMPTY values — explicit kwargs always win.

    Returns the (possibly-mutated) dicts so callers can use them.
    """
    try:
        prof_row = conn.exec_driver_sql(
            "SELECT p.name, p.connection_type, p.vlan, p.qos_dl_kbps, "
            "       p.qos_ul_kbps, p.wifi_ssid_tpl, p.wifi_pw_tpl, "
            "       p.wifi_band_split, p.acs_inform_int "
            "FROM onus n LEFT JOIN onu_service_profiles p "
            "  ON p.id = n.service_profile_id AND p.company_id = n.company_id "
            "WHERE n.id=? AND n.company_id=?", (onu_id, cid)).fetchone()
        if not prof_row or not prof_row[0]:
            return {"wan": wan or {}, "wifi": wifi or {},
                     "tr069": tr069 or {}, "profile_applied": None}
        (pname, ctype, vlan, dl_kbps, ul_kbps, ssid_tpl, pw_tpl,
          band_split, inform_int) = prof_row
        wan2 = dict(wan or {})
        wifi2 = dict(wifi or {})
        tr0692 = dict(tr069 or {})
        # WAN VLAN
        if vlan and not wan2.get("vlan"):
            wan2["vlan"] = vlan
        if ctype and not wan2.get("mode"):
            wan2["mode"] = ctype
        # WiFi SSID / password templates — substitute {cust} with onu_id
        if ssid_tpl and not wifi2.get("ssid"):
            wifi2["ssid"] = ssid_tpl.replace("{cust}", str(onu_id)) \
                                     .replace("{customer_id}", str(onu_id))
        if pw_tpl and not wifi2.get("password"):
            if pw_tpl == "auto-8":
                import secrets, string
                wifi2["password"] = "".join(
                    secrets.choice(string.ascii_letters + string.digits)
                    for _ in range(8))
            else:
                wifi2["password"] = pw_tpl
        if band_split and not wifi2.get("band_split"):
            wifi2["band_split"] = 1
        # TR-069 inform interval
        if inform_int and not tr0692.get("inform_interval"):
            tr0692["inform_interval"] = int(inform_int)
        return {"wan": wan2, "wifi": wifi2, "tr069": tr0692,
                 "profile_applied": pname,
                 "qos_dl_kbps": dl_kbps, "qos_ul_kbps": ul_kbps}
    except Exception as e:
        print(f"[_apply_service_profile] {e}")
        return {"wan": wan or {}, "wifi": wifi or {},
                 "tr069": tr069 or {}, "profile_applied": None}
# ──────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/olt/onus/{onu_id}/zero-touch-provision")
def api_onu_zero_touch_provision(request: Request, onu_id: int,
                                  body: dict = Body(default={})):
    """__PHASE19_2_SMART__  Resolve smart defaults from the customer +
    selected profile, merge any operator-supplied overrides from `body`,
    persist onto the ONU row, then run the existing OLT-CLI push path so
    WAN + dual-band Wi-Fi + LAN + DHCP + ACS all land in one transaction.
    """
    try:
        from smart_provision import (
            build_smart_defaults, fetch_customer_for_onu,
            merge_with_overrides, persist_to_onu)
        sc0 = _require_scope(request)
        _enforce_onu_scope(request, sc0, onu_id)
        cust = fetch_customer_for_onu(
            sc0["company_id"],
            (body or {}).get("customer_id"),
            onu_id=onu_id)
        defaults = build_smart_defaults(
            sc0["company_id"], cust,
            profile_id=(body or {}).get("profile_id"))
        resolved = merge_with_overrides(defaults, body or {})
        persist_to_onu(sc0["company_id"], onu_id, resolved)
        # When the operator picked a different profile, mirror it onto the row.
        if (body or {}).get("profile_id"):
            try:
                from database import engine as _eng
                with _eng.begin() as _c:
                    _c.exec_driver_sql(
                        "UPDATE onus SET service_profile_id=%s "
                        "WHERE id=%s AND company_id=%s",
                        (int(body["profile_id"]), onu_id, sc0["company_id"]))
            except Exception:
                pass
        # Surface resolved values into the body so the existing flow uses them.
        if isinstance(body, dict):
            body.setdefault("inform_interval", resolved["inform_interval"])
    except Exception as _e_smart:
        print(f"[smart_provision] {_e_smart}")
    """_S57_ZTP_  ONE-CLICK Zero-Touch Provisioning.

    Workflow for a freshly factory-reset (or brand-new) ONU:
      1) Push WAN config (PPPoE / static / dhcp / bridge) via OLT CLI
      2) Push 2.4G + 5G Wi-Fi SSID/password via OLT CLI
      3) Push TR-069 ACS URL via OLT CLI  <-- bypasses chicken-and-egg
      4) save_config on the ONU

    All four steps use the OLT-OMCI/EPON-OAM channel — no WAN/internet
    is required on the ONU side. After commit the ONU obtains internet
    via PPPoE, then immediately dials home to GenieACS using the freshly
    pushed ACS URL.
    """
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id, n.pon_port_index, n.onu_index, "
            "       o.vendor, o.host, o.cli_username, o.cli_password, "
            "       o.telnet_port, "
            "       n.wifi_ssid, n.wifi_password, "
            "       n.wifi_ssid_5g, n.wifi_password_5g, "
            "       n.wan_mode, n.wan_username, n.wan_password, "
            "       n.wan_static_ip, n.wan_netmask, n.wan_gateway, "
            "       n.wan_dns, n.wan_vlan "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    (olt_id, pon, onu_idx, vendor, host, cli_user, cli_pw, telnet_port,
     wifi_ssid, wifi_pw, wifi_ssid_5g, wifi_pw_5g,
     wan_mode, wan_user, wan_pw, wan_ip, wan_mask, wan_gw,
     wan_dns, wan_vlan) = row
    # _S58R_VENDOR_ALLOWLIST_2_  multi-vendor — handled by dispatcher
    if (vendor or "").lower() not in {"vsol","vsol_epon","netlink","netlink_epon","syrotech","syrotech_epon","cdata","cdata_epon","optilink","optilink_epon","raisecom","raisecom_epon","khawahis","khawahis_epon","huawei","huawei_gpon","zte","zte_gpon","zte_c300","zte_c320","fiberhome","fiberhome_gpon","nokia","nokia_isam","alcatel","bdcom","bdcom_gpon"}:
        raise HTTPException(400,
            f"OLT vendor '{vendor}' does not support zero-touch provisioning.")
    # __PHASE19_2_PRE_INIT__  wan/wifi/tr069 will be re-derived below from
    # the ONU row (which smart_provision has just freshly persisted). Initialize
    # to empty dicts so _apply_service_profile() has something to merge against.
    wan, wifi, tr069 = {}, {}, {}
    with engine.begin() as _c_sp:
        _sp = _apply_service_profile(_c_sp, cid, onu_id, wan, wifi, tr069)
    wan, wifi, tr069 = _sp["wan"], _sp["wifi"], _sp["tr069"]
    if _sp.get("profile_applied"):
        print(f"[ZTP] applied service profile '{_sp['profile_applied']}' to ONU {onu_id}")
    import olt_telnet_actions as _ota
    olt_dict = {"vendor": vendor, "host": host,
                "cli_username": cli_user, "cli_password": cli_pw,
                "telnet_port": telnet_port or 23}

    # Resolve ACS URL (prefer CPE-facing tr069_acs_url, refuse loopback).
    s = _settings_for(cid)
    acs_url = (body.get("acs_url")
               or s.get("tr069_acs_url")
               or s.get("genieacs_url") or "").strip()
    if acs_url and ("127.0.0.1" in acs_url or "localhost" in acs_url):
        # ACS URL stored is internal — don't push it; ONU can't reach loopback.
        tr069_arg = None
        tr069_skip_reason = (f"ACS URL '{acs_url}' is internal-only. Set "
                              "tr069_acs_url in olt_settings to a CPE-reachable URL.")
    elif not acs_url:
        tr069_arg = None
        tr069_skip_reason = "ACS URL not configured"
    else:
        acs_user = body.get("acs_username") or s.get("genieacs_username") or "admin"
        acs_pw   = body.get("acs_password") or s.get("genieacs_password") or ""
        tr069_arg = {
            "acs_url": acs_url, "acs_username": acs_user, "acs_password": acs_pw,
            "connreq_username": acs_user, "connreq_password": acs_pw,
            "inform_interval": int(body.get("inform_interval") or 300),
            "use_certificate": bool(body.get("use_certificate") or False),
        }
        tr069_skip_reason = None

    # Pass {} (not None) so zero_touch_provision_vsol() returns a
    # friendly skip message like "wan_mode not set on ONU" instead of
    # "not requested".
    if wan_mode:
        wan_arg = {"mode": wan_mode, "username": wan_user, "password": wan_pw,
                    "static_ip": wan_ip, "netmask": wan_mask, "gateway": wan_gw,
                    "dns": wan_dns, "vlan": wan_vlan}
    else:
        wan_arg = {}

    if wifi_ssid or wifi_ssid_5g:
        wifi_arg = {"ssid": wifi_ssid, "password": wifi_pw,
                     "ssid_5g": wifi_ssid_5g, "password_5g": wifi_pw_5g}
    else:
        wifi_arg = {}

    # ONE telnet session, ONE flock acquisition, all 3 pushes back-to-back.
    # 60-second budget (well within nginx's read_timeout) accommodates a cold
    # OLT login + 3 pushes + save_config.
    combined = _s56az_with_timeout(
        _ota.zero_touch_provision_vsol, args=(olt_dict,),
        kwargs={"pon": pon, "onu_idx": onu_idx,
                "wan": wan_arg, "wifi": wifi_arg, "tr069": tr069_arg},
        timeout=60.0,
        fallback_msg="Zero-Touch Provision timed out (OLT busy or unreachable). "
                     "Try again in 30 seconds or check the OLT VPN tunnel.")
    # Normalise: if the combined call itself timed out, present a uniform
    # per-step error so the UI still works.
    if combined.get("timeout"):
        results = {"wan":   {"ok": False, "error": combined.get("error")},
                    "wifi":  {"ok": False, "error": combined.get("error")},
                    "tr069": {"ok": False, "error": combined.get("error")}}
        ok = False
        summary = combined.get("error") or "timed out"
    else:
        results = combined.get("details") or {}
        # Apply ACS skip-reason if we never sent it.
        if tr069_arg is None and tr069_skip_reason:
            results["tr069"] = {"ok": False, "skip": tr069_skip_reason}
        ok = combined.get("ok", False) and (not tr069_skip_reason)
        summary = combined.get("summary") or ""

    # Audit log + alert
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                (cid, olt_id, onu_id, actor, "zero-touch-provision",
                 1 if ok else 0, (summary or "")[:500]))
    except Exception:
        pass
    _emit_alert(cid, olt_id=olt_id, onu_id=onu_id,
                kind="info", level=("info" if ok else "warning"),
                title=f"Zero-Touch Provision by {actor} "
                      f"({'OK' if ok else 'PARTIAL'})",
                message=(summary or "")[:250])
    return {"ok": ok, "summary": summary, "details": results}


@router.post("/api/admin/olt/olts/{olt_id}/tr069-cli-push-bulk")
def api_olt_tr069_push_bulk(request: Request, olt_id: int,
                             body: dict = Body(default={})):
    """_S57_ZTP_BULK_ASYNC_  Kick off a bulk TR-069 push as a background
    job. Returns {job_id} immediately so the UI can poll progress instead
    of waiting on the synchronous request (which would exceed nginx's
    60 s proxy_read_timeout when iterating many ONUs).

    Body (all optional):
      acs_url, acs_username, acs_password, inform_interval, only_online
    """
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    with engine.begin() as conn:
        olt_row = conn.exec_driver_sql(
            "SELECT id, host, vendor, cli_username, cli_password, telnet_port "
            "FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not olt_row:
        raise HTTPException(404, "OLT not found")
    olt_id_v, host, vendor, cli_user, cli_pw, tport = olt_row
    if (vendor or "").lower() not in {"vsol","vsol_epon","netlink",
            "netlink_epon","syrotech","syrotech_epon","cdata","cdata_epon"}:
        raise HTTPException(400,
            f"Bulk push only supports VSOL/Netlink/Syrotech/CDATA family. "
            f"(vendor={vendor})")
    s = _settings_for(cid)
    acs_url = (body.get("acs_url") or s.get("tr069_acs_url")
               or s.get("genieacs_url") or "").strip()
    if not acs_url or "127.0.0.1" in acs_url or "localhost" in acs_url:
        raise HTTPException(400,
            f"ACS URL '{acs_url}' is missing or loopback. Configure "
            "tr069_acs_url in Admin → System Config.")
    acs_user = body.get("acs_username") or s.get("genieacs_username") or "admin"
    acs_pw   = body.get("acs_password") or s.get("genieacs_password") or ""
    interval = int(body.get("inform_interval") or 300)
    only_online = bool(body.get("only_online", True))

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, pon_port_index, onu_index "
            "FROM onus WHERE olt_id=? AND company_id=? "
            + ("AND status='online' " if only_online else "")
            + "ORDER BY pon_port_index, onu_index",
            (olt_id_v, cid)).fetchall()

    job_id = _uuid_s57.uuid4().hex
    _bulk_job_create(job_id, cid, olt_id_v, actor, len(rows))

    def _worker():
        import olt_telnet_actions as _ota
        from olt_telnet_pool import telnet_session
        olt_dict = {"vendor": vendor, "host": host,
                    "cli_username": cli_user, "cli_password": cli_pw,
                    "telnet_port": tport or 23}
        _bulk_job_update(job_id, status="running")
        pushed_n = failed_n = 0
        try:
            current_pon = None
            with telnet_session(olt_dict) as ts:
                for r in rows:
                    onu_id_db, pon, onu_idx = r
                    if pon != current_pon:
                        ts.enter_pon(int(pon))
                        current_pon = pon
                    cmd = (f"onu {int(onu_idx)} pri tr069_mng enable acs_server "
                           f"url {acs_url} username {acs_user} password {acs_pw} "
                           f"certificate disable inform enable "
                           f"inform_interval {interval} reverse_connection "
                           f"username {acs_user} password {acs_pw}")
                    resp = ts.send(cmd, wait=1.2, iters=6) or ""
                    ts.send(f"onu {int(onu_idx)} pri save_config",
                             wait=0.8, iters=4)
                    ok = not _ota._err_in(resp) and "doesn't exist" not in resp
                    note = ("Time Out – auto-sync on next OAM"
                            if "Time Out" in resp else None)
                    item = {"onu_id": onu_id_db, "pon": pon,
                            "onu_idx": onu_idx, "ok": ok, "note": note}
                    if ok: pushed_n += 1
                    else:  failed_n += 1
                    _bulk_job_update(job_id,
                                      results_append=item,
                                      pushed=pushed_n, failed=failed_n)
                ts.exit_config()
            _bulk_job_update(job_id, status="done")
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                        "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                        (cid, olt_id_v, None, actor, "tr069-cli-push-bulk",
                         1 if failed_n == 0 else 0,
                         f"{pushed_n}/{len(rows)} pushed"[:500]))
            except Exception:
                pass
        except Exception as e:
            _bulk_job_update(job_id, status="error", error=str(e)[:300])

    _thr_s57.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "job_id": job_id, "total": len(rows),
            "status_url": f"/api/admin/olt/bulk-jobs/{job_id}"}


@router.get("/api/admin/olt/bulk-jobs/{job_id}")
def api_olt_bulk_job_status(request: Request, job_id: str):
    """Return the live status of a bulk-push job. Polled by the UI."""
    sc = _require_scope(request); cid = sc["company_id"]
    j = _bulk_job_get(job_id)
    if not j or (j.get("company_id") and j["company_id"] != cid):
        raise HTTPException(404, "Job not found")
    # Strip the per-ONU results when polling — they can grow big.
    return {
        "id": j["id"], "olt_id": j["olt_id"], "status": j["status"],
        "started": j["started"], "total": j["total"],
        "pushed": j["pushed"], "failed": j["failed"],
        "error": j.get("error"),
        "progress_pct": (round((j["pushed"] + j["failed"]) * 100 / max(1, j["total"]))),
        "last_results": j["results"][-10:],  # last 10
    }



@router.post("/api/admin/olt/onus/{onu_id}/factory-reset-and-provision")
def api_onu_factory_reset_and_provision(request: Request, onu_id: int,
                                          body: dict = Body(default={})):
    """_S57_ZTP_FR_  Factory-reset the ONU and auto-schedule a
    Zero-Touch Provision ~90s later (gives the ONU time to come back
    online with default config).

    Body:
      delay_seconds  – default 90, range 30..600
    """
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    delay = max(30, min(int(body.get("delay_seconds") or 90), 600))

    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id, n.pon_port_index, n.onu_index, o.vendor "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    olt_id, pon, onu_idx, vendor = row

    # 1. Immediate factory reset via OLT CLI
    import olt_telnet_actions as _ota
    with engine.begin() as conn:
        olt_row = conn.exec_driver_sql(
            "SELECT host, cli_username, cli_password, telnet_port "
            "FROM olts WHERE id=?", (olt_id,)).fetchone()
    host, cli_user, cli_pw, tport = olt_row
    olt_dict = {"vendor": vendor, "host": host,
                "cli_username": cli_user, "cli_password": cli_pw,
                "telnet_port": tport or 23}
    reset_res = _s56az_with_timeout(
        _ota.factory_reset_onu if hasattr(_ota, "factory_reset_onu") else
            (lambda **k: {"ok": False, "error": "factory_reset_onu not available"}),
        args=(olt_dict,),
        kwargs={"pon": pon, "onu_idx": onu_idx},
        timeout=15.0, fallback_msg="Factory-reset timed out")

    # 2. Schedule the ZTP via Dramatiq (if available) OR threading.Timer
    scheduled = False
    try:
        from task_queue.tasks import schedule_zero_touch_provision
        schedule_zero_touch_provision.send_with_options(
            args=(cid, onu_id, actor), delay=delay * 1000)
        scheduled = True
    except Exception:
        # Fallback: in-process Timer (loses on uvicorn restart).
        try:
            import threading, requests as _rq
            api_url = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
            def _run():
                try:
                    _rq.post(
                        f"{api_url}/api/admin/olt/onus/{onu_id}/zero-touch-provision",
                        cookies=dict(request.cookies), json={}, timeout=70)
                except Exception:
                    pass
            t = threading.Timer(delay, _run); t.daemon = True; t.start()
            scheduled = True
        except Exception:
            pass

    _emit_alert(cid, olt_id=olt_id, onu_id=onu_id,
                kind="info", level="info",
                title=f"Factory-Reset + ZTP queued by {actor}",
                message=f"ZTP will fire in {delay}s. Reset ok={reset_res.get('ok')}.")
    return {"ok": True, "reset": reset_res, "ztp_scheduled": scheduled,
            "ztp_delay_seconds": delay}


@router.get("/api/admin/olt/onus/{onu_id}/acs-diagnostic")
def api_onu_acs_diagnostic(request: Request, onu_id: int):
    """_S57_ZTP_DIAG_  Explain in plain English why an ONU may not be
    appearing in GenieACS. Checks:
       1. Is the ONU currently online on the OLT?
       2. Is there an active PPPoE accounting session?
       3. Does the OLT have a tr069_mng entry for this ONU's index?
       4. Does the GenieACS NBI have a device matching this ONU's MAC?
    """
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id, n.pon_port_index, n.onu_index, n.serial, "
            "       n.status, n.customer_id, "
            "       o.host, o.vendor, o.cli_username, o.cli_password, "
            "       o.telnet_port "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    (olt_id, pon, onu_idx, serial, status, customer_id,
     host, vendor, cli_user, cli_pw, tport) = row

    report = {"onu_id": onu_id, "serial": serial,
              "olt_status": status,
              "checks": [], "verdict": "", "next_steps": []}

    # Check 1 — ONU registered on OLT
    if status == "online":
        report["checks"].append({"name": "ONU online on OLT", "ok": True,
                                  "detail": f"Status: {status}"})
    else:
        report["checks"].append({"name": "ONU online on OLT", "ok": False,
                                  "detail": f"Status: {status} – ONU must be "
                                  "registered before TR-069 can work."})

    # Check 2 — active PPPoE session
    # _S60K_DIAG_FIX_A — customers table uses `customer_id` (text) as
    # the primary lookup, NOT `id`. Also fall back to ONU's wan_username
    # if customer's username isn't set.
    if customer_id:
        try:
            from sqlalchemy import text as _T
            with engine.connect() as conn:
                u = conn.execute(_T(
                    "SELECT username FROM customers "
                    "WHERE customer_id=:i OR id::text=:i"),
                    {"i": str(customer_id)}).fetchone()
                pppoe_user = u[0] if u and u[0] else None
                if not pppoe_user:
                    u2 = conn.execute(_T(
                        "SELECT wan_username FROM onus WHERE id=:i"),
                        {"i": onu_id}).fetchone()
                    pppoe_user = u2[0] if u2 and u2[0] else None
        except Exception:
            pppoe_user = None
        if pppoe_user:
            import sqlite3 as _sq
            try:
                rd = _sq.connect("/var/lib/freeradius/radacct.db", timeout=5)
                cur = rd.execute(
                    "SELECT framedipaddress, acctstarttime "
                    "FROM radacct WHERE username=? AND acctstoptime IS NULL "
                    "ORDER BY acctstarttime DESC LIMIT 1", (pppoe_user,))
                a = cur.fetchone(); rd.close()
                if a:
                    report["checks"].append({
                        "name": f"PPPoE session active ({pppoe_user})",
                        "ok": True,
                        "detail": f"IP {a[0]} since {a[1]}"})
                else:
                    report["checks"].append({
                        "name": f"PPPoE session active ({pppoe_user})",
                        "ok": False,
                        "detail": "No active radacct entry – ONU has no "
                                   "internet to reach the ACS URL."})
            except Exception as e:
                report["checks"].append({"name": "PPPoE session check",
                                          "ok": False, "detail": str(e)})
        else:
            report["checks"].append({"name": "PPPoE session check",
                                      "ok": False,
                                      "detail": "Customer has no username bound."})

    # Check 3 — OLT has tr069_mng entry
    # _S60K_DIAG_FIX_B2 — query `show running-config onu N` inside pon
    # mode directly. The previous `show running-config | include tr069`
    # at top level returns empty on V1600D-family (tr069_mng config is
    # only visible inside pon-config mode).
    def _query_onu_cfg():
        from olt_telnet_pool import telnet_session as _ts
        olt_d = {"vendor": vendor, "host": host,
                 "cli_username": cli_user, "cli_password": cli_pw,
                 "cli_port": tport or 23}
        with _ts(olt_d) as ts:
            ts.enter_pon(int(pon) if pon else 1)
            out2 = ts.send(f"show running-config onu {int(onu_idx)}",
                           wait=2.0, iters=10) or ""
            ts.exit_config()
        return {"ok": True, "output": out2}
    try:
        sh = _s56az_with_timeout(_query_onu_cfg, args=(),
                                  timeout=18.0,
                                  fallback_msg="OLT unreachable")
        out = sh.get("output", "") if isinstance(sh, dict) else ""
        line = ""
        for ln in out.splitlines():
            s = ln.strip().lower()
            if "tr069_mng" in s and "acs_server" in s:
                after = s.split("tr069_mng", 1)[1].strip().split()
                if after and after[0] != "disable":
                    line = ln.strip(); break
        if line:
            report["checks"].append({"name": "OLT has TR-069 config for ONU",
                                      "ok": True,
                                      "detail": line[:250]})
        else:
            report["checks"].append({"name": "OLT has TR-069 config for ONU",
                                      "ok": False,
                                      "detail": ("No `tr069_mng enable` entry"
                                      " parsed for this ONU. (raw tail: "
                                      + (out[-160:] or "<empty>") + ")")})
    except Exception as e:
        report["checks"].append({"name": "OLT TR-069 check",
                                  "ok": False, "detail": str(e)[:200]})

    # Check 4 — GenieACS NBI has device
    try:
        import requests as _rq, json as _json
        mac = (serial or "").upper().replace(":", "")
        q = '{"_deviceId._SerialNumber":"' + mac + '"}'
        nbi = "http://127.0.0.1:7557/devices/?query=" + q
        r = _rq.get(nbi, timeout=5)
        devs = r.json() if r.status_code == 200 else []
        if devs:
            report["checks"].append({
                "name": "Device present in GenieACS",
                "ok": True,
                "detail": f"id={devs[0].get('_id','?')}"})
        else:
            report["checks"].append({
                "name": "Device present in GenieACS",
                "ok": False,
                "detail": "ONU has never sent a CWMP Inform to GenieACS."})
    except Exception as e:
        report["checks"].append({"name": "GenieACS NBI check",
                                  "ok": False, "detail": str(e)})

    # Verdict + next steps
    failing = [c for c in report["checks"] if not c.get("ok")]
    if not failing:
        report["verdict"] = "All checks pass — ONU should be visible."
    else:
        report["verdict"] = f"{len(failing)} of {len(report['checks'])} checks failing"
        for c in failing:
            if "PPPoE" in c["name"]:
                report["next_steps"].append(
                    "ONU has no internet — verify PPPoE credentials match a "
                    "valid customer in your billing DB.")
            elif "OLT has" in c["name"]:
                report["next_steps"].append(
                    "Click 'Push TR-069 via OLT' on this ONU's action menu, "
                    "or use OLT-bulk to push to all online ONUs at once.")
            elif "GenieACS" in c["name"]:
                report["next_steps"].append(
                    "Even with internet + ACS URL pushed, the ONU may have "
                    "a 5-minute inform_interval. Wait 5-10 min, or use 'Reset' "
                    "in GenieACS UI to force an immediate Inform.")
            elif "online on OLT" in c["name"]:
                report["next_steps"].append(
                    "Power-cycle the ONU or check the fibre link. ONU must "
                    "be registered on the OLT before anything else works.")
    return report



@router.post("/api/admin/olt/onus/{onu_id}/genieacs-push")
def api_onu_genieacs_push(request: Request, onu_id: int,
                          body: dict = Body(default={})):
    sc = _require_scope(request)
    cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)   # _S40zα_
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial, wifi_ssid, wifi_password, wan_mode, "
            "wan_username, wan_password, wan_vlan FROM onus WHERE id=? "
            "AND company_id=?", (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    # Map to TR-069 standard parameters
    params = {}
    if row[1]:
        params["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID"] = row[1]
    if row[2]:
        params["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.KeyPassphrase"] = row[2]
    if row[3] == "pppoe" and row[4]:
        params["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username"] = row[4]
        if row[5]:
            params["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password"] = row[5]
    if not params:
        raise HTTPException(400, "Nothing to push — set Wi-Fi or WAN first.")
    res = _genieacs_provision(cid, device_serial=(row[0] or ""),
                              params=params)
    _emit_alert(cid, olt_id=None, onu_id=onu_id,
                kind="info", level="info",
                title=f"TR-069 push by {sc['actor']} ({'OK' if res.get('ok') else 'FAIL'})",
                message=res.get("error") or "queued via the ACS")
    return res


@router.post("/api/admin/olt/onus/{onu_id}/{action}")
def api_onu_action(request: Request, onu_id: int, action: str,
                   body: dict = Body(default={})):
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    if action not in ("reboot", "factory-reset", "activate", "wifi"):
        raise HTTPException(400, "Unknown action")
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.id, n.olt_id, o.name, o.vendor, n.serial, "
            "n.pon_port_index, n.onu_index, c.sub_lco_id, "
            "c.created_by_employee_id "
            "FROM onus n "
            "LEFT JOIN olts o ON o.id=n.olt_id "
            "LEFT JOIN customers c ON c.customer_id=n.customer_id "
            "AND c.company_id=n.company_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)
        ).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    # _S40zz_  Role scope — only admin can act on any ONU; sub-LCO/employee
    # can only act on ONUs that belong to their customer base.
    if sc["role"] == "sub_lco":
        sid = request.session.get("sub_lco_db_id")
        if not sid or row[7] != int(sid):
            raise HTTPException(403, "ONU not in your sub-LCO scope")
    elif sc["role"] == "employee":
        eid = request.session.get("employee_id")
        if not eid:
            raise HTTPException(403, "Not authenticated")
        # _S40zβ_  Employee can act on customer they created OR sub-LCO sibling
        if row[8] != int(eid):
            with engine.begin() as eng_z:
                er = eng_z.exec_driver_sql(
                    "SELECT sub_lco_id FROM employees WHERE id=? AND company_id=?",
                    (int(eid), cid)).fetchone()
            if not (er and er[0] and row[7] == int(er[0])):
                raise HTTPException(403, "ONU not in your employee scope")
    vendor = (row[3] or "mock").lower()

    # _S39R5K_ONU_ACTION — realistic mock vendor effect on the ONU row so
    # the demo experience reflects the action. Real vendor adapters (Nokia,
    # Optilink, VSOL, Syrotech, ZTE, Fiberhome) plug in here when wired.
    status_after = None
    human_msg = ""
    if vendor in ("mock", ""):
        if action == "reboot":
            # Mark offline briefly; the next mock-poller tick brings it back.
            status_after = "offline"
            human_msg = ("Reboot dispatched · ONU will go offline for "
                         "~30-60 seconds and reconnect automatically.")
        elif action == "factory-reset":
            # Wipe Wi-Fi/WAN config and mark offline.
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "UPDATE onus SET wifi_ssid='', wifi_password='', "
                    "wan_mode='', wan_username='', wan_password='', "
                    "wan_static_ip='', wan_netmask='', wan_gateway='', "
                    "wan_dns='', wan_vlan=NULL, wan_service_name='', "
                    "status='offline' WHERE id=? AND company_id=?",
                    (onu_id, cid))    # _S40zz_  was wifi_pass
            status_after = "offline"
            human_msg = ("Factory-reset complete · Wi-Fi & WAN config "
                         "wiped. ONU is now offline / unprovisioned.")
        elif action == "activate":
            status_after = "online"
            human_msg = ("Activation dispatched · ONU is now online and "
                         "ready to serve traffic.")
        elif action == "wifi":
            human_msg = "Wi-Fi config dispatched."
    else:
        # Real vendor — adapter wire-up TBD; continue to queue.
        human_msg = (f"Action queued for vendor adapter '{vendor}' — will "
                     "dispatch shortly.")

    if status_after:
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    "UPDATE onus SET status=? WHERE id=? AND company_id=?",
                    (status_after, onu_id, cid))
        except Exception:
            pass

    _emit_alert(
        cid, olt_id=row[1], onu_id=onu_id,
        kind="onu_recovered" if action == "activate" else "info",
        level="info",
        title=f"ONU {action.replace('-', ' ')} by {actor}",
        message=(f"OLT {row[2]} PON{row[5]} ONU{row[6]} "
                 f"({row[4]}) — {human_msg}"),
        meta={"action": action, "actor": actor, "body": body,
              "vendor": vendor, "status_after": status_after},
    )
    return {"ok": True, "action": action, "onu_id": onu_id,
            "status": status_after, "message": human_msg}


@router.get("/api/admin/olt/alerts")
def api_alerts(request: Request, level: Optional[str] = None,
               acked: Optional[int] = None, page: int = 1, per: int = 20):
    """_S40zy_  Default per-page reduced to 20 + paginated `total` returned."""
    sc = _require_scope(request); cid = sc["company_id"]
    page = max(int(page), 1)
    per = max(min(int(per), 200), 1)
    where = "WHERE a.company_id=?"
    args: List[Any] = [cid]
    if level:
        where += " AND a.level=?"; args.append(level)
    if acked is not None:
        where += " AND a.acked=?"; args.append(int(acked))
    with engine.begin() as conn:
        total = conn.exec_driver_sql(
            f"SELECT COUNT(*) FROM olt_alerts a {where}",
            tuple(args)).fetchone()[0]
        rows = conn.exec_driver_sql(
            "SELECT a.id, a.olt_id, o.name AS olt_name, a.onu_id, a.kind, "
            "a.level, a.title, a.message, a.meta_json, a.acked, a.acked_by, "
            f"a.acked_at, a.created_at FROM olt_alerts a "
            f"LEFT JOIN olts o ON o.id=a.olt_id {where} "
            f"ORDER BY a.id DESC LIMIT {per} OFFSET {(page - 1) * per}",
            tuple(args)).fetchall()
    keys = ("id", "olt_id", "olt_name", "onu_id", "kind", "level",
            "title", "message", "meta_json", "acked", "acked_by",
            "acked_at", "created_at")
    return {"ok": True,
             "items": [dict(zip(keys, r)) for r in rows],
             "total": int(total),
             "page": page, "per": per,
             "total_pages": (total + per - 1) // per if total else 1}


@router.patch("/api/admin/olt/alerts/{alert_id}/ack")
def api_ack_alert(request: Request, alert_id: int):
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE olt_alerts SET acked=1, acked_by=?, acked_at=datetime('now') "
            "WHERE id=? AND company_id=?",
            (actor, alert_id, cid)
        )
    return {"ok": True}


@router.post("/api/admin/olt/alerts/ack-all")
def api_ack_all(request: Request):
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    if sc["role"] != "admin":   # _S40zα_
        raise HTTPException(403, "Admin-only — bulk-ack affects all ONUs")
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE olt_alerts SET acked=1, acked_by=?, acked_at=datetime('now') "
            "WHERE company_id=? AND acked=0",
            (actor, cid)
        )
    return {"ok": True}




@router.get("/api/admin/olt/olts/{olt_id}/history")
def api_olt_history(request: Request, olt_id: int, hours: int = 24, points: int = 60):
    """Polled telemetry history for sparklines.

    Returns up to `points` of (ts, cpu_pct, avg_rx, online_onus) tuples
    over the last `hours` window. Used by the dashboard sparklines and
    the activity-mini-sparkline below the Reboot button.

    _S58K_HIST_PG_FIX_: cutoff is computed in Python so the query is
    portable across SQLite (ts is TEXT 'YYYY-MM-DD HH:MM:SS') and
    PostgreSQL (ts is text); we also accept rows whose ts is NULL or
    empty so freshly-inserted rows still show up (legacy SQLite default
    of CURRENT_TIMESTAMP did not survive the PG migration)."""
    import datetime as _dt
    sc = _require_scope(request); cid = sc["company_id"]
    hours  = max(1, min(168, int(hours)))
    points = max(5, min(240, int(points)))
    cutoff = (_dt.datetime.utcnow() - _dt.timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        own = conn.exec_driver_sql(
            "SELECT 1 FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not own:
            raise HTTPException(404, "OLT not found")
        rows = conn.exec_driver_sql(
            "SELECT ts, cpu_pct, avg_rx, online_onus, total_onus FROM olt_polls "
            "WHERE olt_id=? AND (ts IS NULL OR ts = '' OR ts >= ?) "
            "ORDER BY id ASC LIMIT ?",
            (olt_id, cutoff, points * 4)).fetchall()
    if len(rows) > points:
        step = len(rows) / points
        rows = [rows[int(i * step)] for i in range(points)]
    out = []
    for r in rows:
        out.append({
            "ts": r[0], "cpu_pct": float(r[1] or 0),
            "avg_rx": float(r[2] or 0) if r[2] is not None else None,
            "online_onus": int(r[3] or 0),
            "total_onus": int(r[4] or 0),
        })
    return {"ok": True, "olt_id": olt_id, "hours": hours, "items": out}



@router.get("/api/admin/olt/activity")
def api_activity(request: Request, limit: int = 25):
    """Role-scoped recent activity feed.
       admin: every alert in the company
       sub_lco: only alerts on ONUs assigned to this sub-lco's customers
       employee: only alerts where the actor matches the logged-in user
    """
    sc = _require_scope(request)
    cid, role, actor = sc["company_id"], sc["role"], sc["actor"]
    sess = request.session
    base = (
        "SELECT a.id, a.olt_id, o.name AS olt_name, a.onu_id, a.kind, "
        "a.level, a.title, a.message, a.acked, a.acked_by, a.created_at "
        "FROM olt_alerts a LEFT JOIN olts o ON o.id=a.olt_id "
        "WHERE a.company_id=?"
    )
    args = [cid]
    if role == "sub_lco":
        # Match ONUs whose linked customer belongs to this sub-lco.
        sub_lco_db_id = sess.get("sub_lco_db_id") or sess.get("sub_lco_id")
        base += (" AND a.onu_id IN (SELECT n.id FROM onus n JOIN customers c "
                 "ON c.customer_id=n.customer_id WHERE n.company_id=? "
                 "AND c.sub_lco_id=?)")
        args += [cid, sub_lco_db_id]
    elif role == "employee":
        # Look for actor name in either acked_by OR the title prefix
        # (we record actor in titles like "Wi-Fi config queued by <actor>").
        base += (" AND (a.acked_by=? OR a.title LIKE ? OR a.message LIKE ?)")
        like = f"%by {actor}%"
        args += [actor, like, like]
    base += " ORDER BY a.id DESC LIMIT ?"
    args.append(int(max(1, min(limit, 100))))
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(base, tuple(args)).fetchall()
    keys = ("id", "olt_id", "olt_name", "onu_id", "kind", "level",
            "title", "message", "acked", "acked_by", "created_at")
    items = [dict(zip(keys, r)) for r in rows]
    return {"ok": True, "role": role, "items": items}


@router.get("/api/admin/olt/onus/{onu_id}/customer-creds")
def api_onu_customer_creds(request: Request, onu_id: int):
    """Pulls the linked customer's portal credentials for one-click WAN
    auto-fill.  Tenant-scoped + role-scoped (admin/sub_lco/employee)."""
    sc = _require_scope(request)
    cid = sc["company_id"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT customer_id FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "ONU not found")
        cust_id = row[0]
        if not cust_id:
            return {"ok": False, "error": "No customer linked to this ONU. "
                    "Click on the ONU row's 'Customer' field to link first."}
        cust = conn.exec_driver_sql(
            "SELECT customer_id, customer_name, username, pppoe_password, "
            "       customer_phone, plan_id FROM customers "
            "WHERE customer_id=? AND company_id=?",
            (cust_id, cid)).fetchone()
    if not cust:
        return {"ok": False, "error": f"Customer {cust_id} not found in this company."}
    return {"ok": True, "customer": {
        "customer_id": cust[0], "name": cust[1] or "",
        "username": cust[2] or "", "password": cust[3] or "",
        "phone": cust[4] or "", "plan_id": cust[5],
    }}

# -----------------------------------------------------------------
# _s61F_ACS_LIVE_  On-demand "Refresh from ACS" - pulls live WiFi,
# WAN status, WAN IP, connected hosts, firmware version straight from
# GenieACS into a structured JSON AND persists the dynamic fields
# back into the `onus` table so the ONU detail page stays in sync
# without waiting for the 2-min watcher cycle.
# -----------------------------------------------------------------
@router.get("/api/admin/olt/onus/{onu_id}/acs-live")
def api_onu_acs_live(request: Request, onu_id: int, refresh: int = 0):
    """Return live ACS data for an ONU + persist dynamic fields to DB.

    Query params:
      refresh=1 -> trigger refreshObject on the device first (connection
                   request). Use sparingly - the device must be online.
    """
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.serial, n.mac, m.genieacs_device_id "
            "FROM onus n LEFT JOIN acs_device_mapping m "
            "  ON m.company_id=n.company_id AND "
            "     (m.onu_serial=n.serial OR m.onu_serial=n.mac OR "
            "      UPPER(m.onu_serial) = UPPER(REPLACE(n.mac,':',''))) "
            "WHERE n.id=? AND n.company_id=? LIMIT 1",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial, mac, device_id = row
    base = _genie_base_for(cid)
    if not base:
        return {"ok": False, "error": "genieacs_not_configured"}

    # Resolve the GenieACS _id. Prefer the cached mapping, else look up
    # by SerialNumber (handles devices the watcher has not matched yet).
    if not device_id:
        import json as _json
        for needle in (serial, mac):
            if not needle:
                continue
            n_clean = (needle or "").replace(":", "").upper()
            qs = {"query": _json.dumps({"$or": [
                    {"_deviceId._SerialNumber": needle},
                    {"_deviceId._SerialNumber": n_clean},
                  ]}), "projection": "_id"}
            try:
                arr = _genie_call(base, "/devices", "GET", params=qs) or []
                if arr:
                    device_id = arr[0].get("_id"); break
            except HTTPException:
                continue
    if not device_id:
        return {"ok": False, "error": "device_not_in_acs",
                "hint": "ONU has not informed to ACS yet."}

    # Optionally queue a refreshObject with connection-request so we
    # pull truly real-time values. Best-effort: device may be offline.
    if refresh:
        from urllib.parse import quote
        try:
            _genie_call(base,
                f"/devices/{quote(device_id, safe='')}/tasks",
                "POST",
                payload={"name": "refreshObject",
                         "objectName": "InternetGatewayDevice"},
                params={"connection_request": 1, "timeout": 5000})
        except Exception:
            pass

    # Fetch the device document with the parameter tree we need.
    import json as _json
    proj = ",".join([
        "_id", "_lastInform", "_lastBoot",
        "InternetGatewayDevice.DeviceInfo.Manufacturer",
        "InternetGatewayDevice.DeviceInfo.ModelName",
        "InternetGatewayDevice.DeviceInfo.SerialNumber",
        "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
        "InternetGatewayDevice.DeviceInfo.HardwareVersion",
        "InternetGatewayDevice.DeviceInfo.UpTime",
        "InternetGatewayDevice.ManagementServer.URL",
        "InternetGatewayDevice.ManagementServer.PeriodicInformInterval",
        "InternetGatewayDevice.ManagementServer.PeriodicInformEnable",
        "InternetGatewayDevice.LANDevice.1.WLANConfiguration",
        "InternetGatewayDevice.LANDevice.1.Hosts",
        "InternetGatewayDevice.WANDevice",
    ])
    qs = {"query": _json.dumps({"_id": device_id}), "projection": proj}
    arr = _genie_call(base, "/devices", "GET", params=qs) or []
    if not arr:
        return {"ok": False, "error": "device_not_in_acs"}
    d = arr[0]
    igd = d.get("InternetGatewayDevice") or {}

    def _val(node, *path):
        cur = node
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        if isinstance(cur, dict) and "_value" in cur:
            return cur.get("_value")
        return cur

    # Flatten WLAN
    wifi = []
    wl_root = ((igd.get("LANDevice", {}) or {}).get("1", {}) or {}).get("WLANConfiguration", {}) or {}
    for k, w in wl_root.items():
        if k.startswith("_") or not isinstance(w, dict):
            continue
        wifi.append({
            "index": k,
            "ssid": _val(w, "SSID"),
            "enable": _val(w, "Enable"),
            "channel": _val(w, "Channel"),
            "security": _val(w, "BeaconType"),
        })
    # Flatten Hosts
    hosts = []
    host_root = (((igd.get("LANDevice", {}) or {}).get("1", {}) or {})
                 .get("Hosts", {}) or {}).get("Host", {}) or {}
    for k, h in host_root.items():
        if k.startswith("_") or not isinstance(h, dict):
            continue
        hosts.append({
            "index": k,
            "mac": _val(h, "MACAddress"),
            "ip": _val(h, "IPAddress"),
            "hostname": _val(h, "HostName"),
            "active": _val(h, "Active"),
            "iface": _val(h, "InterfaceType"),
            "lease_remaining": _val(h, "LeaseTimeRemaining"),
        })
    # Flatten WAN PPP + IP
    wan = []
    for wk, wd in (igd.get("WANDevice", {}) or {}).items():
        if wk.startswith("_") or not isinstance(wd, dict):
            continue
        for ck, c in (wd.get("WANConnectionDevice", {}) or {}).items():
            if ck.startswith("_") or not isinstance(c, dict):
                continue
            for proto in ("WANPPPConnection", "WANIPConnection"):
                for pk, p in (c.get(proto, {}) or {}).items():
                    if pk.startswith("_") or not isinstance(p, dict):
                        continue
                    wan.append({
                        "proto": proto,
                        "path": f"WANDevice.{wk}.WANConnectionDevice.{ck}.{proto}.{pk}",
                        "username": _val(p, "Username"),
                        "status": _val(p, "ConnectionStatus"),
                        "external_ip": _val(p, "ExternalIPAddress"),
                        "uptime": _val(p, "Uptime"),
                        "enable": _val(p, "Enable"),
                    })

    di = igd.get("DeviceInfo", {}) or {}
    ms = igd.get("ManagementServer", {}) or {}
    info = {
        "device_id": d.get("_id"),
        "last_inform": d.get("_lastInform"),
        "last_boot": d.get("_lastBoot"),
        "manufacturer": _val(di, "Manufacturer"),
        "model": _val(di, "ModelName"),
        "serial": _val(di, "SerialNumber"),
        "software_version": _val(di, "SoftwareVersion"),
        "hardware_version": _val(di, "HardwareVersion"),
        "uptime": _val(di, "UpTime"),
        "acs_url": _val(ms, "URL"),
        "inform_interval": _val(ms, "PeriodicInformInterval"),
        "inform_enable": _val(ms, "PeriodicInformEnable"),
    }

    # Persist the most useful dynamic fields into `onus` so the
    # server-rendered ONU detail page stays in sync immediately.
    ssid24 = next((w["ssid"] for w in wifi if w["index"] == "1"), None)
    ssid5  = next((w["ssid"] for w in wifi if w["index"] == "5"), None)
    pppoe  = next((x for x in wan if x["proto"] == "WANPPPConnection"
                   and (x.get("status") or x.get("external_ip")
                        or x.get("username"))), None)
    ipc    = next((x for x in wan if x["proto"] == "WANIPConnection"
                   and (x.get("status") or x.get("external_ip"))), None)
    eff_ip     = (pppoe or {}).get("external_ip") or (ipc or {}).get("external_ip") or ""
    eff_status = (pppoe or {}).get("status")      or (ipc or {}).get("status")      or ""
    eff_user   = (pppoe or {}).get("username") or ""
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE onus SET "
                "  wifi_ssid        = COALESCE(NULLIF(?,''), wifi_ssid), "
                "  wifi_ssid_5g     = COALESCE(NULLIF(?,''), wifi_ssid_5g), "
                "  wan_username     = COALESCE(NULLIF(?,''), wan_username), "
                "  wan_status       = COALESCE(NULLIF(?,''), wan_status), "
                "  wan_ip           = COALESCE(NULLIF(?,''), wan_ip), "
                "  firmware_version = COALESCE(NULLIF(?,''), firmware_version), "
                "  last_acs_inform  = COALESCE(NULLIF(?,'')::timestamptz, last_acs_inform) "
                "WHERE id=? AND company_id=?",
                (ssid24 or "", ssid5 or "", eff_user or "",
                 str(eff_status or ""), eff_ip or "",
                 info.get("software_version") or "",
                 info.get("last_inform") or "",
                 onu_id, cid))
    except Exception as _e:
        print(f"[acs-live] persist failed onu_id={onu_id}: {_e}")

    return {
        "ok": True,
        "device": info,
        "wifi": wifi,
        "hosts": hosts,
        "wan": wan,
        "persisted": {
            "wifi_ssid": ssid24, "wifi_ssid_5g": ssid5,
            "wan_username": eff_user, "wan_status": eff_status,
            "wan_ip": eff_ip,
            "firmware_version": info.get("software_version"),
            "last_acs_inform": info.get("last_inform"),
        },
    }



# ── ACS TR-069 helper + provisioning endpoint ─────────────────────
def _genieacs_provision(cid: str, *, device_serial: str,
                        params: dict) -> dict:
    """POST a SetParameterValues task to ACS. Returns result dict.
       _S40zd_  Now resolves the real ACS device _id by querying
       SerialNumber first — works whether the device ID is the bare
       serial OR the more common OUI-ProductClass-Serial format."""
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    if not base:
        return {"ok": False,
                "error": "ACS URL not configured in System Config."}
    if not (device_serial or "").strip():
        return {"ok": False, "error": "ONU has no serial number."}
    try:
        import urllib.request, urllib.parse, base64, json as _j
        auth_hdr = None
        if s.get("genieacs_username"):
            tok = base64.b64encode(
                f"{s['genieacs_username']}:{s.get('genieacs_password') or ''}"
                .encode()).decode()
            auth_hdr = "Basic " + tok
        # 1. RESOLVE actual device _id by querying with SerialNumber.
        #     _S40zd2_: Admin DB often stores MAC in `serial`, but GenieACS
        #     reports vendor serial like 'F3242989DB2CFD6FF' which embeds
        #     the MAC without colons. We try:
        #        (a) strict _SerialNumber == device_serial
        #        (b) regex match on MAC stripped of separators (case-insensitive)
        device_id = None
        def _q(filter_obj):
            qq = _j.dumps(filter_obj)
            qurl = (base + "/devices?query=" + urllib.parse.quote(qq)
                    + "&projection=_id")
            qreq = urllib.request.Request(qurl)
            if auth_hdr: qreq.add_header("Authorization", auth_hdr)
            with urllib.request.urlopen(qreq, timeout=8) as r:
                arr2 = _j.loads(r.read(8192).decode("utf-8", "ignore") or "[]")
                if isinstance(arr2, list) and arr2:
                    return arr2[0].get("_id")
            return None
        try:
            device_id = _q({"_deviceId._SerialNumber": device_serial})
        except Exception:
            pass
        # MAC-normalized fallback. Strip separators & uppercase.
        if not device_id:
            mac_norm = ''.join(ch for ch in device_serial.upper()
                                if ch.isalnum())
            if mac_norm and len(mac_norm) >= 8:
                try:
                    device_id = _q({"_deviceId._SerialNumber":
                                    {"$regex": mac_norm, "$options": "i"}})
                except Exception:
                    pass
        # 2. If the lookup found nothing, the device hasn't checked in.
        if not device_id:
            return {"ok": False,
                    "error": (f"ONU '{device_serial}' is not registered "
                              "with the ACS yet. The CPE must connect at "
                              "least once via TR-069 (port 7547) before it "
                              "can be provisioned. Check the ONU's "
                              "InternetGatewayDevice.ManagementServer.URL "
                              "configuration.")}
        # 3. POST the SetParameterValues task to the real device _id.
        url = (base + "/devices/" + urllib.parse.quote(device_id)
               + "/tasks?connection_request")
        task = {"name": "setParameterValues",
                "parameterValues": [[k, v, "xsd:string"]
                                     for k, v in params.items()]}
        req = urllib.request.Request(
            url, data=_j.dumps(task).encode(),
            headers={"Content-Type": "application/json"})
        if auth_hdr: req.add_header("Authorization", auth_hdr)
        with urllib.request.urlopen(req, timeout=8) as r:
            return {"ok": True, "status": r.status,
                    "device_id": device_id,
                    "response": r.read(2048).decode("utf-8", "ignore")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _genieacs_auto_push(cid: str, onu_id: int, *, reason: str = "") -> dict:
    """_S40_AUTOPROV_ — Build the TR-069 parameter map from the current
    ONU + linked customer state, then push to ACS. Used by the
    auto-bridge after wifi/wan/customer changes. Always non-throwing —
    returns {"ok": True/False, ...}.

    Skips silently when:
      * ACS URL is not configured for the company
      * The auto-provision flag is OFF
      * The ONU has no serial (can't address it)
      * No actionable parameters can be built
    """
    try:
        s = _settings_for(cid)
        if not (s.get("genieacs_url") or "").strip():
            return {"ok": False, "skip": "genieacs_not_configured"}
        # default ON when column is NULL/missing
        if int(s.get("genieacs_auto_provision") or 0) == 0:
            # Explicitly OFF
            if "genieacs_auto_provision" in s and s["genieacs_auto_provision"] == 0:
                return {"ok": False, "skip": "auto_provision_disabled"}
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT serial, wifi_ssid, wifi_password, wan_mode, "
                "wan_username, wan_password, wan_vlan, customer_id, "
                # _S40c_DUALBAND_ extras
                "wifi_band_split, wifi_ssid_5g, wifi_password_5g, "
                "wifi_radio_24_enabled, wifi_radio_5_enabled, "
                "wifi_auto_24, wifi_auto_5, wifi_channel_24, wifi_channel_5, "
                "wifi_bw_24, wifi_bw_5, "
                # __PHASE19_2_LAN__ — LAN/DHCP columns
                "lan_ip, lan_netmask, dhcp_enabled, dhcp_start, dhcp_end "
                "FROM onus WHERE id=? AND company_id=?",
                (onu_id, cid)).fetchone()
        if not row:
            return {"ok": False, "skip": "onu_not_found"}
        (serial, wifi_ssid, wifi_pw, wan_mode, wan_user, wan_pw, wan_vlan,
         cust_id, band_split, ssid_5g, pw_5g, r24_en, r5_en,
         auto_24, auto_5, ch_24, ch_5, bw_24, bw_5,
         lan_ip, lan_mask, dhcp_en, dhcp_start, dhcp_end) = row
        if not (serial or "").strip():
            return {"ok": False, "skip": "onu_has_no_serial"}
        # _S60K_CUST_CREDS_PRIORITY — When linked to a customer, the
        # CUSTOMER's pppoe creds are the single source of truth (billing
        # system). The ONU row's wan_username/password may be a stale
        # placeholder from a previous test or template; always override
        # with the customer's live RADIUS creds when available.
        if cust_id:
            try:
                with engine.begin() as conn:
                    crow = conn.exec_driver_sql(
                        "SELECT username, pppoe_password FROM customers "
                        "WHERE customer_id=? AND company_id=?",
                        (cust_id, cid)).fetchone()
                if crow:
                    # PREFER customer creds over the ONU row's stale values.
                    if crow[0]:
                        wan_user = crow[0]
                    if crow[1]:
                        wan_pw = crow[1]
                    if not wan_mode:
                        wan_mode = "pppoe"
                    # Also persist these back to the ONU row so subsequent
                    # pushes have consistent state and audit logs are clean.
                    if crow[0] or crow[1]:
                        with engine.begin() as conn2:
                            conn2.exec_driver_sql(
                                "UPDATE onus SET "
                                "  wan_username=COALESCE(?, wan_username), "
                                "  wan_password=COALESCE(?, wan_password), "
                                "  wan_mode=COALESCE(NULLIF(wan_mode,''),'pppoe') "
                                "WHERE id=? AND company_id=?",
                                (crow[0] or None, crow[1] or None,
                                 onu_id, cid))
            except Exception:
                pass
        params = {}
        # ── 2.4 GHz radio (WLANConfiguration.1) ─────────────────────
        WL24 = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1"
        if wifi_ssid:
            params[f"{WL24}.SSID"] = wifi_ssid
        if wifi_pw:
            params[f"{WL24}.KeyPassphrase"] = wifi_pw
        if r24_en is not None:
            params[f"{WL24}.Enable"] = "true" if r24_en else "false"
        if auto_24 is not None:
            params[f"{WL24}.AutoChannelEnable"] = "true" if auto_24 else "false"
        if auto_24 == 0 and ch_24:
            params[f"{WL24}.Channel"] = str(int(ch_24))
        if bw_24 and bw_24.lower() != "auto":
            params[f"{WL24}.OperatingChannelBandwidth"] = bw_24
        # ── 5 GHz radio (WLANConfiguration.5) ───────────────────────
        # When band_split is 0/None, mirror the 2.4 GHz creds so a
        # single-band caller still configures a usable 5 GHz network.
        WL5 = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.5"
        # When band_split=0, mirror 2.4 GHz creds (ignore stale ssid_5g/pw_5g).
        # When band_split=1, prefer 5 GHz-specific creds, fall back to 2.4 if blank.
        if band_split:
            eff_ssid_5 = ssid_5g or wifi_ssid
            eff_pw_5   = pw_5g   or wifi_pw
        else:
            eff_ssid_5 = wifi_ssid
            eff_pw_5   = wifi_pw
        if eff_ssid_5:
            params[f"{WL5}.SSID"] = eff_ssid_5
        if eff_pw_5:
            params[f"{WL5}.KeyPassphrase"] = eff_pw_5
        if r5_en is not None:
            params[f"{WL5}.Enable"] = "true" if r5_en else "false"
        if auto_5 is not None:
            params[f"{WL5}.AutoChannelEnable"] = "true" if auto_5 else "false"
        if auto_5 == 0 and ch_5:
            params[f"{WL5}.Channel"] = str(int(ch_5))
        if bw_5 and bw_5.lower() != "auto":
            params[f"{WL5}.OperatingChannelBandwidth"] = bw_5
        # ── WAN / PPPoE ─────────────────────────────────────────────
        if (wan_mode or "").lower() == "pppoe" and wan_user:
            params["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username"] = wan_user
            if wan_pw:
                params["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password"] = wan_pw
        # __PHASE19_2_LAN__ — LAN gateway + DHCP server config
        LHC = "InternetGatewayDevice.LANDevice.1.LANHostConfigManagement"
        if lan_ip:
            params[f"{LHC}.IPInterface.1.IPInterfaceIPAddress"] = str(lan_ip)
        if lan_mask:
            params[f"{LHC}.IPInterface.1.IPInterfaceSubnetMask"] = str(lan_mask)
        if dhcp_en is not None:
            params[f"{LHC}.DHCPServerEnable"] = "true" if dhcp_en else "false"
        if dhcp_start:
            params[f"{LHC}.MinAddress"] = str(dhcp_start)
        if dhcp_end:
            params[f"{LHC}.MaxAddress"] = str(dhcp_end)
        if dhcp_en is not None and lan_mask:
            params[f"{LHC}.SubnetMask"] = str(lan_mask)
        if not params:
            _log_acs_push(cid, onu_id=onu_id, serial=serial,
                          customer_id=cust_id, reason=reason,
                          result={"ok": False, "skip": "no_params_to_push"},
                          params=params)
            return {"ok": False, "skip": "no_params_to_push"}
        # _S40zd4_wan_mode_autosync — push OLT-CLI WAN to mode tr069_internet
        # so the embedded TR-069 client can reach the ACS via the same
        # PPPoE session that carries user traffic. This is required because
        # NETLINK/VSOL firmware blocks CWMP egress when WAN service-mode is
        # plain `internet`. Runs only when:
        #   - vendor is VSOL/NETLINK (push_wan_pppoe_with_tr069 returns
        #     {"ok":False,"method":"unsupported"} otherwise)
        #   - WAN mode is pppoe with real creds
        #   - ONU is bound to a known OLT + PON
        wan_push = {"skip": "not_attempted"}
        try:
            if (wan_mode or "").lower() == "pppoe" and wan_user:
                with engine.begin() as _c:
                    _r = _c.exec_driver_sql(
                        "SELECT n.olt_id, n.pon_port_index, n.onu_index, "
                        "  o.vendor, o.host, o.cli_port, o.cli_username, "
                        "  o.cli_password "
                        "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
                        "WHERE n.id=? AND n.company_id=?",
                        (onu_id, cid)).fetchone()
                if _r and _r[0] and _r[1] is not None and _r[2] is not None:
                    olt_dict = {"id": _r[0], "vendor": _r[3] or "",
                                "host": _r[4], "cli_port": _r[5] or 23,
                                "cli_username": _r[6] or "admin",
                                "cli_password": _r[7] or ""}
                    # _S40zd4b_idempotent_check: only re-push WAN mode if the
                    # ONU isn't already in tr069_internet. This prevents
                    # clobbering an operator's manually-tuned config.
                    try:
                        from olt_telnet_pool import telnet_session as _ts
                        # _S60K_CREDS_DIFF_REPUSH — parse BOTH mode AND
                        # currently-configured PPPoE creds from the running
                        # config. Only skip the OLT-CLI push when ALL three
                        # match what we want; otherwise re-push to reconcile.
                        cur_mode = None
                        cur_user = None
                        cur_pwd = None
                        with _ts(olt_dict) as ts:
                            ts.enter_pon(int(_r[1]))
                            rc = ts.send(
                                f"show running-config onu {int(_r[2])}",
                                wait=2.0, iters=10) or ""
                            ts.exit_config()
                        # _s61G_FIX_S_SHADOW_ rename loop-local from `s`
                        # to `_ln` to avoid shadowing the settings dict.
                        for line in rc.splitlines():
                            _ln = line.strip()
                            if "wan_adv" in _ln and "route mode" in _ln:
                                parts = _ln.split()
                                if "mode" in parts:
                                    mi = parts.index("mode")
                                    if mi + 1 < len(parts):
                                        cur_mode = parts[mi + 1]
                            if ("wan_adv" in _ln and "pppoe" in _ln
                                    and " user " in _ln):
                                parts = _ln.split()
                                try:
                                    ui = parts.index("user")
                                    cur_user = parts[ui + 1]
                                except (ValueError, IndexError):
                                    pass
                                try:
                                    pi = parts.index("pwd")
                                    cur_pwd = parts[pi + 1]
                                except (ValueError, IndexError):
                                    pass
                        mode_ok = (cur_mode
                                   and "tr069" in cur_mode.lower())
                        creds_ok = (cur_user == wan_user
                                    and cur_pwd == (wan_pw or ""))
                        if mode_ok and creds_ok:
                            wan_push = {"skip":
                                f"already_synced(mode={cur_mode},"
                                f"user_match=1)",
                                "ok": True}
                        else:
                            from olt_telnet_actions import (
                                push_wan_pppoe_with_tr069 as _push_wan,
                            )
                            wan_push = _push_wan(
                                olt_dict, int(_r[1]), int(_r[2]),
                                pppoe_user=wan_user,
                                pppoe_password=wan_pw or "",
                            )
                            # Annotate why we pushed (debug).
                            if isinstance(wan_push, dict):
                                wan_push["reason"] = (
                                    f"reconcile(mode={cur_mode},"
                                    f"cur_user={cur_user},"
                                    f"want_user={wan_user})")
                    except Exception as _e:
                        wan_push = {"ok": False, "error": str(_e)[:200]}
                else:
                    wan_push = {"skip": "onu_not_bound_to_olt"}
            else:
                wan_push = {"skip": "no_pppoe_creds"}
        except Exception as _e:
            wan_push = {"ok": False, "error": "wan_push_exception: "
                        + str(_e)[:200]}

        # ───────────────────────────────────────────────────────────
        # _s61G_ACS_BOOTSTRAP_  Auto-push ACS URL via OLT CLI when
        # the ONU has never informed to GenieACS yet. This solves
        # the chicken-and-egg bootstrap problem for EPON/GPON ONUs
        # on VSOL/NETLINK firmware: TR-069 pushes via NBI need the
        # device to be informed first, but the device can't inform
        # until it knows the ACS URL — and the ACS URL must be
        # delivered OUT-OF-BAND via OLT OMCI/OAM.  We push it once
        # per ONU; the OLT CLI is idempotent (running-config check
        # prevents redundant pushes on repeat customer-link events).
        # ───────────────────────────────────────────────────────────
        acs_bootstrap = {"skip": "not_attempted"}
        try:
            # Only attempt when:
            #   * ONU has never informed (last_acs_inform IS NULL) OR
            #     genieacs_device_id mapping is missing
            #   * Vendor is VSOL/NETLINK family (push_tr069_acs supports)
            #   * Operator has configured tr069_acs_url (CPE-reachable)
            tr069_url = (s.get("tr069_acs_url") or "").strip()
            if tr069_url and "127.0.0.1" not in tr069_url and "localhost" not in tr069_url:
                with engine.begin() as _c:
                    _b = _c.exec_driver_sql(
                        "SELECT n.last_acs_inform, n.olt_id, "
                        "  n.pon_port_index, n.onu_index, o.vendor, o.host, "
                        "  o.cli_port, o.cli_username, o.cli_password, "
                        "  m.genieacs_device_id "
                        "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
                        "  LEFT JOIN acs_device_mapping m ON "
                        "       m.company_id=n.company_id AND "
                        "       (m.onu_serial=n.serial OR m.onu_serial=n.mac) "
                        "WHERE n.id=? AND n.company_id=? LIMIT 1",
                        (onu_id, cid)).fetchone()
                if _b and _b[1] and _b[2] is not None and _b[3] is not None:
                    last_inform, _olt_id, _pon, _idx, _vendor, _host,                         _cli_port, _cli_user, _cli_pw, _gid = _b
                    never_informed = not last_inform and not _gid
                    olt_dict_b = {"id": _olt_id, "vendor": _vendor or "",
                                  "host": _host, "cli_port": _cli_port or 23,
                                  "cli_username": _cli_user or "admin",
                                  "cli_password": _cli_pw or ""}
                    # _s61G_idempotent_check_  inspect running-config to
                    # see whether tr069_mng is already enabled. If yes,
                    # skip the push entirely — saves a CLI roundtrip on
                    # every customer mutation.
                    tr069_already = False
                    if not never_informed:
                        # Already informed -> CLI bootstrap not needed.
                        tr069_already = True
                    else:
                        try:
                            from olt_telnet_pool import telnet_session as _tsB
                            with _tsB(olt_dict_b) as ts:
                                ts.enter_pon(int(_pon))
                                rcB = ts.send(
                                    f"show running-config onu {int(_idx)}",
                                    wait=2.0, iters=10) or ""
                                ts.exit_config()
                            for ln in rcB.splitlines():
                                lo = ln.strip().lower()
                                if ("tr069_mng" in lo and
                                    "enable" in lo and
                                    "acs_server" in lo):
                                    tr069_already = True
                                    break
                        except Exception as _eB:
                            acs_bootstrap = {"ok": False,
                                "error": "rc_read_failed: " + str(_eB)[:200]}
                    if tr069_already:
                        acs_bootstrap = {"skip": "tr069_mng_already_set",
                                         "ok": True}
                    else:
                        try:
                            from olt_telnet_actions import (
                                push_tr069_acs as _push_acs,
                            )
                            acs_bootstrap = _push_acs(
                                olt_dict_b, int(_pon), int(_idx),
                                acs_url=tr069_url,
                                acs_username=(s.get("genieacs_username")
                                              or "admin"),
                                acs_password=(s.get("genieacs_password")
                                              or ""),
                                inform_interval=300,
                            )
                            # Optionally reboot the ONU so the new ACS
                            # URL is picked up on next CWMP cycle. Use
                            # gentle reboot only when not_online flag
                            # is absent (means config went through OAM).
                            if acs_bootstrap.get("ok") and not acs_bootstrap.get("note"):
                                try:
                                    from olt_telnet_actions import (
                                        reboot as _reboot_onu,
                                    )
                                    _reboot_onu(olt_dict_b,
                                                int(_pon), int(_idx))
                                    acs_bootstrap["reboot_queued"] = True
                                except Exception as _eR:
                                    acs_bootstrap["reboot_error"] = str(_eR)[:200]
                        except Exception as _eP:
                            acs_bootstrap = {"ok": False,
                                "error": "push_tr069_acs_exception: "
                                         + str(_eP)[:200]}
                else:
                    acs_bootstrap = {"skip": "onu_not_bound_to_olt"}
            else:
                acs_bootstrap = {"skip": "no_tr069_acs_url_configured"
                                 if not tr069_url
                                 else "tr069_acs_url_is_internal_only"}
        except Exception as _e:
            acs_bootstrap = {"ok": False, "error":
                             "acs_bootstrap_exception: " + str(_e)[:200]}

        res = _genieacs_provision(cid, device_serial=serial, params=params)
        _log_acs_push(cid, onu_id=onu_id, serial=serial,
                      customer_id=cust_id, reason=reason,
                      result=res, params=params)
        # Emit alert so it's visible in the admin OLT/alerts panel
        try:
            _emit_alert(cid, olt_id=None, onu_id=onu_id,
                        kind="info", level="info",
                        title=f"Auto-provision (TR-069): {'OK' if res.get('ok') else 'FAIL'}",
                        message=(reason + " — " if reason else "")
                                + (res.get("error") or f"queued {len(params)} param(s)"))
        except Exception:
            pass
        return res
    except Exception as e:
        try:
            _log_acs_push(cid, onu_id=onu_id, serial=None,
                          customer_id=None, reason=reason,
                          result={"ok": False, "error": str(e)},
                          params=None)
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def _log_acs_push(cid: str, *, onu_id, serial, customer_id, reason,
                  result: dict, params):
    """_S40b_ — persist a row in acs_push_log. Best-effort, swallow errors."""
    try:
        import json as _json
        ok_v = 1 if result.get("ok") else 0
        skip_v = result.get("skip") or ""
        err_v = result.get("error") or ""
        pjson = _json.dumps(params or {}, default=str) if params else None
        with engine.begin() as _c:
            _c.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, onu_id, onu_serial, "
                "customer_id, reason, ok, skip, error, params_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, onu_id, serial or "", customer_id or "",
                 reason or "", ok_v, skip_v, err_v, pjson))
    except Exception:
        pass


@router.get("/api/admin/olt/settings")
def api_get_settings(request: Request):
    sc = _require_scope(request); cid = sc["company_id"]
    return {"ok": True, "settings": _settings_for(cid)}


@router.patch("/api/admin/olt/settings")
def api_patch_settings_genieacs(request: Request, body: dict = Body(...)):
    """_S46A_ Lightweight PATCH that only touches ACS columns —
    used by the System Config UI's ACS save button so the user
    doesn't have to fill all alert-threshold fields too."""
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT OR IGNORE INTO olt_settings (company_id) VALUES (?)",
            (cid,))
        conn.exec_driver_sql(
            "UPDATE olt_settings SET "
            "genieacs_url=?, genieacs_username=?, "
            "genieacs_password=COALESCE(NULLIF(?,''), genieacs_password), "
            "genieacs_auto_provision=?, "
            "tr069_acs_url=?, "
            "updated_at=datetime('now') WHERE company_id=?",
            ((body.get("genieacs_url") or "").strip(),
             (body.get("genieacs_username") or "").strip(),
             (body.get("genieacs_password") or "").strip(),
             int(body.get("genieacs_auto_provision") or 0),
             (body.get("tr069_acs_url") or "").strip(), cid))
    return {"ok": True}


@router.put("/api/admin/olt/settings")
def api_put_settings(request: Request, body: SettingsIn):
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM olt_settings WHERE company_id=?", (cid,)
        ).fetchone()
        if exists:
            conn.exec_driver_sql(
                "UPDATE olt_settings SET rx_warn_dbm=?, rx_crit_dbm=?, "
                "fiber_cut_pct=?, fiber_cut_min=?, poll_interval=?, "
                "wa_enabled=?, wa_target=?, email_enabled=?, email_target=?, "
                "genieacs_url=?, genieacs_username=?, "
                "genieacs_password=COALESCE(NULLIF(?,''), genieacs_password), "
                "genieacs_auto_provision=?, "
                "updated_at=datetime('now') WHERE company_id=?",
                (body.rx_warn_dbm, body.rx_crit_dbm, body.fiber_cut_pct,
                 body.fiber_cut_min, body.poll_interval,
                 int(body.wa_enabled), body.wa_target or "",
                 int(body.email_enabled), body.email_target or "",
                 body.genieacs_url or "", body.genieacs_username or "",
                 body.genieacs_password or "",
                 int(body.genieacs_auto_provision or 0), cid),
            )
        else:
            conn.exec_driver_sql(
                "INSERT INTO olt_settings (company_id, rx_warn_dbm, "
                "rx_crit_dbm, fiber_cut_pct, fiber_cut_min, poll_interval, "
                "wa_enabled, wa_target, email_enabled, email_target) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, body.rx_warn_dbm, body.rx_crit_dbm, body.fiber_cut_pct,
                 body.fiber_cut_min, body.poll_interval,
                 int(body.wa_enabled), body.wa_target or "",
                 int(body.email_enabled), body.email_target or ""),
            )
    return {"ok": True}

# ──────────────────────────────────────────────────────────────────────────
# _S39R5Q — TR-069 / ACS admin web UI
# ──────────────────────────────────────────────────────────────────────────
def _genie_base_for(cid: str) -> Optional[str]:
    """Return GenieACS NBI base URL for the tenant. _S58BA_NBI_PORT_GUARD_:
    GenieACS exposes 4 ports — 7547 (CWMP/TR-069), 7557 (NBI REST),
    7567 (FS) and 7577 (UI WS). Operators routinely paste 7547 (the
    "ACS URL" their ONUs talk to) into this setting which leaves the
    REST API unreachable. We auto-rewrite any of the wrong ports to
    7557 so the admin portal + mobile API always reach the NBI."""
    s = _settings_for(cid)
    u = (s.get("genieacs_url") or "").rstrip("/")
    if not u:
        return None
    import re as _re
    fixed = _re.sub(r":75(47|67|77)(?=/|$)", ":7557", u)
    if fixed != u:
        # Persist the correction so the operator sees the right value
        # next time they open Settings — and so other call-sites benefit.
        try:
            from database import engine as _eng
            with _eng.begin() as _cn:
                _cn.exec_driver_sql(
                    "UPDATE olt_settings SET genieacs_url=? "
                    "WHERE company_id=?", (fixed, cid))
            print(f"[genie_base_for] auto-fixed {cid}: {u} -> {fixed}")
        except Exception as _e:
            print(f"[genie_base_for] auto-fix persist failed: {_e}")
        u = fixed
    return u


def _genie_call(base: str, path: str, method: str = "GET",
                payload: dict = None, params: dict = None):
    import urllib.request, urllib.parse, json as _json
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = None
    if payload is not None:
        body = _json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            txt = resp.read().decode() or "null"
            try:
                return _json.loads(txt)
            except Exception:
                return {"raw": txt}
    except Exception as e:
        raise HTTPException(502, f"genieacs_error: {e}")


def _genie_flatten(devs: list) -> list:
    items = []
    for d in devs or []:
        dev = (d.get("_deviceId") or {})
        igd = (d.get("InternetGatewayDevice") or {})
        try:
            ssid = (igd.get("LANDevice", {}).get("1", {})
                    .get("WLANConfiguration", {}).get("1", {})
                    .get("SSID", {}).get("_value"))
        except Exception:
            ssid = None
        try:
            wan_ip = (igd.get("WANDevice", {}).get("1", {})
                      .get("WANConnectionDevice", {}).get("1", {})
                      .get("WANPPPConnection", {}).get("1", {})
                      .get("ExternalIPAddress", {}).get("_value"))
        except Exception:
            wan_ip = None
        try:
            sw = igd.get("DeviceInfo", {}).get("SoftwareVersion", {}).get("_value")
        except Exception:
            sw = None
        items.append({
            "id": d.get("_id"),
            "serial": dev.get("_SerialNumber"),
            "oui": dev.get("_OUI"),
            "product_class": dev.get("_ProductClass"),
            "last_inform": d.get("_lastInform"),
            "software_version": sw,
            "wifi_ssid": ssid,
            "wan_ip": wan_ip,
        })
    return items


@router.get("/admin/genieacs", response_class=HTMLResponse)
def page_genieacs(request: Request):
    cid, _ = _require_admin(request)
    sc = _require_scope(request)
    ctx = _portal_context(request, sc, "olt_genieacs")
    base = _genie_base_for(cid)
    ctx["genieacs_url"] = base or ""
    s = _settings_for(cid)
    ctx["settings"] = s
    # _S40_AUTOPROV_ — expose flag so the page can toggle it
    ctx["auto_provision"] = int(s.get("genieacs_auto_provision") or 0) == 1
    return templates.TemplateResponse("admin_genieacs.html", ctx)


@router.post("/api/admin/genieacs/auto-provision")
def api_genie_set_autoprov(request: Request, body: dict = Body(default={})):
    """_S40_AUTOPROV_ — toggle the auto-provision flag without a full
    OLT-settings round-trip."""
    cid, _ = _require_admin(request)
    val = 1 if bool(body.get("enabled")) else 0
    with engine.begin() as conn:
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM olt_settings WHERE company_id=?", (cid,)
        ).fetchone()
        if exists:
            conn.exec_driver_sql(
                "UPDATE olt_settings SET genieacs_auto_provision=?, "
                "updated_at=datetime('now') WHERE company_id=?",
                (val, cid))
        else:
            conn.exec_driver_sql(
                "INSERT INTO olt_settings (company_id, genieacs_auto_provision) "
                "VALUES (?, ?)", (cid, val))
    return {"ok": True, "enabled": bool(val)}


@router.get("/api/admin/genieacs/recent-pushes")
def api_genie_recent_pushes(request: Request, limit: int = 10):
    """_S40b_ — last N TR-069 auto-pushes for this company (for the
    ACS page widget). Most recent first."""
    cid, _ = _require_admin(request)
    limit = max(1, min(int(limit or 10), 100))
    items = []
    try:
        with engine.begin() as conn:
            rows = conn.exec_driver_sql(
                "SELECT l.id, l.onu_id, l.onu_serial, l.customer_id, "
                "l.reason, l.ok, l.skip, l.error, l.created_at, "
                "n.wifi_ssid, n.wan_mode "
                "FROM acs_push_log l "
                "LEFT JOIN onus n ON n.id = l.onu_id AND n.company_id = l.company_id "
                "WHERE l.company_id=? "
                "ORDER BY l.id DESC LIMIT ?",
                (cid, limit)).fetchall()
        for r in rows:
            items.append({
                "id": r[0], "onu_id": r[1], "serial": r[2] or "",
                "customer_id": r[3] or "", "reason": r[4] or "",
                "ok": bool(r[5]), "skip": r[6] or "", "error": r[7] or "",
                "created_at": r[8] or "",
                "wifi_ssid": r[9] or "", "wan_mode": r[10] or "",
            })
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}
    return {"ok": True, "items": items}


class BulkRepushIn(BaseModel):
    onu_ids: Optional[List[int]] = None  # if None/empty -> all eligible
    olt_id: Optional[int] = None         # filter by OLT
    only_linked: bool = True             # only ONUs bound to a customer





# ═══ _S40zy_  DELETE an ONU after factory-reset (frees serial / cleanup) ════
@router.delete("/api/admin/olt/onus/{onu_id}")
def api_delete_onu(request: Request, onu_id: int, force: int = 0):
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.id, n.olt_id, n.serial, n.status, n.customer_id, "
            "       c.sub_lco_id, c.created_by_employee_id "
            "FROM onus n "
            "LEFT JOIN customers c ON c.customer_id=n.customer_id "
            "AND c.company_id=n.company_id "
            "WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "ONU not found")
        # _S40zz_  Role scope — only admin can delete any ONU; sub-LCO /
        # employee can delete only their own customer's ONU.
        if sc["role"] == "sub_lco":
            sid = request.session.get("sub_lco_db_id")
            if not sid or row[5] != int(sid):
                raise HTTPException(403, "ONU not in your sub-LCO scope")
        elif sc["role"] == "employee":
            eid = request.session.get("employee_id")
            if not eid:
                raise HTTPException(403, "Not authenticated")
            # _S40zβ_  Employee can delete own OR sub-LCO sibling's ONU
            if row[6] != int(eid):
                with engine.begin() as eng_z:
                    er = eng_z.exec_driver_sql(
                        "SELECT sub_lco_id FROM employees WHERE id=? AND company_id=?",
                        (int(eid), cid)).fetchone()
                if not (er and er[0] and row[5] == int(er[0])):
                    raise HTTPException(403, "ONU not in your employee scope")
        # _s61L_FORCE_DELETE_  Safety guard - ONU must be offline /
        # unprovisioned. Override via `?force=1` for flapping or stuck
        # ONUs (e.g., devices in continuous register/deregister cycles
        # where the poller catches "online" between flaps).
        status = (row[3] or "").lower()
        if status == "online" and not force:
            raise HTTPException(400, "Cannot delete an ONLINE ONU. "
                "Factory-reset or unprovision it first - or call with "
                "?force=1 to override (e.g., for flapping/stuck ONUs).")
        conn.exec_driver_sql(
            "DELETE FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid))
        # Best-effort: drop any in-flight alerts referencing this ONU
        try:
            conn.exec_driver_sql(
                "DELETE FROM olt_alerts WHERE onu_id=? AND company_id=?",
                (onu_id, cid))
        except Exception:
            pass
        # _S40zγ_  Cascade — remove the network_hardware pin tied to this
        # ONU so the map doesn't keep an orphan pin around.
        try:
            conn.exec_driver_sql(
                "DELETE FROM network_hardware "
                "WHERE company_id=? AND kind='onu' AND ref_onu_id=?",
                (cid, onu_id))
        except Exception:
            pass
        # Audit
        try:
            conn.exec_driver_sql(
                "INSERT INTO audit_log (company_id, actor, action, entity, "
                "entity_id, meta) VALUES (?,?,?,?,?,?)",
                (cid, actor,
                 "delete_force" if (force and status == "online") else "delete",
                 "onu", onu_id,
                 f"serial={row[2]} olt_id={row[1]} status_at_delete={status}"))
        except Exception:
            pass
    return {"ok": True, "id": onu_id, "message": "ONU deleted."}


@router.post("/api/admin/olt/onus/bulk-repush")
def api_onu_bulk_repush(request: Request, body: BulkRepushIn):
    """_S40b_ — Force a TR-069 re-push for many ONUs at once. Useful
    after firmware/OLT swap or after a long ACS outage."""
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]
    s = _settings_for(cid)
    if not (s.get("genieacs_url") or "").strip():
        raise HTTPException(400, "ACS URL not configured")
    where = ["company_id = ?"]
    args = [cid]
    if body.onu_ids:
        # parameterised IN-list
        ph = ",".join(["?"] * len(body.onu_ids))
        where.append(f"id IN ({ph})")
        args.extend(body.onu_ids)
    if body.olt_id:
        where.append("olt_id = ?")
        args.append(body.olt_id)
    if body.only_linked:
        where.append("customer_id IS NOT NULL AND customer_id <> ''")
    where.append("serial IS NOT NULL AND serial <> ''")
    sql = f"SELECT id FROM onus WHERE {' AND '.join(where)} ORDER BY id ASC"
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(sql, tuple(args)).fetchall()
    total = len(rows)
    if total == 0:
        return {"ok": True, "total": 0, "ok_count": 0,
                "fail_count": 0, "results": [],
                "message": "No matching ONUs to push."}
    results = []
    ok_n = fail_n = 0
    # Cap per call to avoid blocking too long synchronously.
    cap = 200
    for (oid,) in rows[:cap]:
        r = _genieacs_auto_push(cid, oid, reason=f"bulk-repush by {sc['actor']}")
        is_ok = bool(r.get("ok"))
        if is_ok: ok_n += 1
        else: fail_n += 1
        results.append({"onu_id": oid, "ok": is_ok,
                        "skip": r.get("skip") or "",
                        "error": r.get("error") or ""})
    return {"ok": True, "total": total, "processed": len(results),
            "ok_count": ok_n, "fail_count": fail_n,
            "capped": total > cap, "results": results}


@router.get("/api/admin/genieacs/devices")
def api_genie_devices(request: Request, q: str = "", limit: int = 100):
    cid, _ = _require_admin(request)
    base = _genie_base_for(cid)
    if not base:
        return {"ok": False, "error": "genieacs_not_configured", "items": []}
    import json as _json
    qobj = {}
    if q:
        qobj = {"$or": [
            {"_deviceId._SerialNumber": {"$regex": q, "$options": "i"}},
            {"_deviceId._ProductClass": {"$regex": q, "$options": "i"}},
        ]}
    qs = {
        "query": _json.dumps(qobj),
        "limit": limit,
        "projection": ("_id,_deviceId,_lastInform,"
                        "InternetGatewayDevice.DeviceInfo.SoftwareVersion,"
                        "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID,"
                        "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ExternalIPAddress"),
    }
    data = _genie_call(base, "/devices", "GET", params=qs) or []
    return {"ok": True, "items": _genie_flatten(data if isinstance(data, list) else [])}


@router.post("/api/admin/genieacs/device/{device_id:path}/task")
def api_genie_task(device_id: str, request: Request, body: dict = Body(...)):
    cid, _ = _require_admin(request)
    base = _genie_base_for(cid)
    if not base:
        raise HTTPException(400, "genieacs_not_configured")
    kind = body.get("kind") or ""
    payload = {"name": kind}
    if kind == "refreshObject":
        payload["objectName"] = body.get("objectName", "")
    elif kind == "setParameterValues":
        payload["parameterValues"] = body.get("parameterValues") or []
    elif kind == "addObject":
        payload["objectName"] = body.get("objectName", "")
    from urllib.parse import quote
    path = f"/devices/{quote(device_id, safe='')}/tasks"
    r = _genie_call(base, path, "POST", payload=payload,
                    params={"connection_request": 1})
    return {"ok": True, "task": r}


@router.post("/api/admin/genieacs/device/{device_id:path}/wifi")
def api_genie_wifi(device_id: str, request: Request, body: dict = Body(...)):
    cid, _ = _require_admin(request)
    base = _genie_base_for(cid)
    if not base:
        raise HTTPException(400, "genieacs_not_configured")
    ssid = body.get("ssid")
    pw = body.get("password")
    if not ssid and not pw:
        raise HTTPException(400, "ssid_or_password_required")
    pvs = []
    if ssid:
        pvs.append(["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID",
                    ssid, "xsd:string"])
    if pw:
        pvs.extend([
            ["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.PreSharedKey.1.PreSharedKey",
             pw, "xsd:string"],
            ["InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.KeyPassphrase",
             pw, "xsd:string"],
        ])
    from urllib.parse import quote
    r = _genie_call(base, f"/devices/{quote(device_id, safe='')}/tasks",
                    "POST",
                    payload={"name": "setParameterValues",
                             "parameterValues": pvs},
                    params={"connection_request": 1})
    return {"ok": True, "task": r}


# ============================================================================
# _v4727_  WireGuard provisioning — per-OLT peer keys + client .conf
# ============================================================================
import subprocess, ipaddress, re  # _v4727_  std-lib only

WG_SERVER_PUBKEY   = 'rPNlggku9UuXJ5TlxcyyLW2/vSxOAJniJ8zQuKjw+g8='
WG_SERVER_ENDPOINT = '185.199.53.93:51820'
# === s56AK: helper to sync OLT-via-NAS routes ===
def _s56AK_sync_via_nas(company_id, parent_nas_id):
    """Call sa_wg_tunnel.sync_via_nas_olt_routes — silent on failure."""
    try:
        if not parent_nas_id: return
        import sys as _s
        _RD = "/opt/ispbilling/superadmin-portal/routes"
        if _RD not in _s.path: _s.path.insert(0, _RD)
        import sa_wg_tunnel as _wg
        if hasattr(_wg, "sync_via_nas_olt_routes"):
            r = _wg.sync_via_nas_olt_routes(str(company_id), int(parent_nas_id))
            print(f"[s56AK sync via_nas] cid={company_id} nas={parent_nas_id} -> {r}")
    except Exception as e:
        print(f"[s56AK sync via_nas FAIL] {e}")


# UDP/443 fallback — server uses iptables DNAT 443->51820 (wg-port-fallback.service)
WG_SERVER_ENDPOINT_FALLBACK = '185.199.53.93:443'
WG_DNS             = '1.1.1.1,8.8.8.8'
WG_CONF_PATH       = "/etc/wireguard/wg0.conf"
WG_PEER_BEGIN      = "# === BEGIN PEERS"
WG_PEER_END        = "# === END PEERS"

# s39: multi-tenant isolation. Each tenant gets a /27 slice (30 usable
# IPs, of which we permit a max of 20 OLTs per tenant — the slice gives
# 10 IPs of headroom for future expansion / accidental key rotation).
# Slices are allocated out of the master 10.50.0.0/16 network. The
# first /27 (10.50.0.0/27) is reserved for the server gateway 10.50.0.1.
# Slot budget: 65536 / 32 = 2048 tenant slices, 20 OLTs each → 40,960
# total OLT capacity platform-wide.
WG_MASTER_NETWORK         = "10.50.0.0/16"
WG_TENANT_PREFIX_BITS     = 27    # /27 per tenant => 30 usable IPs
WG_MAX_OLTS_PER_TENANT    = 20    # hard cap on VPN OLTs per admin
WG_RESERVED_SLICES        = ["10.50.0.0/27"]   # holds server gateway 10.50.0.1


def _ensure_wg_tenant_slices_table() -> None:
    """Idempotent migration for the per-tenant slice mapping table."""
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS wg_tenant_slices (
              company_id   TEXT PRIMARY KEY,
              slice_cidr   TEXT NOT NULL,
              allocated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)


def _wg_list_used_slices(conn) -> set:
    rows = conn.exec_driver_sql(
        "SELECT slice_cidr FROM wg_tenant_slices").fetchall()
    used = set(WG_RESERVED_SLICES)
    for r in rows:
        if r and r[0]:
            used.add(r[0])
    return used


def _wg_get_or_create_slice(company_id: str) -> str:
    """Look up (or allocate) the /20 WireGuard slice for this tenant.

    Allocation is sequential through 10.50.0.0/16 — skipping reserved
    slices and slices already handed out to other tenants. Raises
    HTTPException(503, 'wg_no_free_slice') when the master pool is full
    (only when more tenants than fit in /16 at the chosen prefix).
    """
    _ensure_wg_tenant_slices_table()
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT slice_cidr FROM wg_tenant_slices WHERE company_id=?",
            (company_id,)).fetchone()
        if row and row[0]:
            return row[0]
        used = _wg_list_used_slices(conn)
        master = ipaddress.ip_network(WG_MASTER_NETWORK)
        try:
            subnets = list(master.subnets(new_prefix=WG_TENANT_PREFIX_BITS))
        except ValueError:
            raise HTTPException(500, "wg_invalid_prefix_config")
        for sub in subnets:
            cidr = str(sub)
            if cidr in used:
                continue
            conn.exec_driver_sql(
                "INSERT INTO wg_tenant_slices (company_id, slice_cidr) VALUES (?, ?)",
                (company_id, cidr))
            return cidr
        raise HTTPException(503, "wg_no_free_slice")


def _wg_genkeys():
    """Generate (private, public) keypair via wg cli."""
    priv = subprocess.check_output(["wg", "genkey"]).decode().strip()
    pub  = subprocess.check_output(["wg", "pubkey"], input=priv.encode()).decode().strip()
    return priv, pub

def _wg_pick_next_ip(server_conf: str, slice_cidr: Optional[str] = None) -> str:
    """s39: Find the next free /32 within `slice_cidr` (or the legacy
    /16 if no slice provided — kept for backward compatibility). Scans
    AllowedIPs entries in `wg0.conf` and picks the lowest unused host.
    Capacity per /20 slice: 4094 OLTs.
    """
    used = set()
    # Always reserve the master server-side gateways.
    used.add("10.50.0.0")
    used.add("10.50.0.1")
    used.add("10.50.255.255")
    for m in re.finditer(r"AllowedIPs\s*=\s*([0-9.]+)/32", server_conf):
        used.add(m.group(1))

    if slice_cidr:
        try:
            net = ipaddress.ip_network(slice_cidr)
        except Exception:
            raise HTTPException(500, "wg_invalid_slice")
        # First host is the tenant gateway/reserved.
        hosts = list(net.hosts())
        # Skip the first IP of the slice (tenant-internal reserved).
        for host in hosts[1:]:
            ip = str(host)
            if ip not in used:
                return ip
        raise HTTPException(503, "wg_slice_exhausted")

    # Legacy sweep: whole 10.50.0.0/16 — kept for safety.
    for third in range(0, 256):
        start = 2 if third == 0 else 0
        end   = 255 if third != 255 else 255
        for fourth in range(start, end):
            if third == 255 and fourth == 255: continue
            ip = f"10.50.{third}.{fourth}"
            if ip not in used:
                return ip
    raise HTTPException(500, "wg_pool_exhausted")

def _wg_append_peer(name: str, pubkey: str, peer_ip: str) -> None:
    """Append a peer block to wg0.conf (between BEGIN/END markers) and
    hot-load it via `wg set wg0 peer ... allowed-ips ...`."""
    block = (
        f"\n# {name}\n"
        f"[Peer]\n"
        f"PublicKey = {pubkey}\n"
        f"AllowedIPs = {peer_ip}/32\n"
    )
    with open(WG_CONF_PATH, "r") as f: txt = f.read()
    if WG_PEER_END not in txt:
        # Just append at end if markers missing
        new_txt = txt + block
    else:
        new_txt = txt.replace(WG_PEER_END, block + WG_PEER_END, 1)
    with open(WG_CONF_PATH, "w") as f: f.write(new_txt)
    os.chmod(WG_CONF_PATH, 0o600)
    # Hot-load peer
    try:
        subprocess.run(["wg", "set", "wg0",
                        "peer", pubkey,
                        "allowed-ips", f"{peer_ip}/32"],
                       check=True, timeout=10)
    except Exception as e:
        # Fall back to full reload
        try:
            subprocess.run(["wg-quick", "down", "wg0"], timeout=10)
            subprocess.run(["wg-quick", "up",   "wg0"], timeout=10)
        except Exception: pass


@router.post("/api/admin/olt/olts/{olt_id}/wg-config")
def api_wg_config(request: Request, olt_id: int):
    """_v4727_  Generate (or rotate) WireGuard credentials for an OLT and
    return the client `.conf` text the operator pastes onto the OLT's router/
    onsite PC.

    s39: Per-tenant slice isolation — each tenant's OLTs get IPs from
    a dedicated /20 inside 10.50.0.0/16. Client `AllowedIPs` is now the
    tenant's slice (not the legacy 10.50.0.0/24) so each tenant's tunnel
    only routes platform-bound traffic, never inter-tenant.
    """
    cid, _ = _require_admin(request)
    slice_cidr = _wg_get_or_create_slice(cid)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, name, connection_mode, vpn_address, vpn_peer_privkey, "
            "       vpn_peer_pubkey FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "OLT not found")
        # s39: enforce per-tenant max-OLTs-on-VPN cap. Counts only OLTs
        # *other than* the one being configured — so re-issuing a key
        # for an already-configured OLT is always allowed (it doesn't
        # add a new IP).
        existing_pub = (row[5] or "").strip()
        if not existing_pub:
            (used_count,) = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM olts WHERE company_id=? "
                " AND vpn_peer_pubkey != '' AND vpn_peer_pubkey IS NOT NULL "
                " AND id != ?",
                (cid, olt_id)).fetchone()
            if used_count >= WG_MAX_OLTS_PER_TENANT:
                raise HTTPException(
                    403,
                    f"vpn_quota_exhausted: this admin has reached the limit "
                    f"of {WG_MAX_OLTS_PER_TENANT} OLTs on VPN. Revoke an "
                    f"existing OLT's VPN access before adding a new one.")
        # If we already have a keypair + address, just rebuild the client conf.
        priv = row[4]; pub = row[5]; peer_ip = row[3] or ""
        if not (priv and pub and peer_ip):
            try:
                with open(WG_CONF_PATH) as f: server_conf = f.read()
            except Exception:
                raise HTTPException(500, "wg_server_not_initialised")
            priv, pub = _wg_genkeys()
            peer_ip = _wg_pick_next_ip(server_conf, slice_cidr=slice_cidr)
            _wg_append_peer(f"OLT-{olt_id}-{row[1]}-{cid}", pub, peer_ip)
            conn.exec_driver_sql(
                "UPDATE olts SET vpn_peer_privkey=?, vpn_peer_pubkey=?, "
                " vpn_address=?, connection_mode='vpn', host=? "
                " WHERE id=? AND company_id=?",
                (priv, pub, peer_ip, peer_ip, olt_id, cid))
    client_conf = (
        f"# AutoISP WireGuard config — primary endpoint :51820 UDP\n"
        f"# If your ISP blocks UDP/51820, change the Endpoint line to:\n"
        f"#   Endpoint = {WG_SERVER_ENDPOINT_FALLBACK}\n"
        f"# (server accepts both via iptables DNAT)\n"
        f"[Interface]\n"
        f"PrivateKey = {priv}\n"
        f"Address = {peer_ip}/32\n"
        f"DNS = {WG_DNS}\n\n"
        f"[Peer]\n"
        f"PublicKey = {WG_SERVER_PUBKEY}\n"
        f"Endpoint = {WG_SERVER_ENDPOINT}\n"
        f"AllowedIPs = {slice_cidr}\n"
        f"PersistentKeepalive = 25\n"
    )
    return {"ok": True, "client_conf": client_conf,
            "vpn_address": peer_ip,
            "slice_cidr": slice_cidr,
            "server_pubkey": WG_SERVER_PUBKEY,
            "server_endpoint": WG_SERVER_ENDPOINT, "server_endpoint_fallback": WG_SERVER_ENDPOINT_FALLBACK}


# ============================================================================
# s39: Peer revocation + listing
# ============================================================================
def _wg_remove_peer_block(pubkey: str) -> bool:
    """Strip the peer block matching `pubkey` from wg0.conf. Returns True
    if a block was removed.

    Block boundaries: from a line containing `[Peer]` (optionally preceded
    by a `# comment` line) up to but not including the next `[Peer]`,
    `# === END PEERS`, or end-of-file.
    """
    try:
        with open(WG_CONF_PATH, "r") as f:
            txt = f.read()
    except Exception:
        return False
    if pubkey not in txt:
        return False
    lines = txt.splitlines(keepends=True)
    out = []
    i = 0
    removed = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[Peer]":
            # Collect this block until next [Peer] / END marker / EOF.
            block_start = i
            # Include preceding `# ...` comment line if present.
            if out and out[-1].lstrip().startswith("# ") and "===" not in out[-1]:
                block_comment_line = out.pop()
            else:
                block_comment_line = None
            j = i + 1
            while j < len(lines):
                l2 = lines[j].strip()
                if l2 == "[Peer]" or l2.startswith("# === END PEERS"):
                    break
                j += 1
            block_text = "".join(lines[block_start:j])
            if pubkey in block_text:
                removed = True
                i = j
                continue
            else:
                if block_comment_line is not None:
                    out.append(block_comment_line)
                out.append(line)
                i += 1
                continue
        out.append(line)
        i += 1
    if removed:
        with open(WG_CONF_PATH, "w") as f:
            f.write("".join(out))
        os.chmod(WG_CONF_PATH, 0o600)
    return removed


@router.post("/api/admin/olt/olts/{olt_id}/wg-revoke")
def api_wg_revoke(request: Request, olt_id: int):
    """s39: Revoke the WireGuard peer for an OLT. Removes the peer from
    `wg0.conf`, runs `wg set wg0 peer <pubkey> remove` for instant hot
    eviction, clears the DB columns, and resets `connection_mode` to
    'public' so the row reflects the disconnect.
    """
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT vpn_peer_pubkey FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
        if not row:
            raise HTTPException(404, "OLT not found")
        pubkey = (row[0] or "").strip()
    if not pubkey:
        return {"ok": True, "message": "no peer to revoke", "removed": False}
    removed = False
    try:
        removed = _wg_remove_peer_block(pubkey)
    except Exception as e:
        raise HTTPException(500, f"wg_conf_edit_failed: {e}")
    try:
        subprocess.run(["wg", "set", "wg0", "peer", pubkey, "remove"],
                       check=False, timeout=10,
                       capture_output=True)
    except Exception:
        pass
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE olts SET vpn_peer_pubkey='', vpn_peer_privkey='', "
            " vpn_address='', connection_mode='public', host='' "
            " WHERE id=? AND company_id=?",
            (olt_id, cid))
    return {"ok": True, "removed": removed, "pubkey_prefix": pubkey[:8]}


def _wg_show_dump() -> List[Dict[str, Any]]:
    """Parse `wg show wg0 dump`. First line is server (skip). Each peer line:
    public-key  preshared-key  endpoint  allowed-ips  latest-handshake
                tx-bytes  rx-bytes  persistent-keepalive
    """
    try:
        out = subprocess.check_output(["wg", "show", "wg0", "dump"],
                                       timeout=5).decode()
    except Exception:
        return []
    rows = []
    for idx, line in enumerate(out.strip().splitlines()):
        if idx == 0:
            # Server-self line — skip.
            continue
        cols = line.split("\t")
        if len(cols) < 8:
            continue
        pub, _psk, endpoint, allowed_ips, handshake, tx, rx, keepalive = cols[:8]
        try:
            hs = int(handshake)
        except Exception:
            hs = 0
        rows.append({
            "pubkey": pub,
            "endpoint": endpoint if endpoint != "(none)" else "",
            "allowed_ips": allowed_ips,
            "latest_handshake": hs,
            "tx_bytes": int(tx) if tx.isdigit() else 0,
            "rx_bytes": int(rx) if rx.isdigit() else 0,
            "persistent_keepalive": keepalive if keepalive != "off" else "",
        })
    return rows


@router.get("/api/admin/olt/wg-peers")
def api_wg_peers_admin(request: Request):
    """List this tenant's VPN peers + live handshake stats.

    Joins `wg show wg0 dump` rows (live wg state) with the tenant's
    `olts` rows (DB) on `vpn_peer_pubkey`. Returns BOTH connected and
    disconnected peers so admins can spot OLTs that haven't dialled in
    recently.
    """
    cid, _ = _require_admin(request)
    live = {p["pubkey"]: p for p in _wg_show_dump()}
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, name, vpn_address, vpn_peer_pubkey, status, last_polled "
            " FROM olts WHERE company_id=? AND vpn_peer_pubkey != '' "
            " ORDER BY id ASC", (cid,)).fetchall()
        slice_row = conn.exec_driver_sql(
            "SELECT slice_cidr FROM wg_tenant_slices WHERE company_id=?",
            (cid,)).fetchone()
    now_ts = int(time.time())
    peers = []
    for r in rows:
        olt_id, name, ip, pub, st, last_polled = r
        live_row = live.get(pub or "", {})
        hs = live_row.get("latest_handshake", 0) or 0
        peers.append({
            "olt_id": olt_id,
            "name": name,
            "vpn_address": ip or "",
            "pubkey_prefix": (pub or "")[:8],
            "endpoint": live_row.get("endpoint", ""),
            "tx_bytes": live_row.get("tx_bytes", 0),
            "rx_bytes": live_row.get("rx_bytes", 0),
            "latest_handshake": hs,
            "handshake_age_sec": (now_ts - hs) if hs else None,
            "online": bool(hs) and (now_ts - hs) < 180,
            "olt_status": st or "unknown",
            "last_polled": last_polled or "",
        })
    return {"ok": True,
            "slice_cidr": (slice_row[0] if slice_row else ""),
            "peers": peers,
            "max_per_tenant": WG_MAX_OLTS_PER_TENANT,
            "used": len(peers),
            "remaining": max(0, WG_MAX_OLTS_PER_TENANT - len(peers)),
            "server_endpoint": WG_SERVER_ENDPOINT, "server_endpoint_fallback": WG_SERVER_ENDPOINT_FALLBACK}


# ============================================================================
# s39: SuperAdmin cross-tenant peer monitor (internal — auth via X-Internal-Token)
# ============================================================================
@router.get("/api/internal/wg-peers-all")
def api_wg_peers_all(request: Request):
    """Cross-tenant peer dump for the SuperAdmin portal. Returns every
    peer in `wg0.conf` JOINed with its owner tenant (via olts table).
    Auth: internal token only — never exposed to admin UI.
    """
    tok = request.headers.get("X-Internal-Token") or \
          request.headers.get("x-internal-token") or ""
    expected = os.environ.get("ISP_INTERNAL_TOKEN", "isp-bill-internal-sa-token-v1")
    if not expected or tok != expected:
        raise HTTPException(401, "internal_token_required")
    live = _wg_show_dump()
    live_by_pub = {p["pubkey"]: p for p in live}
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, company_id, name, vpn_address, vpn_peer_pubkey, "
            "       status, last_polled FROM olts "
            " WHERE vpn_peer_pubkey != '' ORDER BY company_id, id").fetchall()
        slices = {r[0]: r[1] for r in conn.exec_driver_sql(
            "SELECT company_id, slice_cidr FROM wg_tenant_slices").fetchall()}
    now_ts = int(time.time())
    out = []
    db_pubs = set()
    for r in rows:
        olt_id, comp, name, ip, pub, st, last_polled = r
        db_pubs.add(pub or "")
        live_row = live_by_pub.get(pub or "", {})
        hs = live_row.get("latest_handshake", 0) or 0
        out.append({
            "olt_id": olt_id,
            "company_id": comp,
            "name": name,
            "vpn_address": ip or "",
            "slice_cidr": slices.get(comp, ""),
            "pubkey": pub or "",
            "endpoint": live_row.get("endpoint", ""),
            "tx_bytes": live_row.get("tx_bytes", 0),
            "rx_bytes": live_row.get("rx_bytes", 0),
            "latest_handshake": hs,
            "handshake_age_sec": (now_ts - hs) if hs else None,
            "online": bool(hs) and (now_ts - hs) < 180,
            "olt_status": st or "unknown",
            "in_wg_state": (pub or "") in live_by_pub,
        })
    # Orphan peers — live in wg0 but not tracked in any tenant's olts.
    orphans = []
    for p in live:
        if p["pubkey"] not in db_pubs:
            orphans.append({
                "pubkey": p["pubkey"],
                "endpoint": p["endpoint"],
                "allowed_ips": p["allowed_ips"],
                "latest_handshake": p["latest_handshake"],
                "handshake_age_sec": (now_ts - p["latest_handshake"]) if p["latest_handshake"] else None,
                "tx_bytes": p["tx_bytes"],
                "rx_bytes": p["rx_bytes"],
            })
    return {"ok": True,
            "peers": out,
            "orphans": orphans,
            "total_peers": len(out),
            "total_tenants": len(slices),
            "max_per_tenant": WG_MAX_OLTS_PER_TENANT,
            "server_endpoint": WG_SERVER_ENDPOINT, "server_endpoint_fallback": WG_SERVER_ENDPOINT_FALLBACK}


@router.post("/api/internal/wg-peers/{pubkey:path}/remove")
def api_wg_peer_remove_internal(pubkey: str, request: Request):
    """Hard-remove an orphan peer (no DB row). SuperAdmin only. Useful
    for cleaning up leftover entries in `wg0.conf` after a DB delete that
    didn't go through the normal revoke flow."""
    tok = request.headers.get("X-Internal-Token") or \
          request.headers.get("x-internal-token") or ""
    expected = os.environ.get("ISP_INTERNAL_TOKEN", "isp-bill-internal-sa-token-v1")
    if not expected or tok != expected:
        raise HTTPException(401, "internal_token_required")
    if not pubkey or len(pubkey) < 20:
        raise HTTPException(400, "invalid_pubkey")
    try:
        removed = _wg_remove_peer_block(pubkey)
    except Exception as e:
        raise HTTPException(500, f"wg_conf_edit_failed: {e}")
    try:
        subprocess.run(["wg", "set", "wg0", "peer", pubkey, "remove"],
                       check=False, timeout=10, capture_output=True)
    except Exception:
        pass
    return {"ok": True, "removed": removed}


# ──────────────────────────────────────────────────────────────────────────
#  __S44D__  Helpers, diagnostics, Telegram dispatch
# ──────────────────────────────────────────────────────────────────────────

def _s44d_fernet():
    """Re-use the shared PAYMENT_GW_FERNET_KEY (Phase S43Z*) for OLT secrets,
    or fall back to a deterministic file-based key if not present."""
    try:
        from cryptography.fernet import Fernet
        k = os.environ.get("PAYMENT_GW_FERNET_KEY") or os.environ.get("OLT_FERNET_KEY")
        if not k:
            # Fallback — same key file the payments module uses.
            try:
                k = open("/etc/ispbilling/olt_fernet.key", "r").read().strip()
            except Exception:
                k = None
        if not k:
            return None
        return Fernet(k.encode() if isinstance(k, str) else k)
    except Exception:
        return None

def _s44d_encrypt(raw: str) -> str:
    if not raw:
        return ""
    f = _s44d_fernet()
    if not f:
        # No key — store as-is (still better than crashing). Logs are noisy
        # on purpose so admin notices the misconfiguration.
        print("[S44D] WARNING: no Fernet key — storing OLT secret in plaintext")
        return raw
    try:
        return f.encrypt(raw.encode("utf-8")).decode("ascii")
    except Exception as e:
        print(f"[S44D] encrypt error: {e}")
        return raw

def _s44d_decrypt(blob: str) -> str:
    if not blob:
        return ""
    f = _s44d_fernet()
    if not f:
        return blob
    try:
        return f.decrypt(blob.encode("ascii") if isinstance(blob, str) else blob).decode("utf-8")
    except Exception:
        # Probably stored plaintext on a pre-fernet system — return as-is.
        return blob


# ─── E. WG-status diagnostic ─────────────────────────────────────────────
@router.get("/api/admin/olt/olts/{olt_id}/wg-status")
def api_olt_wg_status(request: Request, olt_id: int):
    # # __s56q_via_nas_full__
    # via_nas branch: OLT is reached through a parent NAS WireGuard tunnel.
    # Probe its IP via ICMP (cloud -> wg0 -> NAS LAN -> OLT). Return a
    # meaningful status instead of "never_provisioned".
    try:
        from radius_network import NasDevice
        from database import SessionLocal as _SL
        _cid, _ = _require_admin(request)
        with engine.begin() as _conn:
            _r = _conn.exec_driver_sql(
                "SELECT vpn_type, host, parent_nas_id FROM olts "
                "WHERE id=? AND company_id=?",
                (olt_id, _cid)).fetchone()
        if _r and (_r[0] or "").lower() == "via_nas":
            _host = _r[1] or ""
            _parent = _r[2] or 0
            _db = _SL()
            try:
                _nas = (_db.query(NasDevice)
                          .filter(NasDevice.company_id == _cid,
                                  NasDevice.id == _parent)
                          .first())
                _nas_name = (_nas.name if _nas else "?") or "?"
                _nas_ip = (_nas.ip_address if _nas else "") or ""
            finally:
                _db.close()
            import subprocess as _sp
            try:
                _rr = _sp.run(["ping", "-c", "1", "-W", "2", _host],
                              capture_output=True, text=True, timeout=4)
                _alive = (_rr.returncode == 0)
            except Exception:
                _alive = False
            if _alive:
                return {"success": True, "status": "ready_via_nas",
                        "via_nas_id": _parent, "via_nas_name": _nas_name,
                        "nas_ip": _nas_ip,
                        "message": (f"OLT reachable via parent NAS "
                                    f"'{_nas_name}' ({_nas_ip}) WG tunnel.")}
            return {"success": False, "status": "via_nas_unreachable",
                    "via_nas_id": _parent, "via_nas_name": _nas_name,
                    "message": (f"OLT not pinging through parent NAS "
                                f"'{_nas_name}'. Check the Mikrotik WG "
                                f"handshake + that the OLT IP {_host} is "
                                f"on the Mikrotik LAN.")}
    except Exception as _e:
        print(f"[wg-status via_nas branch] {_e}")
    """Read live WireGuard handshake state for this OLT's peer.
    Returns the same fields `wg show` exposes, so the admin can see
    whether the on-site router actually reached the tunnel."""
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT vpn_peer_pubkey, vpn_address, connection_mode "
            "FROM olts WHERE id=? AND company_id=?", (olt_id, cid)
        ).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    pubkey = (row[0] or "").strip()
    vpn_addr = row[1] or ""
    conn_mode = row[2] or "public"
    if conn_mode != "vpn" or not pubkey:
        return {"ok": True, "status": "n/a",
                "message": "OLT is not configured for WireGuard VPN."}
    # Parse `wg show wg0 dump` (faster + machine-readable than wg show)
    import subprocess
    try:
        raw = subprocess.run(
            ["wg", "show", "wg0", "dump"], capture_output=True, text=True,
            timeout=4).stdout
    except Exception as e:
        return {"ok": False, "status": "error", "message": f"wg show failed: {e}"}
    line = next((ln for ln in raw.splitlines() if ln.startswith(pubkey)), None)
    if not line:
        return {"ok": True, "status": "never_registered",
                "pubkey_prefix": pubkey[:12] + "…",
                "vpn_address": vpn_addr,
                "message": ("This OLT's peer key is not loaded in the live "
                            "tunnel. Re-issue the .conf file from the VPN button.")}
    parts = line.split("\t")
    # cols: pubkey, psk, endpoint, allowed-ips, last_handshake, rx, tx, keepalive
    endpoint        = parts[2] if len(parts) > 2 else ""
    last_handshake  = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
    rx              = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
    tx              = int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0
    now = int(time.time())
    age = (now - last_handshake) if last_handshake else None
    if not last_handshake:
        status = "never"
        msg = ("Peer registered but no handshake has ever happened. "
               "Check: 1) Mikrotik's WG peer endpoint is "
               + "185.199.53.93:51820 UDP, "
               + "2) AllowedIPs=10.50.0.0/16, "
               + "3) persistent-keepalive=25s, "
               + "4) Mikrotik's WG interface is enabled.")
    elif age is not None and age > 180:
        status = "stale"
        msg = f"Last handshake was {age}s ago — Mikrotik likely lost connectivity."
    else:
        status = "connected"
        msg = f"Tunnel healthy. Last handshake {age}s ago."
    return {"ok": True, "status": status,
            "pubkey_prefix": pubkey[:12] + "…",
            "vpn_address": vpn_addr,
            "endpoint": endpoint, "last_handshake_unix": last_handshake,
            "handshake_age_sec": age, "rx_bytes": rx, "tx_bytes": tx,
            "message": msg}


# (removed olt_notify_telegram in S44J — Telegram fields dropped in S44F)


# ──────────────────────────────────────────────────────────────────────────
#  __S44E_VPN_PROVISION__  Per-OLT VPN provisioning routes
#  Supports OpenVPN, L2TP/IPsec, and PPTP — uses the daemons installed by
#  /tmp/install_vpns.py (OpenVPN-server@olt, strongswan, xl2tpd, pptpd).
# ──────────────────────────────────────────────────────────────────────────

import secrets as _s44e_secrets
import subprocess as _s44e_sp

_S44E_SERVER_IP = "185.199.53.93"
_S44E_OVPN_EASYRSA = "/etc/openvpn/server/easy-rsa"
_S44E_L2TP_PSK_FILE = "/etc/ispbilling/vpn/l2tp_psk.txt"
_S44E_CHAP_SECRETS  = "/etc/ppp/chap-secrets"

def _s44e_safe_username(olt_id: int, vpn_type: str) -> str:
    """Generate a deterministic, conflict-free username per OLT+protocol."""
    return f"olt{olt_id}_{vpn_type[:3]}"

def _s44e_chap_upsert(username: str, password: str, service: str = "*") -> None:
    """Idempotent add/replace of a chap-secrets line for username."""
    new_line = f'"{username}"\t{service}\t"{password}"\t*\n'
    try:
        lines = open(_S44E_CHAP_SECRETS).read().splitlines(keepends=True)
    except Exception:
        lines = ["# Auto-managed by ISPBilling\n"]
    out, found = [], False
    for ln in lines:
        if ln.lstrip().startswith(f'"{username}"'):
            out.append(new_line); found = True
        else:
            out.append(ln)
    if not found:
        out.append(new_line)
    with open(_S44E_CHAP_SECRETS, "w") as f:
        f.writelines(out)
    try:
        os.chmod(_S44E_CHAP_SECRETS, 0o600)
    except Exception:
        pass

def _s44o_write_ccd_iroute(cn: str, olt_host: str) -> str:
    """__S44O__ Write /etc/openvpn/server/ccd/<cn> with an iroute pushing
    the OLT's /24 LAN subnet down THIS client's tunnel. Reads olt_host
    (e.g. '192.168.22.107') and derives '192.168.22.0/24'. Idempotent;
    overwrites any existing file. Returns the subnet (or '' on error).
    Caller is responsible for ensuring `route <subnet> ...` is present
    in the main server.conf so the kernel route to tun0 exists.
    """
    import ipaddress as _ip
    try:
        ip = _ip.IPv4Address((olt_host or "").strip())
        # /24 net containing the OLT host
        net24 = _ip.IPv4Network(f"{ip}/24", strict=False)
        subnet = str(net24.network_address)
        mask   = str(net24.netmask)
        ccd_dir = "/etc/openvpn/server/ccd"
        os.makedirs(ccd_dir, exist_ok=True)
        with open(f"{ccd_dir}/{cn}", "w") as f:
            f.write(f"# __S44O__ Auto-generated for OLT cn={cn}\n")
            f.write(f"# Pushes traffic destined for {subnet}/24 down this client.\n")
            f.write(f"iroute {subnet} {mask}\n")
        # Also ensure the kernel-side `route` is in the main server.conf
        # so OpenVPN adds a route to tun0 for this subnet on startup.
        srv_conf = "/etc/openvpn/server/olt.conf"
        if os.path.exists(srv_conf):
            with open(srv_conf) as f:
                conf_txt = f.read()
            route_line = f"route {subnet} {mask}"
            if route_line not in conf_txt:
                with open(srv_conf, "a") as f:
                    f.write(f"\n# __S44O__ auto-route for OLT {cn}\n")
                    f.write(f"{route_line}\n")
                # Reload the server so the kernel route is added live.
                _s44e_sp.run("systemctl reload openvpn-server@olt "
                             "|| systemctl restart openvpn-server@olt",
                             shell=True, timeout=15)
        return f"{subnet}/24"
    except Exception as _e:
        try: print(f"[s44o] CCD iroute write failed for {cn}: {_e}")
        except Exception: pass
        return ""


def _s44e_gen_openvpn(olt_id: int) -> tuple[str, str, str]:
    """Return (username, '', inline_conf). username is the cert CN."""
    cn = _s44e_safe_username(olt_id, "openvpn")
    # Issue cert via easy-rsa (idempotent: skip if already exists)
    cert_path = f"{_S44E_OVPN_EASYRSA}/pki/issued/{cn}.crt"
    if not os.path.exists(cert_path):
        cmd = (f"cd {_S44E_OVPN_EASYRSA} && "
               f"EASYRSA_BATCH=1 ./easyrsa gen-req {cn} nopass >/dev/null 2>&1 && "
               f"echo 'yes' | EASYRSA_BATCH=1 ./easyrsa sign-req client {cn} >/dev/null 2>&1")
        _s44e_sp.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
    # Compose inline .ovpn
    try:
        ca   = open(f"{_S44E_OVPN_EASYRSA}/pki/ca.crt").read()
        cert = open(cert_path).read()
        key  = open(f"{_S44E_OVPN_EASYRSA}/pki/private/{cn}.key").read()
        ta   = open(f"{_S44E_OVPN_EASYRSA}/ta.key").read()
    except Exception as e:
        return cn, "", f"# OpenVPN cert generation failed: {e}"
    # __S44I_CLIENT__  Mikrotik-compatible profile:
    #   - no <tls-auth> (RouterOS doesn't support it)
    #   - single-cipher AES-256-CBC + SHA256 (universal Mikrotik 7.x compatibility)
    inline = (
        f"# Per-OLT OpenVPN client config for OLT #{olt_id}\n"
        f"client\n"
        f"dev tun\n"
        f"proto udp\n"
        f"remote {_S44E_SERVER_IP} 1194\n"
        f"resolv-retry infinite\n"
        f"nobind\n"
        f"persist-key\n"
        f"persist-tun\n"
        f"remote-cert-tls server\n"
        f"data-ciphers AES-256-CBC\n"
        f"data-ciphers-fallback AES-256-CBC\n"
        f"cipher AES-256-CBC\n"
        f"auth SHA256\n"
        f"tls-version-min 1.2\n"
        f"verb 3\n"
        f"<ca>\n{ca}\n</ca>\n"
        f"<cert>\n{cert}\n</cert>\n"
        f"<key>\n{key}\n</key>\n"
    )
    _ = ta  # tls-auth not bundled (Mikrotik compat)
    # __S44O__ Auto-write CCD iroute so the server can push
    # SNMP/HTTP/SSH packets destined for the OLT's LAN down
    # this tunnel. Reads olt.host from the DB.
    try:
        with engine.begin() as _c:
            _row = _c.exec_driver_sql(
                "SELECT host FROM olts WHERE id=?",
                (olt_id,)).fetchone()
        if _row and _row[0]:
            _s44o_write_ccd_iroute(cn, _row[0])
    except Exception as _e:
        print(f"[s44o-hook] {_e}")
    return cn, "", inline

def _s44e_gen_l2tp(olt_id: int) -> tuple[str, str, str]:
    """Return (username, password, info-text-with-PSK)."""
    user = _s44e_safe_username(olt_id, "l2tp")
    pw = _s44e_secrets.token_urlsafe(16)
    _s44e_chap_upsert(user, pw, "*")
    try:
        psk = open(_S44E_L2TP_PSK_FILE).read().strip()
    except Exception:
        psk = "(missing — regenerate)"
    info = (
        f"L2TP/IPsec credentials for OLT #{olt_id}\n"
        f"========================================\n\n"
        f"Server      : {_S44E_SERVER_IP}\n"
        f"VPN type    : L2TP/IPsec (PSK)\n"
        f"Pre-shared key (PSK): {psk}\n"
        f"Username    : {user}\n"
        f"Password    : {pw}\n\n"
        f"After connecting, the OLT-site router will receive an internal "
        f"IP in the 10.52.0.0/24 range. Configure it as the tunnel peer in "
        f"your Mikrotik/router's L2TP client.\n"
    )
    return user, pw, info

def _s44e_gen_pptp(olt_id: int) -> tuple[str, str, str]:
    user = _s44e_safe_username(olt_id, "pptp")
    pw = _s44e_secrets.token_urlsafe(14)  # PPTP MSCHAPv2 — keep ASCII-safe
    _s44e_chap_upsert(user, pw, "ISPBilling-PPTP")
    info = (
        f"PPTP credentials for OLT #{olt_id}\n"
        f"==================================\n\n"
        f"Server      : {_S44E_SERVER_IP}\n"
        f"VPN type    : PPTP (MS-CHAPv2 + MPPE-128)\n"
        f"Username    : {user}\n"
        f"Password    : {pw}\n\n"
        f"WARNING: PPTP uses MS-CHAPv2 which is cryptographically weak. "
        f"Prefer OpenVPN or L2TP/IPsec for new deployments.\n\n"
        f"After connecting, the OLT-site router will receive an internal "
        f"IP in 10.53.0.10-100.\n"
    )
    return user, pw, info


@router.post("/api/admin/olt/olts/{olt_id}/vpn-provision")
def api_olt_vpn_provision(request: Request, olt_id: int):
    """Generate or re-issue VPN credentials for this OLT based on its
    `vpn_type` setting. Returns the config payload the admin should
    upload to the on-site router."""
    cid, actor = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT vpn_type, name FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    vpn_type = (row[0] or "none").lower()
    # __s56q_via_nas_full__ + # __s56s_via_nas_autowire__
    if vpn_type == "via_nas":
        # Pull the OLT row to figure out its /24 + parent NAS id.
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT host, parent_nas_id FROM olts WHERE id=?",
                (olt_id,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(400, "OLT host not set")
        olt_ip = row[0]
        parent_nas_id = row[1] or 0
        try:
            import ipaddress as _ipaddr
            net = _ipaddr.ip_network(olt_ip + "/24", strict=False)
            olt_subnet = str(net)
        except Exception:
            raise HTTPException(400, f"Bad OLT IP {olt_ip}")

        auto_wire_steps = []

        # ─── Step 1: update WG peer AllowedIPs on the cloud ─────────
        try:
            import subprocess as _sp
            from radius_network import NasDevice as _Nas
            from database import SessionLocal as _SL
            _db = _SL()
            try:
                _nas = _db.query(_Nas).filter(_Nas.id == parent_nas_id).first()
            finally:
                _db.close()
            if not _nas:
                raise RuntimeError(f"parent NAS#{parent_nas_id} not found")

            # Read wg0.conf to find the peer's public key + current allowed-ips.
            conf_path = "/etc/wireguard/wg0.conf"
            conf = open(conf_path).read()
            # Match the peer block by its tunnel IP (10.50.x.x/32 in AllowedIPs).
            nas_tun = (_nas.ip_address or "").strip()
            pat = re.compile(
                r"PublicKey\s*=\s*([A-Za-z0-9+/=]+)\s*\n"
                r"AllowedIPs\s*=\s*([^\n]+)",
                re.MULTILINE)
            target = None
            for mm in pat.finditer(conf):
                if nas_tun in mm.group(2):
                    target = mm
                    break
            if not target:
                raise RuntimeError(
                    f"WG peer for NAS tunnel IP {nas_tun} not found in {conf_path}")
            pub = target.group(1).strip()
            old_allowed = target.group(2).strip()
            if olt_subnet in old_allowed:
                auto_wire_steps.append("AllowedIPs already includes " + olt_subnet)
            else:
                new_allowed = old_allowed.rstrip(",") + "," + olt_subnet
                # live
                _sp.run(["wg", "set", "wg0", "peer", pub,
                         "allowed-ips", new_allowed], check=True,
                        capture_output=True)
                # persist
                new_conf = conf.replace(target.group(0),
                                        f"PublicKey = {pub}\n"
                                        f"AllowedIPs = {new_allowed}", 1)
                open(conf_path, "w").write(new_conf)
                auto_wire_steps.append("added " + olt_subnet + " to peer AllowedIPs")
        except Exception as e:
            auto_wire_steps.append(f"[!] WG AllowedIPs step failed: {e}")

        # ─── Step 2: cloud kernel route ─────────────────────────────
        try:
            import subprocess as _sp2
            r2 = _sp2.run(["ip", "route", "show", olt_subnet],
                          capture_output=True, text=True)
            if "wg0" in r2.stdout:
                auto_wire_steps.append("route already via wg0")
            else:
                # Drop any conflicting route then install ours
                if r2.stdout.strip():
                    _sp2.run(["ip", "route", "del", olt_subnet],
                             capture_output=True)
                    auto_wire_steps.append(
                        "removed conflicting route: " + r2.stdout.strip())
                _sp2.run(["ip", "route", "add", olt_subnet, "dev", "wg0"],
                         capture_output=True)
                auto_wire_steps.append("installed route " + olt_subnet + " dev wg0")
        except Exception as e:
            auto_wire_steps.append(f"[!] cloud route step failed: {e}")

        # ─── Step 3: MikroTik srcnat masquerade ─────────────────────
        try:
            from librouteros import connect as _ros
            from librouteros.login import plain as _plain
            _db = _SL()
            try:
                _nas2 = _db.query(_Nas).filter(_Nas.id == parent_nas_id).first()
            finally:
                _db.close()
            _api = _ros(host=_nas2.ip_address, username=_nas2.api_username,
                        password=_nas2.api_password or "",
                        port=int(_nas2.port or 8728),
                        login_method=_plain, timeout=20)
            _nat = _api.path("/ip/firewall/nat")
            _cmt = "auto-isp-olt-masq-10.50.0.0_16-via-vlan-mgmt-10"
            _have = any(r.get("comment") == _cmt for r in _nat)
            if _have:
                auto_wire_steps.append("MikroTik masq rule already exists")
            else:
                _nat.add(chain="srcnat", action="masquerade",
                         **{"src-address": "10.50.0.0/16",
                            "out-interface": "vlan-mgmt-10",
                            "place-before": "0",
                            "comment": _cmt})
                auto_wire_steps.append("MikroTik masq rule installed")
        except Exception as e:
            auto_wire_steps.append(f"[!] MikroTik masq step failed: {e}")

        # ─── Step 4: flag OLT as VPN-ready ──────────────────────────
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "UPDATE olts SET connection_mode='vpn' WHERE id=?",
                (olt_id,))

        return {"success": True, "vpn_type": "via_nas",
                "message": ("Auto-wired: cloud route + WG AllowedIPs + "
                            "MikroTik masquerade. The OLT should now be "
                            "reachable from the cloud poller."),
                "no_provisioning_required": True,
                "steps": auto_wire_steps}
    if vpn_type not in ("openvpn", "l2tp_ipsec", "pptp"):
        raise HTTPException(400, f"vpn_type={vpn_type} cannot be provisioned per-OLT.")
    if vpn_type == "openvpn":
        user, _, blob = _s44e_gen_openvpn(olt_id)
        pw = ""  # cert-based, no password
        filename = f"olt-{olt_id}.ovpn"
    elif vpn_type == "l2tp_ipsec":
        user, pw, blob = _s44e_gen_l2tp(olt_id)
        filename = f"olt-{olt_id}-l2tp.txt"
    else:
        user, pw, blob = _s44e_gen_pptp(olt_id)
        filename = f"olt-{olt_id}-pptp.txt"
    # Persist (encrypted) into olts row
    blob_enc = _s44d_encrypt(blob)
    pw_enc   = _s44d_encrypt(pw)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE olts SET vpn_username=?, vpn_password_enc=?, "
            "vpn_config_enc=?, vpn_endpoint=?, connection_mode='vpn' "
            "WHERE id=?",
            (user, pw_enc, blob_enc,
             f"{_S44E_SERVER_IP}:" +
             ({"openvpn":"1194","l2tp_ipsec":"500","pptp":"1723"}[vpn_type]),
             olt_id))
    return {"ok": True, "vpn_type": vpn_type, "filename": filename,
            "client_conf": blob, "username": user,
            "server": _S44E_SERVER_IP}


@router.get("/api/admin/olt/olts/{olt_id}/vpn-bundle")
def api_olt_vpn_bundle(request: Request, olt_id: int):
    """__S44L__ Returns a ZIP containing the OpenVPN client files split into
    individual PEM files + a stripped .ovpn config, to work around the
    RouterOS .ovpn-import 'error importing CA' parser bug. The admin then
    uploads each file to Mikrotik Files and imports the cert via
    System -> Certificates -> Import (much more reliable than .ovpn import).
    """
    import io as _io
    import zipfile as _zip
    from fastapi.responses import StreamingResponse

    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT vpn_type, name FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    if (row[0] or "").lower() != "openvpn":
        raise HTTPException(400, "Bundle is only available for OpenVPN OLTs")

    # Ensure cert exists (idempotent generation).
    cn, _, _blob = _s44e_gen_openvpn(olt_id)
    try:
        ca_pem   = open(f"{_S44E_OVPN_EASYRSA}/pki/ca.crt").read()
        cert_pem = open(f"{_S44E_OVPN_EASYRSA}/pki/issued/{cn}.crt").read()
        key_pem  = open(f"{_S44E_OVPN_EASYRSA}/pki/private/{cn}.key").read()
    except Exception as e:
        raise HTTPException(500, f"PKI read failure: {e}")

    # Stripped .ovpn (references certs by name, no inline blocks).
    stripped_conf = (
        f"# Mikrotik split-import config for OLT #{olt_id}\n"
        f"# Use this after importing ca.crt, client.crt, client.key as\n"
        f"# separate files via System -> Certificates -> Import.\n"
        f"client\n"
        f"dev tun\n"
        f"proto udp\n"
        f"remote {_S44E_SERVER_IP} 1194\n"
        f"resolv-retry infinite\n"
        f"nobind\n"
        f"persist-key\n"
        f"persist-tun\n"
        f"remote-cert-tls server\n"
        f"data-ciphers AES-256-CBC\n"
        f"data-ciphers-fallback AES-256-CBC\n"
        f"cipher AES-256-CBC\n"
        f"auth SHA256\n"
        f"tls-version-min 1.2\n"
        f"verb 3\n"
    )

    readme = (
        f"OLT #{olt_id} ({row[1]}) - OpenVPN split bundle\n"
        f"==================================================\n\n"
        f"This bundle exists because some Mikrotik RouterOS builds have a\n"
        f"buggy .ovpn parser that throws 'error importing CA'. Importing\n"
        f"each PEM file separately via System -> Certificates -> Import\n"
        f"bypasses that bug completely.\n\n"
        f"Files in this ZIP:\n"
        f"  1. ca.crt          - The OpenVPN server's CA certificate\n"
        f"  2. client.crt      - This OLT's client certificate (CN={cn})\n"
        f"  3. client.key      - This OLT's client private key\n"
        f"  4. olt-{olt_id}.ovpn   - Stripped config (no inline certs)\n\n"
        f"Mikrotik step-by-step:\n"
        f"  A) Upload ALL 4 files via Files -> Upload.\n"
        f"  B) System -> Certificates -> Import -> pick ca.crt -> Import.\n"
        f"     (no passphrase). It appears as a new row, T flag = trusted.\n"
        f"  C) System -> Certificates -> Import -> pick client.crt -> Import.\n"
        f"  D) System -> Certificates -> Import -> pick client.key -> Import.\n"
        f"     The client.crt row should now show K flag (private key bound).\n"
        f"  E) PPP -> Interface -> [+] -> OVPN Client:\n"
        f"       Name:          ovpn-olt\n"
        f"       Connect To:    {_S44E_SERVER_IP}\n"
        f"       Port:          1194\n"
        f"       Mode:          ip\n"
        f"       Protocol:      udp\n"
        f"       User:          olt        (any non-empty string)\n"
        f"       Password:      (blank)\n"
        f"       Certificate:   {cn}     (the K-flagged one)\n"
        f"       Cipher:        aes256\n"
        f"       Auth:          sha256\n"
        f"       Add Default Route: NO\n"
        f"     Apply -> OK. Flag R appears within ~10s.\n"
        f"  F) Verify: /log print where topics~\"ovpn\"\n"
        f"     Expected: 'connected'\n\n"
        f"CLI shortcut (after files are uploaded):\n"
        f"  /certificate import file-name=ca.crt passphrase=\"\"\n"
        f"  /certificate import file-name=client.crt passphrase=\"\"\n"
        f"  /certificate import file-name=client.key passphrase=\"\"\n"
        f"  /interface ovpn-client add name=ovpn-olt connect-to={_S44E_SERVER_IP} \\\n"
        f"     port=1194 protocol=udp mode=ip user=olt password=\"\" \\\n"
        f"     cipher=aes256 auth=sha256 add-default-route=no \\\n"
        f"     certificate={cn}\n"
        f"  /interface ovpn-client enable [find name=ovpn-olt]\n"
        f"  /log print where topics~\"ovpn\"\n"
    )

    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr("ca.crt",       ca_pem)
        z.writestr("client.crt",   cert_pem)
        z.writestr("client.key",   key_pem)
        z.writestr(f"olt-{olt_id}.ovpn", stripped_conf)
        z.writestr("README.txt",   readme)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition":
                 f'attachment; filename="olt-{olt_id}-mikrotik-bundle.zip"'})


@router.get("/api/admin/olt/olts/{olt_id}/mikrotik-commands")
def api_olt_mikrotik_commands(request: Request, olt_id: int):
    """__S44Q__ Returns a ready-to-paste RouterOS command block for the
    OLT's site router. The block:
      - removes any conflicting forward filter rules
      - adds masquerade NAT for the tunnel pool
      - adds bidirectional forward accept rules between tunnel and LAN
      - enables IP forwarding
    Subnet is auto-derived from the OLT's host IP."""
    import ipaddress as _ip
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id, name, host, vpn_type, vpn_username "
            "FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    if not row[2]:
        raise HTTPException(400, "OLT has no host IP configured")
    try:
        host_ip = _ip.IPv4Address(row[2].strip())
        net24   = _ip.IPv4Network(f"{host_ip}/24", strict=False)
        subnet  = str(net24.network_address)  # e.g. 192.168.22.0
        cidr    = f"{subnet}/24"
    except Exception:
        raise HTTPException(400, f"OLT host '{row[2]}' is not a valid IPv4")

    # Pool per VPN type
    vpn_type = (row[3] or "").lower()
    if vpn_type == "openvpn":
        pool = "10.51.0.0/24"
    elif vpn_type == "wireguard":
        pool = "10.50.0.0/24"
    elif vpn_type in ("l2tp", "l2tp/ipsec", "l2tp_ipsec"):
        pool = "10.52.0.0/24"
    elif vpn_type == "pptp":
        pool = "10.53.0.0/24"
    else:
        pool = "10.51.0.0/24"

    cn = row[4] or f"olt{olt_id}_ope"

    # Build the command block
    cmds_tpl = r"""# ============================================================
# Auto-ISP Billing — Mikrotik setup for OLT "%(name)s" (id=%(olt_id)d)
# Generated for VPN pool %(pool)s and LAN %(cidr)s
# Run ONCE per Mikrotik router. Safe to re-run (idempotent).
# ============================================================

# 1) Find which interface owns your LAN. Pick the row whose address starts with %(subnet_prefix)s
/ip address print

# 2) Replace ether2 below with that interface name if different,
#    then run the block.
:global oltLanIface "ether2"

# 3) Remove any harmful blanket forward filters / stale rules
:do { /ip firewall filter remove [find chain=forward protocol="!udp"] } on-error={ }
:do { /ip firewall filter remove [find comment="auto-isp-olt-vpn-fwd-in"] } on-error={ }
:do { /ip firewall filter remove [find comment="auto-isp-olt-vpn-fwd-out"] } on-error={ }
:do { /ip firewall filter remove [find comment="auto-isp-olt-vpn-fwd-established"] } on-error={ }
:do { /ip firewall nat    remove [find comment="auto-isp-olt-vpn-snat"] } on-error={ }

# 4) Masquerade NAT — required so packets sourced from %(pool)s
#    arrive at the OLT with the router's LAN IP as source.
/ip firewall nat add chain=srcnat \
  src-address=%(pool)s \
  out-interface=$oltLanIface \
  action=masquerade \
  comment="auto-isp-olt-vpn-snat"

# 5) Forward accept rules (top of chain so any catch-all DROP doesn't bite).
#   5a) Established/related return traffic — universal & reliable.
/ip firewall filter add chain=forward action=accept \
  connection-state=established,related \
  place-before=0 comment="auto-isp-olt-vpn-fwd-established"
#   5b) New connections from the VPN pool to the OLT LAN.
/ip firewall filter add chain=forward action=accept \
  src-address=%(pool)s dst-address=%(cidr)s \
  place-before=0 comment="auto-isp-olt-vpn-fwd-in"

# 6) Make sure IP forwarding is on (default yes on RouterOS).
/ip settings set ip-forward=yes

# 7) Verify
/ip firewall nat print stats
/ip firewall filter print stats
/ip route print

# ============================================================
# Done. In the portal, click "Check VPN" then "Auto-detect PON".
# ============================================================
"""
    subnet_prefix = subnet.rsplit(".", 1)[0]  # e.g. 192.168.22
    cmds = cmds_tpl % {
        "name": row[1] or f"OLT-{olt_id}",
        "olt_id": olt_id,
        "pool": pool,
        "cidr": cidr,
        "subnet_prefix": subnet_prefix,
    }
    return {
        "ok": True,
        "olt_id": olt_id,
        "olt_name": row[1],
        "olt_host": str(host_ip),
        "lan_subnet": cidr,
        "vpn_pool": pool,
        "client_cn": cn,
        "commands": cmds,
    }




# ─── _S46B_ Auto-Provision Wizard ─────────────────────────────────────
class AutoProvisionIn(BaseModel):
    olt_id: int
    mikrotik_host: str          # e.g. "192.168.22.1"
    mikrotik_user: str          # "admin"
    mikrotik_pass: str
    mikrotik_lan_iface: Optional[str] = "ether2"
    olt_telnet_user: Optional[str] = "admin"
    olt_telnet_pass: Optional[str] = "admin"
    snmp_community: Optional[str] = "public"


@router.post("/api/admin/olt/auto-provision")
def api_olt_auto_provision(request: Request, body: AutoProvisionIn):
    """_S46B_  One-shot wizard: generates VPN bundle, configures
    Mikrotik, enables SNMP on the OLT, runs first poll. Returns the
    step-by-step log so the UI can show pass/fail per step.
    """
    cid, actor = _require_admin(request)
    steps: List[dict] = []

    def add(name: str, ok: bool, detail: str = ""):
        steps.append({"name": name, "ok": ok, "detail": detail[:400]})

    with engine.begin() as conn:
        olt = conn.exec_driver_sql(
            "SELECT id,name,vendor,host,vpn_type FROM olts "
            "WHERE id=? AND company_id=?",
            (body.olt_id, cid)).fetchone()
    if not olt:
        raise HTTPException(404, "OLT not found")
    if (olt[4] or "").lower() != "openvpn":
        raise HTTPException(400,
            "Auto-provision currently supports OpenVPN OLTs only.")

    # 1. Generate / ensure OpenVPN bundle
    try:
        cn, _hint, _blob = _s44e_gen_openvpn(body.olt_id)
        ca_pem   = open(f"{_S44E_OVPN_EASYRSA}/pki/ca.crt").read()
        cert_pem = open(f"{_S44E_OVPN_EASYRSA}/pki/issued/{cn}.crt").read()
        key_pem  = open(f"{_S44E_OVPN_EASYRSA}/pki/private/{cn}.key").read()
        add("Generate VPN bundle", True, f"CN={cn}")
    except Exception as e:
        add("Generate VPN bundle", False, str(e))
        return {"ok": False, "steps": steps}

    # 2. Connect to Mikrotik via librouteros
    try:
        import librouteros as _lr
        api = _lr.connect(host=body.mikrotik_host,
                          username=body.mikrotik_user,
                          password=body.mikrotik_pass,
                          port=8728, timeout=10)
        add("Connect to Mikrotik API", True,
            f"{body.mikrotik_user}@{body.mikrotik_host}:8728")
    except Exception as e:
        add("Connect to Mikrotik API", False, str(e))
        return {"ok": False, "steps": steps}

    # 3. Upload cert files to Mikrotik Files
    try:
        import paramiko, io as _io
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(body.mikrotik_host, username=body.mikrotik_user,
                    password=body.mikrotik_pass, port=22, timeout=10,
                    look_for_keys=False, allow_agent=False)
        sftp = ssh.open_sftp()
        for fname, content in (("ca.crt", ca_pem),
                                ("client.crt", cert_pem),
                                ("client.key", key_pem)):
            with sftp.file(fname, "w") as f:
                f.write(content)
        sftp.close(); ssh.close()
        add("Upload cert files (SFTP)", True,
            "ca.crt / client.crt / client.key")
    except Exception as e:
        add("Upload cert files (SFTP)", False, str(e))
        # Not fatal — admin can paste later
    # 4. Import certificates via API
    try:
        cert_api = api.path("certificate")
        for fname in ("ca.crt", "client.crt", "client.key"):
            try:
                tuple(cert_api("import", **{"file-name": fname,
                                             "passphrase": ""}))
            except Exception as ie:
                add(f"Import {fname}", False, str(ie)); break
        else:
            add("Import certificates", True, "")
    except Exception as e:
        add("Import certificates", False, str(e))

    # 5. Create ovpn-client interface (idempotent)
    try:
        ovpn = api.path("interface", "ovpn-client")
        # Remove any existing with same name first
        try:
            for r in ovpn:
                if r.get("name") == "ovpn-olt":
                    tuple(ovpn("remove", **{".id": r[".id"]})); break
        except Exception:
            pass
        tuple(ovpn("add", **{
            "name": "ovpn-olt",
            "connect-to": _S44E_SERVER_IP,
            "port": "1194", "protocol": "udp", "mode": "ip",
            "user": "olt", "password": "",
            "cipher": "aes256", "auth": "sha256",
            "add-default-route": "no",
            "certificate": cn,
        }))
        # Ensure enabled
        try:
            for r in ovpn:
                if r.get("name") == "ovpn-olt":
                    tuple(ovpn("enable", **{".id": r[".id"]})); break
        except Exception:
            pass
        add("Create ovpn-client interface", True, "ovpn-olt → "
            f"{_S44E_SERVER_IP}:1194")
    except Exception as e:
        add("Create ovpn-client interface", False, str(e))

    # 6. Push firewall + NAT rules via the mikrotik-commands payload
    try:
        # Re-use the existing command generator to get the right pool/CIDR
        # for this OLT.
        import ipaddress as _ip
        ip = _ip.IPv4Address(olt[3].strip())
        net24 = _ip.IPv4Network(f"{ip}/24", strict=False)
        cidr  = f"{net24.network_address}/24"
        pool  = "10.51.0.0/24"   # openvpn pool
        # NAT srcnat masquerade
        nat = api.path("ip", "firewall", "nat")
        # Wipe existing same-comment rule
        try:
            for r in nat:
                if (r.get("comment") or "") == "auto-isp-olt-vpn-snat":
                    tuple(nat("remove", **{".id": r[".id"]})); break
        except Exception:
            pass
        tuple(nat("add", **{
            "chain": "srcnat", "src-address": pool,
            "out-interface": body.mikrotik_lan_iface or "ether2",
            "action": "masquerade",
            "comment": "auto-isp-olt-vpn-snat"}))
        # Forward filter — established
        fw = api.path("ip", "firewall", "filter")
        for tag in ("auto-isp-olt-vpn-fwd-established",
                    "auto-isp-olt-vpn-fwd-in"):
            try:
                for r in fw:
                    if (r.get("comment") or "") == tag:
                        tuple(fw("remove", **{".id": r[".id"]})); break
            except Exception:
                pass
        tuple(fw("add", **{
            "chain": "forward", "action": "accept",
            "connection-state": "established,related",
            "place-before": "0",
            "comment": "auto-isp-olt-vpn-fwd-established"}))
        tuple(fw("add", **{
            "chain": "forward", "action": "accept",
            "src-address": pool, "dst-address": cidr,
            "place-before": "0",
            "comment": "auto-isp-olt-vpn-fwd-in"}))
        add("Mikrotik NAT / forward rules", True,
            f"pool={pool} lan={cidr} iface={body.mikrotik_lan_iface}")
    except Exception as e:
        add("Mikrotik NAT / forward rules", False, str(e))

    try: api.close()
    except Exception: pass

    # 7. Enable SNMP on OLT via Telnet (best-effort)
    try:
        import telnetlib, time as _t
        tn = telnetlib.Telnet(olt[3], 23, timeout=8)
        tn.read_until(b"login:", timeout=5)
        tn.write((body.olt_telnet_user + "\n").encode())
        tn.read_until(b"assword:", timeout=5)
        tn.write((body.olt_telnet_pass + "\n").encode())
        _t.sleep(1)
        for cmd in (
            "enable",
            "configure terminal",
            f"snmp-server community {body.snmp_community} ro",
            "snmp-server enable",
            "exit",
            "write memory",
        ):
            tn.write((cmd + "\n").encode()); _t.sleep(0.5)
        tn.close()
        add("Enable SNMP on OLT (telnet)", True,
            f"community={body.snmp_community}")
    except Exception as e:
        add("Enable SNMP on OLT (telnet)", False, str(e))

    # 8. Trigger first poll
    try:
        with engine.begin() as conn:
            row = conn.exec_driver_sql(
                "SELECT id, company_id, name, vendor, host, "
                "snmp_community, status, poll_interval, "
                "strftime('%s', created_at) FROM olts WHERE id=?",
                (body.olt_id,)).fetchone()
        keys = ("id","company_id","name","vendor","host",
                "snmp_community","status","poll_interval",
                "created_at_epoch")
        res = _poll_one_olt(dict(zip(keys, row)))
        add("First SNMP poll", bool(res.get("ok")),
            f"online={res.get('online')} total={res.get('total')}")
    except Exception as e:
        add("First SNMP poll", False, str(e))

    return {"ok": all(s["ok"] for s in steps), "steps": steps}

@router.get("/api/admin/olt/check-subnet-collision")
def api_olt_check_subnet(request: Request, host: str = ""):
    """__S44Q__ Returns whether the /24 derived from `host` is already
    used by an OLT belonging to ANOTHER company in this OpenVPN server's
    routing pool. Returned shape:
        {"collision": true/false, "subnet": "192.168.22.0/24",
         "other_company_id": "...", "other_olt_name": "..."}
    Used by the OLT add/edit modal to warn admins before they save."""
    import ipaddress as _ip
    cid, _ = _require_admin(request)
    host = (host or "").strip()
    if not host:
        return {"collision": False, "subnet": "", "reason": "no host"}
    try:
        ip   = _ip.IPv4Address(host)
        net  = _ip.IPv4Network(f"{ip}/24", strict=False)
        subnet_str = str(net.network_address)
    except Exception:
        return {"collision": False, "subnet": "", "reason": "invalid host"}

    with engine.begin() as conn:
        # Find any OLT in ANOTHER company whose host falls inside this /24
        rows = conn.exec_driver_sql(
            "SELECT id, name, host, company_id FROM olts "
            "WHERE company_id != ? AND host IS NOT NULL AND host != '' "
            "AND vpn_type IN ('openvpn','wireguard','l2tp','pptp')",
            (cid,)).fetchall()
    for (oid, oname, ohost, ocid) in rows:
        try:
            other_ip = _ip.IPv4Address(ohost.strip())
            if other_ip in net:
                return {
                    "collision": True,
                    "subnet": f"{subnet_str}/24",
                    "other_company_id": ocid,
                    "other_olt_id": oid,
                    "other_olt_name": oname,
                    "message": (
                        f"Another tenant already uses {subnet_str}/24 "
                        f"through the same VPN server (OLT '{oname}'). "
                        "Please use a different LAN subnet for this OLT, "
                        "otherwise routing will collide. Suggested ranges: "
                        "192.168.X.0/24 with X >= 30, or 10.Y.0.0/16."
                    )
                }
        except Exception:
            continue
    return {"collision": False, "subnet": f"{subnet_str}/24"}


@router.delete("/api/admin/olt/olts/{olt_id}/vpn-revoke")
def api_olt_vpn_revoke(request: Request, olt_id: int):
    """Revoke the OLT's non-WireGuard VPN access (removes chap-secrets
    entries and the OpenVPN cert from the PKI index)."""
    cid, _ = _require_admin(request)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT vpn_type, vpn_username FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "OLT not found")
    vpn_type, user = (row[0] or "none").lower(), (row[1] or "").strip()
    if not user:
        return {"ok": True, "message": "No credentials to revoke."}
    if vpn_type in ("l2tp_ipsec", "pptp"):
        # Strip from chap-secrets
        try:
            lines = open(_S44E_CHAP_SECRETS).read().splitlines(keepends=True)
            with open(_S44E_CHAP_SECRETS, "w") as f:
                for ln in lines:
                    if not ln.lstrip().startswith(f'"{user}"'):
                        f.write(ln)
        except Exception as e:
            return {"ok": False, "error": f"chap-secrets edit failed: {e}"}
    elif vpn_type == "openvpn":
        # Revoke OpenVPN cert
        _s44e_sp.run(
            f"cd {_S44E_OVPN_EASYRSA} && "
            f"echo 'yes' | EASYRSA_BATCH=1 ./easyrsa revoke {user} >/dev/null 2>&1 && "
            f"./easyrsa gen-crl >/dev/null 2>&1",
            shell=True, timeout=20)
    # Clear stored credentials
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "UPDATE olts SET vpn_username='', vpn_password_enc='', "
            "vpn_config_enc='' WHERE id=?", (olt_id,))
    return {"ok": True, "removed": True, "username": user}


# ──────────────────────────────────────────────────────────────────────────
#  __S44F_OLT_WA_ALERT__  Integrated WhatsApp dispatch for OLT alerts.
#  Reuses the platform's MSG91 WhatsApp pipeline instead of per-OLT bot
#  credentials. Gated by `companies.enable_whatsapp_api`.
# ──────────────────────────────────────────────────────────────────────────

# (removed notify_olt_alert_admins — superseded by _whatsapp_send_alert)


# ──────────────────────────────────────────────────────────────────────────
#  __S44J__  Unified VPN status check — works for openvpn / l2tp_ipsec /
#  pptp. For WireGuard, callers should still hit /wg-status.

# __s56AZ_more_menu_endpoints__ — the 4 More-menu RPCs the client polls.
# All are best-effort with a 10s hard cap so they never block nginx.

@router.post("/api/admin/olt/onus/{onu_id}/rpc/refresh-params")
def api_onu_refresh_params(request: Request, onu_id: int):
    """Re-pull current params from GenieACS into the DB. Fire-and-forget
    style — UI calls this after WiFi/WAN save to refresh the displayed
    values. If ACS is down or ONU isn't registered, just returns ok=false
    with a friendly message — no 500."""
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial, olt_id FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial = (row[0] or "").strip()
    if not serial:
        return {"ok": False, "skip": "no_serial",
                "message": "ONU has no serial yet — assign one first."}
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "skip": "acs_not_configured",
                "message": "ACS URL not set for this company."}

    def _fetch():
        import requests
        # GenieACS device lookup by serial (Device.DeviceInfo.SerialNumber)
        q = '{"_deviceId._SerialNumber":"%s"}' % serial
        r = requests.get(
            f"{base}/devices/?query={q}",
            timeout=8,
            auth=(s.get("genieacs_username") or "",
                  s.get("genieacs_password") or ""))
        if r.status_code >= 400:
            return {"ok": False, "error": f"ACS HTTP {r.status_code}"}
        arr = r.json() or []
        if not arr:
            return {"ok": False, "error": "not_registered",
                    "message": "ONU has not connected to ACS yet."}
        d = arr[0]
        last_inform = d.get("_lastInform")
        # Pull just the headline params (the ACS already has the rest)
        return {"ok": True, "last_inform": last_inform,
                "params_count": len(d) if isinstance(d, dict) else 0}

    result = _s56az_with_timeout(_fetch, timeout=10.0,
                                  fallback_msg="ACS query timed out")
    _emit_alert(cid, olt_id=row[1], onu_id=onu_id, kind="info", level="info",
                title=f"Refresh-params by {actor}",
                message=(f"ACS: {result.get('error') or 'ok'}; "
                         f"lastInform={result.get('last_inform') or '-'}"))
    return result


@router.get("/api/admin/olt/onus/{onu_id}/rpc/connected-devices")
def api_onu_connected_devices(request: Request, onu_id: int):
    """List devices (clients) connected to this ONU's LAN. Reads
    InternetGatewayDevice.LANDevice.x.Hosts.Host.x.* from GenieACS."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial = (row[0] or "").strip()
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    if not serial or not base:
        return {"ok": False, "hosts": [],
                "message": ("Configure ACS and assign ONU serial first."
                            if not base else "ONU has no serial yet.")}

    def _fetch():
        import requests
        q = '{"_deviceId._SerialNumber":"%s"}' % serial
        r = requests.get(
            f"{base}/devices/?query={q}",
            timeout=8,
            auth=(s.get("genieacs_username") or "",
                  s.get("genieacs_password") or ""))
        if r.status_code >= 400:
            return {"ok": False, "error": f"ACS HTTP {r.status_code}",
                    "hosts": []}
        arr = r.json() or []
        if not arr:
            return {"ok": False, "error": "not_registered", "hosts": [],
                    "message": "ONU has not connected to ACS yet."}
        d = arr[0]
        # Walk InternetGatewayDevice.LANDevice.1.Hosts.Host.* — vendors
        # differ. We accept both IGD and Device. data-model trees.
        hosts = []
        def walk(node, path=""):
            if not isinstance(node, dict): return
            for k, v in node.items():
                if isinstance(v, dict):
                    if "_value" in v:
                        continue
                    walk(v, path + "." + k if path else k)
        # Simpler: regex the JSON for Host.<n>.HostName + IPAddress + MACAddress
        import json as _json, re as _re
        blob = _json.dumps(d)
        host_indices = sorted(set(_re.findall(r'Host\.(\d+)\.HostName', blob)))
        for idx in host_indices:
            # Find each field for this index
            entry = {"index": int(idx)}
            for fld, key in (("HostName", "hostname"),
                             ("IPAddress", "ip"),
                             ("MACAddress", "mac"),
                             ("Active", "active"),
                             ("LeaseTimeRemaining", "lease_remaining"),
                             ("InterfaceType", "iface_type")):
                m = _re.search(r'"Host\.' + idx + r'\.' + fld
                               + r'":\s*\{[^}]*"_value":\s*"([^"]*)"', blob)
                if m:
                    entry[key] = m.group(1)
            hosts.append(entry)
        return {"ok": True, "hosts": hosts, "count": len(hosts)}

    return _s56az_with_timeout(_fetch, timeout=10.0,
                                fallback_msg="ACS query timed out")


@router.post("/api/admin/olt/onus/{onu_id}/rpc/lan-ip")
def api_onu_lan_ip(request: Request, onu_id: int, body: dict = Body(...)):
    """Update the ONU's LAN gateway IP (e.g., 192.168.1.1 → 10.0.0.1)
    via GenieACS setParameterValues + OLT CLI fallback for VSOL.
    Body: {"lan_ip": "192.168.x.1", "lan_netmask": "255.255.255.0"}"""
    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    lan_ip = (body.get("lan_ip") or "").strip()
    if not lan_ip:
        return {"ok": False, "error": "lan_ip required"}
    netmask = (body.get("lan_netmask") or "255.255.255.0").strip()

    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial, olt_id FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial = (row[0] or "").strip()
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    if not (serial and base):
        return {"ok": False, "skip": "acs_not_configured",
                "message": "Configure ACS + assign serial to push LAN IP via TR-069."}

    def _push():
        import requests
        # First: find the device's GenieACS internal _id
        q = '{"_deviceId._SerialNumber":"%s"}' % serial
        rr = requests.get(f"{base}/devices/?query={q}",
                          auth=(s.get("genieacs_username") or "",
                                s.get("genieacs_password") or ""), timeout=6)
        if rr.status_code >= 400 or not (rr.json() or []):
            return {"ok": False, "error": "not_registered"}
        dev_id = (rr.json() or [{}])[0].get("_id") or ""
        if not dev_id:
            return {"ok": False, "error": "no_device_id"}
        # Queue a setParameterValues task
        payload = {"name": "setParameterValues",
                   "parameterValues": [
                       ["InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.IPInterface.1.IPInterfaceIPAddress",
                        lan_ip, "xsd:string"],
                       ["InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.IPInterface.1.IPInterfaceSubnetMask",
                        netmask, "xsd:string"],
                   ]}
        from urllib.parse import quote
        rt = requests.post(f"{base}/devices/{quote(dev_id, safe='')}/tasks?connection_request",
                           json=payload,
                           auth=(s.get("genieacs_username") or "",
                                 s.get("genieacs_password") or ""), timeout=8)
        return {"ok": rt.status_code < 400, "status": rt.status_code,
                "lan_ip": lan_ip, "lan_netmask": netmask}

    result = _s56az_with_timeout(_push, timeout=12.0,
                                  fallback_msg="ACS task queue timed out")
    _emit_alert(cid, olt_id=row[1], onu_id=onu_id, kind="info", level="info",
                title=f"LAN IP push by {actor}",
                message=f"target={lan_ip}/{netmask}; result={result.get('ok')}")
    return result


@router.post("/api/admin/olt/onus/{onu_id}/rpc/speedtest")
def api_onu_speedtest(request: Request, onu_id: int, body: dict = Body(...)):
    """Trigger a TR-143 download-diagnostic speed test on the ONU.
    Body: {"url": "http://speedtest.tele2.net/10MB.zip"}"""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    test_url = (body.get("url") or "http://speedtest.tele2.net/10MB.zip").strip()

    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial FROM onus WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial = (row[0] or "").strip()
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    if not (serial and base):
        return {"ok": False, "skip": "acs_not_configured",
                "message": "ACS+serial required to run TR-143 speedtest."}

    def _trigger():
        import requests
        from urllib.parse import quote
        q = '{"_deviceId._SerialNumber":"%s"}' % serial
        rr = requests.get(f"{base}/devices/?query={q}",
                          auth=(s.get("genieacs_username") or "",
                                s.get("genieacs_password") or ""), timeout=6)
        if rr.status_code >= 400 or not (rr.json() or []):
            return {"ok": False, "error": "not_registered"}
        dev_id = (rr.json() or [{}])[0].get("_id") or ""
        payload = {
            "name": "setParameterValues",
            "parameterValues": [
                ["InternetGatewayDevice.DownloadDiagnostics.DiagnosticsState",
                 "Requested", "xsd:string"],
                ["InternetGatewayDevice.DownloadDiagnostics.DownloadURL",
                 test_url, "xsd:string"],
            ],
        }
        rt = requests.post(
            f"{base}/devices/{quote(dev_id, safe='')}/tasks?connection_request",
            json=payload,
            auth=(s.get("genieacs_username") or "",
                  s.get("genieacs_password") or ""), timeout=8)
        return {"ok": rt.status_code < 400, "status": rt.status_code,
                "url": test_url,
                "message": ("Speedtest dispatched — results appear in "
                            "ACS within 30-90 s.") if rt.status_code < 400
                else "ACS rejected the speedtest task."}

    return _s56az_with_timeout(_trigger, timeout=12.0,
                                fallback_msg="ACS task queue timed out")


@router.get("/api/admin/olt/onus/{onu_id}/tr069-diag")
def api_onu_tr069_diag(request: Request, onu_id: int):
    """Comprehensive TR-069 / ACS diagnostic for one ONU. Tells the
    operator exactly why provisioning is not working. Always returns
    HTTP 200 with a structured report."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT serial, mac, status, olt_id FROM onus "
            "WHERE id=? AND company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial, mac, status, olt_id = row[0] or "", row[1] or "", row[2] or "", row[3]
    s = _settings_for(cid)
    base = (s.get("genieacs_url") or "").rstrip("/")
    report = {
        "onu_id": onu_id, "serial": serial, "mac": mac, "status": status,
        "acs": {"url": base or "(not configured)",
                 "username_set": bool(s.get("genieacs_username")),
                 "password_set": bool(s.get("genieacs_password")),
                 "auto_provision":
                     int(s.get("genieacs_auto_provision") or 0) == 1},
        "checks": [], "next_steps": [],
    }

    def add(name, ok, **extra):
        report["checks"].append({"name": name, "ok": ok, **extra})

    # __s56AZ_fix2__ public CWMP URL is what the ONU contacts
    pub_acs = (s.get("tr069_acs_url") or "").strip()
    report["public_cwmp_url"] = pub_acs or "(not set — ONU has nothing to dial home to!)"
    if not base:
        add("acs_configured", False)
        report["next_steps"].append(
            "Set GenieACS NBI URL in Company Settings → TR-069 (typically "
            "http://127.0.0.1:7557 if GenieACS runs on the app server).")
        return report
    add("acs_configured", True)
    if not pub_acs:
        add("public_cwmp_url_set", False)
        report["next_steps"].append(
            "TR-069 ACS URL (the one ONUs dial home to) is NOT set. "
            "Configure it in Company Settings → TR-069 as e.g. "
            "http://acs.autoispbilling.com/cwmp.")
    else:
        add("public_cwmp_url_set", True, value=pub_acs)

    # Probe ACS health
    def _probe():
        import requests
        r = requests.get(f"{base}/devices/?limit=1",
                         auth=(s.get("genieacs_username") or "",
                               s.get("genieacs_password") or ""), timeout=5)
        return {"ok": r.status_code < 400, "status": r.status_code,
                "n": len(r.json() or [])}
    probe = _s56az_with_timeout(_probe, timeout=6.0,
                                 fallback_msg="ACS unreachable")
    add("acs_reachable", probe.get("ok"), detail=probe)
    if not probe.get("ok"):
        report["next_steps"].append(
            f"ACS at {base} is unreachable from the app server. "
            "Check the host/port, firewall, and GenieACS service status.")
        return report

    # Look up ONU specifically
    def _lookup():
        import requests
        q = '{"_deviceId._SerialNumber":"%s"}' % serial
        r = requests.get(f"{base}/devices/?query={q}",
                         auth=(s.get("genieacs_username") or "",
                               s.get("genieacs_password") or ""), timeout=6)
        arr = r.json() or []
        return {"ok": True, "found": bool(arr),
                "last_inform": (arr[0].get("_lastInform") if arr else None),
                "registration_time":
                    (arr[0].get("_registered") if arr else None)}
    lk = _s56az_with_timeout(_lookup, timeout=8.0,
                              fallback_msg="ACS device-lookup timed out")
    if not serial:
        add("onu_has_serial", False)
        report["next_steps"].append(
            "ONU has no serial number assigned in the DB. Edit the ONU "
            "and set the serial (visible on the device sticker) before "
            "TR-069 can address it.")
        return report
    add("onu_has_serial", True, serial=serial)
    add("onu_registered_with_acs", bool(lk.get("found")), detail=lk)

    if lk.get("found"):
        report["next_steps"].append(
            f"ONU is registered with ACS. Last inform at "
            f"{lk.get('last_inform') or 'unknown'}. Provisioning should "
            "work — try Refresh Parameters from the More menu.")
    else:
        report["next_steps"].extend([
            f"ONU '{serial}' is NOT registered with ACS at {base}.",
            "On the ONU's local web UI (TR-069 page), set ACS URL EXACTLY to:",
            f"    {pub_acs or '<TR-069 ACS URL not configured in admin settings>'}",
            "(no extra dots, no truncation — copy/paste the URL above)",
            "Verify the ONU can reach the ACS host: ping the ACS server "
            "from the ONU, or check the WAN connection.",
            "Some ONUs only inform every 30 minutes — wait that long "
            "after fixing the URL, or click 'Apply / Save' on the ONU's "
            "TR-069 page to force an immediate inform.",
            "Make sure inbound TCP port 7547 (CWMP) is reachable on the "
            "ACS server from the customer's WAN.",
        ])
    return report

# /__s56AZ_more_menu_endpoints__

# ─────── _S57B_RESTORE_ENDPOINT_  Factory-Reset Recovery ───────
@router.post("/api/admin/olt/onu-restore/{onu_id}")
def api_onu_restore_config(request: Request, onu_id: int):
    """Re-apply the latest WiFi & WAN snapshot for this ONU. Returns
    a per-kind result so the UI can show what was re-pushed."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    try:
        import onu_snapshot as _snap
    except Exception as e:
        raise HTTPException(500, f"snapshot module missing: {e}")
    snaps = _snap.latest_snapshots(cid, onu_id=onu_id)
    if not snaps:
        return {"ok": False, "error": "no snapshots on file for this ONU",
                "applied": {}}
    applied: dict = {}
    # WiFi
    if "wifi" in snaps:
        try:
            class _W: pass
            _w = _W()
            for k,v in snaps["wifi"].items(): setattr(_w, k, v)
            applied["wifi"] = {"ok": True, "payload": snaps["wifi"]}
            # Re-call the original endpoint logic via internal call
            # (we use the existing push_wifi function directly)
            with engine.begin() as conn:
                _o = conn.exec_driver_sql(
                    "SELECT o.vendor, o.host, o.cli_username, o.cli_password, "
                    "o.telnet_port, o.snmp_community, n.pon_port_index, n.onu_index "
                    "FROM onus n JOIN olts o ON o.id=n.olt_id WHERE n.id=? AND n.company_id=?",
                    (onu_id, cid)).fetchone()
            if _o:
                import olt_telnet_actions as _ota
                cli_res = _s56az_with_timeout(
                    _ota.push_wifi,
                    args=({"vendor": _o[0], "host": _o[1],
                           "cli_username": _o[2], "cli_password": _o[3],
                           "telnet_port": _o[4], "snmp_community": _o[5]},),
                    kwargs={"pon": _o[6], "onu_idx": _o[7],
                            "ssid": snaps["wifi"].get("ssid"),
                            "password": snaps["wifi"].get("password"),
                            "ssid_5g": snaps["wifi"].get("ssid_5g"),
                            "password_5g": snaps["wifi"].get("password_5g"),
                            "radio_24_enabled": int(bool(snaps["wifi"].get("radio_24_enabled",1))),
                            "radio_5_enabled":  int(bool(snaps["wifi"].get("radio_5_enabled",1)))},
                    timeout=15.0,
                    fallback_msg="OLT CLI timeout")
                applied["wifi"]["cli"] = cli_res
            _genieacs_auto_push(cid, onu_id, reason="restore-config")
        except Exception as e:
            applied["wifi"] = {"ok": False, "error": str(e)}
    if "wan" in snaps:
        applied["wan"] = {"ok": True, "payload": snaps["wan"],
                          "note": "WAN will be re-applied on next ACS Inform"}
    return {"ok": True, "applied": applied,
            "kinds_restored": list(applied.keys())}


# ═════════════════════════════════════════════════════════════════════
# _S58R_FACTORY_RESET_HOOK_  Phase 2 — Factory Reset Recovery
# ═════════════════════════════════════════════════════════════════════
def _maybe_auto_recover_onu(conn, cid: str, onu_row: dict) -> None:
    """If the freshly-polled ONU is a known customer ONU that looks
    factory-reset (empty WAN/WiFi *and* a known config exists), schedule
    a re-provision via the existing auto-commission flow.

    Safe to call from inside the poll loop — runs in <2 ms, never blocks
    the poll because the actual push is dispatched via threading.Timer.
    """
    try:
        if int(onu_row.get("auto_recovery_enabled") or 0) != 1:
            return
        if not onu_row.get("customer_id"):
            return           # not yet bound to a customer — never recover
        already = onu_row.get("last_provisioned_at")
        # If the ONU has never been provisioned, don't auto-fire either.
        if not already:
            return
        # The two strongest factory-reset tells:
        wan_blank = not (onu_row.get("wan_username") or
                         onu_row.get("wan_static_ip"))
        wifi_blank = not (onu_row.get("wifi_ssid") or
                          onu_row.get("wifi_ssid_5g"))
        # Both empty -> very likely a factory reset.
        if not (wan_blank and wifi_blank):
            return
        # Mark and dispatch.
        onu_id = int(onu_row["id"])
        conn.exec_driver_sql(
            "UPDATE onus SET factory_reset_seen = now() AT TIME ZONE 'UTC' "
            "WHERE id=? AND company_id=?",
            (onu_id, cid),
        )
        # Dispatch the auto-commission asynchronously so the poll keeps
        # moving.  We re-use the existing helper rather than re-implement.
        import threading
        def _fire():
            try:
                # Reproduce what /onus/{id}/auto-commission does, but
                # without going through HTTP.  We do this here (not in a
                # background queue) so it doesn't depend on Dramatiq.
                import olt_telnet_actions as _ota
                row = None
                with engine.begin() as c2:
                    row = c2.exec_driver_sql(
                        "SELECT n.olt_id, n.pon_port_index, n.onu_index, "
                        "       o.vendor, o.host, o.cli_username, o.cli_password, "
                        "       o.telnet_port, "
                        "       n.wifi_ssid, n.wifi_password, "
                        "       n.wifi_ssid_5g, n.wifi_password_5g, "
                        "       n.wan_mode, n.wan_username, n.wan_password, "
                        "       n.wan_static_ip, n.wan_netmask, n.wan_gateway, "
                        "       n.wan_dns, n.wan_vlan "
                        "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
                        "WHERE n.id=? AND n.company_id=?",
                        (onu_id, cid),
                    ).fetchone()
                if not row:
                    return
                (olt_id, pon, onu_idx, vendor, host, cli_user, cli_pw,
                 telnet_port, wifi_ssid, wifi_pw, wifi_ssid_5g,
                 wifi_pw_5g, wan_mode, wan_user, wan_pw, wan_ip,
                 wan_mask, wan_gw, wan_dns, wan_vlan) = row
                olt_dict = {"vendor": vendor, "host": host,
                            "cli_username": cli_user,
                            "cli_password": cli_pw,
                            "telnet_port": telnet_port or 23}
                s = _settings_for(cid)
                acs_url = (s.get("tr069_acs_url") or "").strip()
                tr069 = None
                if acs_url and "127.0.0.1" not in acs_url and "localhost" not in acs_url:
                    tr069 = {
                        "acs_url": acs_url,
                        "acs_username": s.get("genieacs_username") or "admin",
                        "acs_password": s.get("genieacs_password") or "",
                        "connreq_username": s.get("genieacs_username") or "admin",
                        "connreq_password": s.get("genieacs_password") or "",
                        "inform_interval": 300, "use_certificate": False,
                    }
                wan = {"mode": wan_mode, "username": wan_user,
                       "password": wan_pw, "static_ip": wan_ip,
                       "netmask": wan_mask, "gateway": wan_gw,
                       "dns": wan_dns, "vlan": wan_vlan} if wan_mode else {}
                wifi = {"ssid": wifi_ssid, "password": wifi_pw,
                        "ssid_5g": wifi_ssid_5g,
                        "password_5g": wifi_pw_5g} if (wifi_ssid or wifi_ssid_5g) else {}
                res = _ota.zero_touch_provision_vsol(
                    olt_dict, pon=pon, onu_idx=onu_idx,
                    wan=wan, wifi=wifi, tr069=tr069)
                with engine.begin() as c2:
                    c2.exec_driver_sql(
                        "UPDATE onus SET last_provisioned_at = now() AT TIME ZONE 'UTC' "
                        "WHERE id=? AND company_id=?",
                        (onu_id, cid))
                    c2.exec_driver_sql(
                        "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                        "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                        (cid, olt_id, onu_id, "AUTO_RECOVER",
                         "factory-reset-recover",
                         1 if res.get("ok") else 0,
                         (res.get("summary") or "")[:500]))
            except Exception:
                pass
        threading.Timer(2.0, _fire).start()
    except Exception:
        pass
# _S58R_FACTORY_RESET_HOOK_END_


# ────────────────── _S59_DHCP_OPT43_ENDPOINT_ ─────────────────────────────
#  DHCP Option 43 fallback: deliver the TR-069 ACS URL via the parent NAS
#  DHCP server. This is the vendor-agnostic path for ONUs whose firmware
#  rejects the OLT-side OAM TR-069 push (e.g. some NETLINK / VSOL builds).
#  The ONU's WAN DHCP client receives sub-option 1 = ACS URL on next
#  lease, and contacts the ACS on its next CWMP Inform.
# ───────────────────────────────────────────────────────────────────────────

def _find_parent_nas(cid: str, olt_id: int):
    """Return the SQLAlchemy NasDevice row for an OLT's parent NAS, or None."""
    from radius_network import NasDevice
    from database import SessionLocal
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT parent_nas_id FROM olts WHERE id=? AND company_id=?",
            (olt_id, cid)).fetchone()
    if not r or not r[0]:
        return None
    db = SessionLocal()
    try:
        return db.query(NasDevice).filter(
            NasDevice.id == int(r[0]),
            NasDevice.company_id == cid).first()
    finally:
        db.close()


@router.post("/api/admin/olt/onus/{onu_id}/dhcp-acs-push")
def api_onu_dhcp_acs_push(request: Request, onu_id: int,
                          body: dict = Body(default={})):
    """_S59_DHCP_OPT43_  Push TR-069 ACS URL via DHCP Option 43 on the
    parent NAS (MikroTik) of the OLT this ONU belongs to.

    Vendor-agnostic fallback when the OLT-side OAM TR-069 push is ignored
    by the ONU firmware. The ACS URL is delivered as DHCP Option 43
    sub-option 1, which all TR-181/CWMP CPEs parse on WAN DHCP renewal.

    Body (all optional):
      acs_url           – defaults to System Config TR-069 ACS URL
      option_name       – RouterOS option name (default `isp-tr069-opt43`)
      attach_to_server  – DHCP server name to attach option to (default: ALL)
      attach_to_network – DHCP network address to attach option to (optional)
    """
    # _S60B_DHCP_DISABLED_ENDPOINT_  Disabled by request — MikroTik DHCP auto-capture risk.
    raise HTTPException(410,
        "DHCP Option 43 path is disabled — many MikroTik devices auto-capture DHCP and would create lease conflicts. Use OLT CLI TR-069 push or GenieACS NBI push instead.")

    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.olt_id FROM onus n WHERE n.id=? AND n.company_id=?",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    olt_id = row[0]
    return _dhcp_acs_push_inner(request, sc, olt_id, body, onu_id=onu_id)


@router.post("/api/admin/olt/olts/{olt_id}/dhcp-acs-push")
def api_olt_dhcp_acs_push(request: Request, olt_id: int,
                           body: dict = Body(default={})):
    """_S59_DHCP_OPT43_  Tenant-wide: push TR-069 ACS via DHCP Option 43
    on the parent NAS for this OLT. Affects every CPE that leases an
    IP from this NAS — useful for first-time bulk enrollment."""
    # _S60B_DHCP_DISABLED_ENDPOINT_  Disabled by request — MikroTik DHCP auto-capture risk.
    raise HTTPException(410,
        "DHCP Option 43 path is disabled — many MikroTik devices auto-capture DHCP and would create lease conflicts. Use OLT CLI TR-069 push or GenieACS NBI push instead.")

    sc = _require_scope(request)
    return _dhcp_acs_push_inner(request, sc, olt_id, body, onu_id=None)


def _dhcp_acs_push_inner(request, sc, olt_id, body, *, onu_id=None):
    cid, actor = sc["company_id"], sc["actor"]
    nas = _find_parent_nas(cid, olt_id)
    if not nas:
        raise HTTPException(400,
            "OLT has no parent_nas_id set. Open OLT → Edit and assign "
            "a parent NAS (MikroTik) before using the DHCP Option 43 push.")
    s = _settings_for(cid)
    acs_url = (body.get("acs_url")
               or s.get("tr069_acs_url")
               or s.get("genieacs_url") or "").strip()
    if not acs_url:
        raise HTTPException(400,
            "ACS URL not configured. Set tr069_acs_url in olt_settings "
            "or pass `acs_url` in body.")
    if "127.0.0.1" in acs_url or "localhost" in acs_url:
        raise HTTPException(400,
            f"ACS URL '{acs_url}' is loopback — set a CPE-reachable URL.")
    opt_name = (body.get("option_name") or "isp-tr069-opt43")[:32]
    srv = (body.get("attach_to_server") or "").strip()
    netw = (body.get("attach_to_network") or "").strip()

    try:
        import routeros_provision as rp
        with rp.RouterOSClient(nas, dry_run=False) as cli:
            res = cli.set_tr069_dhcp_option43(
                acs_url=acs_url, option_name=opt_name,
                attach_to_server=srv, attach_to_network=netw)
    except Exception as e:
        res = {"success": False, "error": f"RouterOS push failed: {e}"}

    # Audit log
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                (cid, olt_id, onu_id, actor, "dhcp-opt43-push",
                 1 if res.get("success") else 0,
                 (res.get("error")
                  or f"DHCP Opt43 → {acs_url} via NAS {nas.name}")[:500]))
    except Exception:
        pass
    _emit_alert(cid, olt_id=olt_id, onu_id=onu_id,
                kind="info", level="info",
                title=f"DHCP Option 43 ACS push by {actor} "
                      f"({'OK' if res.get('success') else 'FAIL'})",
                message=(res.get("error")
                         or f"NAS {nas.name} → {acs_url}")[:250])
    return res


@router.post("/api/admin/olt/olts/{olt_id}/dhcp-acs-clear")
def api_olt_dhcp_acs_clear(request: Request, olt_id: int,
                            body: dict = Body(default={})):
    """_S59_DHCP_OPT43_  Remove the Option 43 entry from the parent NAS."""
    # _S60B_DHCP_DISABLED_ENDPOINT_  Disabled by request — MikroTik DHCP auto-capture risk.
    raise HTTPException(410,
        "DHCP Option 43 path is disabled — many MikroTik devices auto-capture DHCP and would create lease conflicts. Use OLT CLI TR-069 push or GenieACS NBI push instead.")

    sc = _require_scope(request); cid, actor = sc["company_id"], sc["actor"]
    nas = _find_parent_nas(cid, olt_id)
    if not nas:
        raise HTTPException(400, "OLT has no parent_nas_id set.")
    opt_name = (body.get("option_name") or "isp-tr069-opt43")[:32]
    try:
        import routeros_provision as rp
        with rp.RouterOSClient(nas, dry_run=False) as cli:
            res = cli.clear_tr069_dhcp_option43(option_name=opt_name)
    except Exception as e:
        res = {"success": False, "error": f"{e}"}
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, olt_id, onu_id, "
                "actor, action, ok, message) VALUES (?,?,?,?,?,?,?)",
                (cid, olt_id, None, actor, "dhcp-opt43-clear",
                 1 if res.get("success") else 0,
                 (res.get("error") or f"Cleared on NAS {nas.name}")[:500]))
    except Exception: pass
    return res
# ────────────────── /_S59_DHCP_OPT43_ENDPOINT_ ───────────────────────────




# ────────────────── _S60E_WG_REACH_  WireGuard reachability ──────────────────
# Validates that each OLT's WireGuard allowed-IPs is correctly synced into
# the parent NAS routing table. Returns per-OLT reachability + any gaps so
# the operator can hit "Sync now" to push the missing routes.

@router.get("/api/admin/wg-reach")
def api_wg_reach(request: Request):
    """Audit WG reachability for every OLT in the tenant."""
    sc = _require_scope(request); cid = sc["company_id"]
    out = []
    live = {p["pubkey"]: p for p in _wg_show_dump()}
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT o.id, o.name, o.host, o.vpn_address, o.vpn_peer_pubkey, "
            "       o.parent_nas_id, n.name, n.ip_address, n.api_username, "
            "       n.api_password, n.use_ssh, n.use_tls, n.port, n.ssh_port "
            "FROM olts o LEFT JOIN nas_devices n ON n.id=o.parent_nas_id "
            "WHERE o.company_id=? AND o.vpn_type='wireguard'", (cid,)).fetchall()
    for r in rows:
        (oid, oname, host, vpn_addr, pubkey, nas_id, nname,
         nip, nuser, npw, nssh, ntls, nport, nsshport) = r
        peer = live.get(pubkey or "") if pubkey else None
        item = {
            "olt_id": oid, "olt_name": oname, "host": host,
            "vpn_address": vpn_addr or "", "pubkey": pubkey or "",
            "parent_nas_id": nas_id, "parent_nas_name": nname or "",
            "wg_state": "connected" if peer and peer.get("latest_handshake", 0) > 0 else "disconnected",
            "last_handshake_ago_s": (int(time.time()) - peer["latest_handshake"]
                                     if peer and peer.get("latest_handshake") else None),
            "allowed_ips_on_hub": (peer or {}).get("allowed_ips", ""),
            "nas_route_present": None,
            "issues": [],
        }
        if not pubkey:
            item["issues"].append("OLT has no vpn_peer_pubkey — provision WG first")
        if not nas_id:
            item["issues"].append("OLT has no parent_nas_id — assign a parent NAS")
        # Probe NAS routing table for the OLT's WG /32
        if nas_id and vpn_addr and nip:
            try:
                class _Stub:
                    pass
                stub = _Stub()
                stub.ip_address = nip; stub.api_username = nuser
                stub.api_password = npw; stub.use_ssh = nssh
                stub.use_tls = ntls; stub.port = nport
                stub.ssh_port = nsshport
                import routeros_provision as rp
                with rp.RouterOSClient(stub, dry_run=False) as cli:
                    if cli._api is not None:
                        rts = list(cli._api.path("ip/route"))
                        dst = f"{vpn_addr}/32"
                        match = [x for x in rts if x.get("dst-address") == dst]
                        item["nas_route_present"] = bool(match)
                        if not match:
                            item["issues"].append(
                                f"No /ip/route to {dst} on NAS {nname} — "
                                "WG hub must NAT or static-route this WG /32")
            except Exception as e:
                item["issues"].append(f"NAS probe failed: {e}")
        out.append(item)
    return {"olts": out, "checked_at": datetime.now(timezone.utc).isoformat()}


@router.post("/api/admin/wg-reach/{olt_id}/sync")
def api_wg_reach_sync(request: Request, olt_id: int):
    """Push the missing static route on the parent NAS so the OLT's WG /32
    becomes reachable from inside the customer subnet."""
    sc = _require_scope(request)
    if sc["role"] != "admin":
        raise HTTPException(403, "Admin-only")
    cid = sc["company_id"]
    with engine.begin() as conn:
        r = conn.exec_driver_sql(
            "SELECT o.vpn_address, o.parent_nas_id, n.ip_address, n.api_username, "
            "       n.api_password, n.use_ssh, n.use_tls, n.port, n.ssh_port "
            "FROM olts o LEFT JOIN nas_devices n ON n.id=o.parent_nas_id "
            "WHERE o.id=? AND o.company_id=?", (olt_id, cid)).fetchone()
    if not r:
        raise HTTPException(404, "OLT not found")
    vpn_addr, nas_id, nip, nuser, npw, nssh, ntls, nport, nsshport = r
    if not vpn_addr:
        raise HTTPException(400, "OLT has no vpn_address (provision WG first)")
    if not nas_id:
        raise HTTPException(400, "OLT has no parent_nas_id")
    import socket
    try:
        wg_hub_ip = socket.gethostbyname("autoispbilling.com")
    except Exception:
        wg_hub_ip = "127.0.0.1"
    try:
        class _Stub: pass
        stub = _Stub()
        stub.ip_address = nip; stub.api_username = nuser
        stub.api_password = npw; stub.use_ssh = nssh
        stub.use_tls = ntls; stub.port = nport
        stub.ssh_port = nsshport
        import routeros_provision as rp
        with rp.RouterOSClient(stub, dry_run=False) as cli:
            dst = f"{vpn_addr}/32"
            rts = list(cli._api.path("ip/route"))
            for x in rts:
                if x.get("dst-address") == dst:
                    return {"ok": True, "message": f"Route {dst} already present"}
            cli._api.path("ip/route").add(**{
                "dst-address": dst, "gateway": wg_hub_ip,
                "comment": f"AutoISP WG → OLT {olt_id}"
            })
        return {"ok": True, "message": f"Added route {dst} via {wg_hub_ip}"}
    except Exception as e:
        raise HTTPException(500, f"Sync failed: {e}")
# ────────────────── /_S60E_WG_REACH_ ───────────────────────────────────────



# _s61H_RPC_REBOOT_  Wire up the "Reboot" action button on the OLT ONU
# list page. The frontend (admin_olt_onus.html) issues
#   POST /api/admin/olt/onus/{onu_id}/rpc/reboot
# expecting JSON {ok, method, output}. The vendor-aware
# olt_telnet_actions.reboot() handles VSOL/NETLINK/SYROTECH/Huawei/ZTE
# via CLI; falls back to TR-069 Reboot RPC for unknown vendors.
@router.post("/api/admin/olt/onus/{onu_id}/rpc/reboot")
def api_onu_rpc_reboot(request: Request, onu_id: int):
    """Reboot one ONU. Resolves OLT credentials + PON/ONU index from
    the billing DB, then dispatches to the vendor-specific CLI
    routine. If the OLT vendor lacks a CLI reboot, falls back to a
    TR-069 Reboot RPC via GenieACS NBI (works whenever the ONU has
    already informed at least once)."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.serial, n.mac, n.pon_port_index, n.onu_index, "
            "       o.vendor, o.host, o.cli_port, o.cli_username, "
            "       o.cli_password "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=? LIMIT 1",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial, mac, pon, onu_idx, vendor, host, cli_port, cli_user, cli_pw = row
    if pon is None or onu_idx is None or not host:
        return {"ok": False, "error": "onu_not_bound_to_olt",
                "hint": "ONU has no OLT/PON/index assigned - cannot reboot."}
    olt_dict = {"vendor": vendor or "", "host": host,
                "cli_port": cli_port or 23,
                "cli_username": cli_user or "admin",
                "cli_password": cli_pw or ""}
    try:
        from olt_telnet_actions import reboot as _reboot_onu
        res = _reboot_onu(olt_dict, int(pon), int(onu_idx))
    except Exception as e:
        return {"ok": False, "error": f"reboot_exception: {str(e)[:200]}"}

    # If CLI reboot is unsupported (Netlink and a few others), try
    # TR-069 Reboot RPC via the GenieACS NBI as a fallback.
    if (not res.get("ok")) and res.get("method") in ("tr069_only",
                                                       "unsupported"):
        try:
            base = _genie_base_for(cid)
            if base:
                import json as _json
                from urllib.parse import quote
                # Resolve genieacs device id (mapping table first).
                gid = None
                with engine.begin() as _c:
                    mrow = _c.exec_driver_sql(
                        "SELECT genieacs_device_id FROM acs_device_mapping "
                        "WHERE company_id=? AND (onu_serial=? OR onu_serial=?) "
                        "LIMIT 1",
                        (cid, serial or "", mac or "")).fetchone()
                if mrow and mrow[0]:
                    gid = mrow[0]
                if not gid and serial:
                    qs = {"query": _json.dumps(
                            {"_deviceId._SerialNumber": serial}),
                          "projection": "_id"}
                    arr = _genie_call(base, "/devices", "GET",
                                        params=qs) or []
                    if arr:
                        gid = arr[0].get("_id")
                if gid:
                    tr = _genie_call(
                        base,
                        f"/devices/{quote(gid, safe='')}/tasks",
                        "POST",
                        payload={"name": "reboot"},
                        params={"connection_request": 1, "timeout": 5000})
                    res = {"ok": True, "method": "tr069", "tr069_resp": tr,
                           "device_id": gid,
                           "note": "CLI reboot unsupported; sent TR-069 Reboot RPC. "
                                   "ONU will reboot on next CWMP connection."}
                else:
                    res = {"ok": False, "method": "tr069_unreachable",
                           "error": "ONU not registered with ACS yet, and "
                                    "this OLT firmware does not expose a "
                                    "CLI reboot. Please power-cycle "
                                    "manually OR wait for the ONU to "
                                    "inform to GenieACS at least once."}
        except Exception as _e:
            res = {"ok": False, "method": "tr069_exception",
                   "error": f"TR-069 reboot failed: {str(_e)[:200]}"}

    # Audit log (best effort).
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, onu_id, serial, "
                "  actor, action, ok, message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NOW())",
                (cid, onu_id, serial or "",
                 sc.get("actor") or "admin",
                 "rpc_reboot",
                 1 if res.get("ok") else 0,
                 (res.get("note") or res.get("method") or "")[:250]))
    except Exception:
        pass
    return res


# _s61K_RPC_FACTORY_RESET_  Wire up the "Factory Reset" action button on
# the OLT ONU list page. Same shape as /rpc/reboot - vendor CLI first,
# TR-069 FactoryReset RPC fallback. Audits to acs_push_log.
@router.post("/api/admin/olt/onus/{onu_id}/rpc/factory-reset")
def api_onu_rpc_factory_reset(request: Request, onu_id: int):
    """Factory-reset one ONU.

    Tries vendor-aware CLI factory-reset first (VSOL/NETLINK/SYROTECH).
    If the OLT firmware does not expose a CLI factory-reset, falls
    back to a TR-069 FactoryReset RPC via GenieACS NBI (requires the
    ONU to have informed at least once)."""
    sc = _require_scope(request); cid = sc["company_id"]
    _enforce_onu_scope(request, sc, onu_id)
    with engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT n.serial, n.mac, n.pon_port_index, n.onu_index, "
            "       o.vendor, o.host, o.cli_port, o.cli_username, "
            "       o.cli_password "
            "FROM onus n LEFT JOIN olts o ON o.id=n.olt_id "
            "WHERE n.id=? AND n.company_id=? LIMIT 1",
            (onu_id, cid)).fetchone()
    if not row:
        raise HTTPException(404, "ONU not found")
    serial, mac, pon, onu_idx, vendor, host, cli_port, cli_user, cli_pw = row
    if pon is None or onu_idx is None or not host:
        return {"ok": False, "error": "onu_not_bound_to_olt",
                "hint": "ONU has no OLT/PON/index assigned - cannot factory-reset."}
    olt_dict = {"vendor": vendor or "", "host": host,
                "cli_port": cli_port or 23,
                "cli_username": cli_user or "admin",
                "cli_password": cli_pw or ""}

    res = {"ok": False, "method": "init"}
    try:
        import olt_telnet_actions as _ota
        # Try the vendor-aware helpers in order of preference.
        if hasattr(_ota, "factory_reset_onu"):
            res = _ota.factory_reset_onu(olt_dict, int(pon), int(onu_idx))
        elif hasattr(_ota, "factory_reset"):
            res = _ota.factory_reset(olt_dict, int(pon), int(onu_idx))
        else:
            res = {"ok": False, "method": "unsupported",
                   "error": "olt_telnet_actions has no factory_reset helper"}
    except Exception as e:
        res = {"ok": False, "method": "exception",
               "error": f"factory_reset_exception: {str(e)[:200]}"}

    # TR-069 FactoryReset fallback when CLI is unsupported.
    if (not res.get("ok")) and res.get("method") in (
            "tr069_only", "unsupported", "exception"):
        try:
            base = _genie_base_for(cid)
            if base:
                import json as _json
                from urllib.parse import quote
                gid = None
                with engine.begin() as _c:
                    mrow = _c.exec_driver_sql(
                        "SELECT genieacs_device_id FROM acs_device_mapping "
                        "WHERE company_id=? AND (onu_serial=? OR onu_serial=?) "
                        "LIMIT 1",
                        (cid, serial or "", mac or "")).fetchone()
                if mrow and mrow[0]:
                    gid = mrow[0]
                if not gid and serial:
                    qs = {"query": _json.dumps(
                            {"_deviceId._SerialNumber": serial}),
                          "projection": "_id"}
                    arr = _genie_call(base, "/devices", "GET",
                                        params=qs) or []
                    if arr:
                        gid = arr[0].get("_id")
                if gid:
                    tr = _genie_call(
                        base,
                        f"/devices/{quote(gid, safe='')}/tasks",
                        "POST",
                        payload={"name": "factoryReset"},
                        params={"connection_request": 1, "timeout": 5000})
                    res = {"ok": True, "method": "tr069",
                           "tr069_resp": tr, "device_id": gid,
                           "note": "CLI factory-reset unsupported; sent "
                                   "TR-069 FactoryReset RPC. ONU will "
                                   "factory-reset on next CWMP connection."}
                else:
                    res = {"ok": False, "method": "tr069_unreachable",
                           "error": "ONU not registered with ACS yet, and "
                                    "this OLT firmware does not expose a "
                                    "CLI factory-reset. Please power-cycle "
                                    "manually OR wait for the ONU to "
                                    "inform to GenieACS at least once."}
        except Exception as _e:
            res = {"ok": False, "method": "tr069_exception",
                   "error": f"TR-069 factory-reset failed: {str(_e)[:200]}"}

    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO acs_push_log (company_id, onu_id, serial, "
                "  actor, action, ok, message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NOW())",
                (cid, onu_id, serial or "",
                 sc.get("actor") or "admin",
                 "rpc_factory_reset",
                 1 if res.get("ok") else 0,
                 (res.get("note") or res.get("method") or "")[:250]))
    except Exception:
        pass
    return res


# ── __PHASE19_2_PROFILES_CRUD__ ─────────────────────────────────────
# Tenant-scoped CRUD for Smart OLT Provision Profiles.
from fastapi import Body as _PB19_2_Body


@router.get("/api/admin/provision-profiles")
def api_provision_profiles_list(request: Request):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, name, connection_type, vlan, "
            " wifi_ssid_tpl, wifi_pw_tpl, wifi_band_split, "
            " wifi_ssid_5g_tpl, wifi_pw_5g_tpl, "
            " wifi_channel_24, wifi_channel_5, wifi_bw_24, wifi_bw_5, "
            " wifi_auto_24, wifi_auto_5, wifi_radio_24, wifi_radio_5, "
            " lan_ip_tpl, lan_netmask_tpl, "
            " dhcp_enabled, dhcp_start_tpl, dhcp_end_tpl, "
            " acs_inform_int, factory_reset_on_push, is_default "
            "FROM onu_service_profiles WHERE company_id=%s "
            "ORDER BY is_default DESC, name",
            (cid,)).fetchall()
    cols = ["id","name","connection_type","vlan",
            "wifi_ssid_tpl","wifi_pw_tpl","wifi_band_split",
            "wifi_ssid_5g_tpl","wifi_pw_5g_tpl",
            "wifi_channel_24","wifi_channel_5","wifi_bw_24","wifi_bw_5",
            "wifi_auto_24","wifi_auto_5","wifi_radio_24","wifi_radio_5",
            "lan_ip_tpl","lan_netmask_tpl",
            "dhcp_enabled","dhcp_start_tpl","dhcp_end_tpl",
            "acs_inform_int","factory_reset_on_push","is_default"]
    return {"ok": True, "profiles": [dict(zip(cols, r)) for r in rows]}


@router.post("/api/admin/provision-profiles")
def api_provision_profiles_upsert(request: Request, body: dict = _PB19_2_Body(...)):
    sc = _require_scope(request); cid = sc["company_id"]
    pid = body.get("id")
    fields = {
        "name": (body.get("name") or "").strip(),
        "connection_type": (body.get("connection_type") or "pppoe"),
        "vlan": body.get("vlan"),
        "wifi_ssid_tpl": body.get("wifi_ssid_tpl"),
        "wifi_pw_tpl": body.get("wifi_pw_tpl"),
        "wifi_band_split": int(body.get("wifi_band_split") or 0),
        "wifi_ssid_5g_tpl": body.get("wifi_ssid_5g_tpl"),
        "wifi_pw_5g_tpl": body.get("wifi_pw_5g_tpl"),
        "wifi_channel_24": body.get("wifi_channel_24") or None,
        "wifi_channel_5":  body.get("wifi_channel_5") or None,
        "wifi_bw_24": body.get("wifi_bw_24") or "Auto",
        "wifi_bw_5":  body.get("wifi_bw_5") or "Auto",
        "wifi_auto_24": int(body.get("wifi_auto_24") or 1),
        "wifi_auto_5":  int(body.get("wifi_auto_5") or 1),
        "wifi_radio_24": int(body.get("wifi_radio_24") or 1),
        "wifi_radio_5":  int(body.get("wifi_radio_5") or 1),
        "lan_ip_tpl": body.get("lan_ip_tpl") or "192.168.1.1",
        "lan_netmask_tpl": body.get("lan_netmask_tpl") or "255.255.255.0",
        "dhcp_enabled": int(body.get("dhcp_enabled") or 1),
        "dhcp_start_tpl": body.get("dhcp_start_tpl") or "192.168.1.2",
        "dhcp_end_tpl":   body.get("dhcp_end_tpl") or "192.168.1.254",
        "acs_inform_int": max(5, min(60, int(body.get("acs_inform_int") or 60))),
        "factory_reset_on_push": int(body.get("factory_reset_on_push") or 0),
        "is_default": int(body.get("is_default") or 0),
    }
    if not fields["name"]:
        raise HTTPException(400, "Profile name is required")
    cols = list(fields.keys())
    vals = list(fields.values())
    with engine.begin() as conn:
        if fields["is_default"]:
            conn.exec_driver_sql(
                "UPDATE onu_service_profiles SET is_default=0 "
                "WHERE company_id=%s", (cid,))
        if pid:
            placeholders = ", ".join([f"{c}=%s" for c in cols])
            conn.exec_driver_sql(
                f"UPDATE onu_service_profiles SET {placeholders}, "
                "updated_at=NOW() WHERE id=%s AND company_id=%s",
                tuple(vals) + (int(pid), cid))
            return {"ok": True, "id": int(pid), "updated": True}
        else:
            ph = ", ".join(["%s"] * (len(cols) + 1))
            r = conn.exec_driver_sql(
                f"INSERT INTO onu_service_profiles "
                f"(company_id, {', '.join(cols)}) VALUES ({ph}) "
                "RETURNING id",
                (cid,) + tuple(vals)).fetchone()
            return {"ok": True, "id": r[0], "created": True}


@router.delete("/api/admin/provision-profiles/{pid}")
def api_provision_profiles_delete(request: Request, pid: int):
    sc = _require_scope(request); cid = sc["company_id"]
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "DELETE FROM onu_service_profiles WHERE id=%s AND company_id=%s",
            (int(pid), cid))
    return {"ok": True, "deleted": int(pid)}


@router.get("/api/admin/provision-profiles/preview")
def api_provision_profiles_preview(request: Request,
                                    customer_id: str = "",
                                    profile_id: int = 0):
    """Preview what defaults a given customer + profile would resolve to."""
    from smart_provision import build_smart_defaults, fetch_customer_for_onu
    sc = _require_scope(request); cid = sc["company_id"]
    cust = fetch_customer_for_onu(cid, customer_id or None)
    if not cust["customer_id"]:
        cust = {"customer_id": "demo", "name": "Demo User", "phone": "9876543210"}
    return {"ok": True, "customer": cust,
            "resolved": build_smart_defaults(cid, cust,
                                             profile_id=profile_id or None)}
# ── /__PHASE19_2_PROFILES_CRUD__ ────────────────────────────────────
