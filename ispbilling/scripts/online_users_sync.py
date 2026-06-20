#!/usr/bin/env python3
"""Standalone online-users sync — talks to MikroTik directly, no HTTP loopback.

This script REPLACES the previous /tmp/online_users_sync.py that hit the
admin portal's /api/online-users/sync-from-nas endpoint via HTTP.

Why this matters for perf:
  - The admin portal runs ONE uvicorn worker.
  - The MikroTik probe per tenant takes ~60 seconds (162 PPPoE actives +
    162 hotspot actives + bytes-in/out enrichment).
  - The 90 s timer calling the loopback HTTP endpoint locked the only
    admin worker for 60 of every 90 seconds. Users hitting any page
    during that window queue up — perceived 40-80 s lag.
  - This script runs OUT OF THE ADMIN WORKER PROCESS (via systemd), so
    the admin worker stays free to serve user pages.

Process:
  - For each Active NAS in nas_devices, talk to MikroTik /ppp/active
    + /ip/hotspot/active directly via librouteros (in-process here).
  - Upsert online_users rows.
  - Skip if a sync started in the last 60 s (avoid pile-up).
"""
import os, sys, sqlite3, time, traceback

# Use the admin portal's vendored libs.
sys.path.insert(0, "/opt/ispbilling/admin-portal")

DB = "/var/lib/autoispbilling/autoispbilling.db"
LOCK_FILE = "/run/isp-online-sync.lock"
LOCK_TTL_S = 75   # don't allow a second sync to start within 75 s

# ────────── lock ──────────
def _acquire_lock():
    try:
        if os.path.exists(LOCK_FILE):
            mtime = os.stat(LOCK_FILE).st_mtime
            if time.time() - mtime < LOCK_TTL_S:
                print(f"[skip] another sync in progress (lock {int(time.time()-mtime)}s old)")
                return False
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        print(f"[warn] lock: {e}")
        return True  # fail-open
def _release_lock():
    try: os.unlink(LOCK_FILE)
    except Exception: pass

# ────────── DB helpers ──────────
def _conn():
    # _S58AD_PG_CONN_  Use db_compat-wrapped connection so we write to
    # the SAME database (Postgres) the web portal reads from. The old
    # sqlite3.connect bypass left the migrated Postgres online_users
    # table permanently empty, breaking the dashboard / online list.
    import sys as _sys
    _sys.path.insert(0, '/opt/ispbilling')
    _sys.path.insert(0, '/opt/ispbilling/admin-portal')
    from db_compat import get_raw_conn as _g
    c = _g(timeout=30)
    try:
        c.row_factory = sqlite3.Row
    except Exception:
        pass
    return c

def _now_utc_iso():
    # _S58AG_TZ_  Return a timezone-aware UTC ISO string so Postgres
    # TIMESTAMPTZ columns parse the offset correctly. Previously the
    # naive utcnow() string was interpreted in the server's IST locale
    # and stored 5.5h in the past, breaking every freshness check.
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(sep=" ")

# ────────── MikroTik helpers ──────────
def _parse_uptime(s):
    import re
    if not s: return 0
    total = 0
    for n, u in re.findall(r"(\d+)([dhmsw])", str(s)):
        total += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1, "w": 604800}[u]
    return total

def _sync_one_nas(con, nas_row):
    """nas_row: tuple (id, company_id, name, ip_address, api_user, api_password, api_port, api_use_tls)"""
    nas_id, company_id, name, ip, user, password, port, use_tls = nas_row
    if not ip or not user:
        return 0, "missing credentials"

    # Direct librouteros connection (faster than going through RouterOSClient
    # which imports the full admin portal stack).
    import librouteros, ssl
    kwargs = dict(host=ip, username=user, password=password or "",
                  port=int(port or 8728), timeout=20)
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            kwargs["ssl_wrapper"] = ctx.wrap_socket
        api = librouteros.connect(**kwargs)
    except Exception as e:
        return 0, f"connect: {e}"

    seen = set()
    cnt = 0
    try:
        # Bulk fetch /queue/simple once — bytes counters live here, not in /ppp/active.
        # Queue names follow `<pppoe-USERNAME>` for PPPoE sessions (auto-created
        # by the PPP server) and we map them by stripping the prefix.
        queue_bytes = {}   # username -> (bytes_in_upload, bytes_out_download)
        # __VLAN_LIVE__: build vlan_iface_name -> vlan_id from /interface/vlan
        vlan_map = {}
        try:
            for v in api.path("/interface/vlan"):
                _vn = (v.get("name") or "").strip()
                try:
                    _vid = int(v.get("vlan-id") or 0)
                except Exception:
                    _vid = 0
                if _vn and _vid:
                    vlan_map[_vn] = _vid
        except Exception:
            vlan_map = {}
        # __VLAN_LIVE__: build user -> parent_iface from /interface/pppoe-server (dynamic actives)
        pppoe_iface_by_user = {}
        try:
            for s in api.path("/interface/pppoe-server"):
                _u = (s.get("user") or "").strip()
                _pif = (s.get("interface") or "").strip()
                if _u and _pif:
                    pppoe_iface_by_user[_u] = _pif
        except Exception:
            pppoe_iface_by_user = {}
        try:
            for q in api.path("/queue/simple"):
                qn = (q.get("name") or "").strip()
                bf = (q.get("bytes") or "").strip()
                if not qn or not bf:
                    continue
                # Extract username from <pppoe-USERNAME>
                m = None
                if qn.startswith("<pppoe-") and qn.endswith(">"):
                    m = qn[7:-1]
                elif qn.startswith("pppoe-"):
                    m = qn[6:]
                if not m:
                    continue
                # bytes is "upload/download" -> from the user's perspective:
                #   upload = bytes_in (sent BY user), download = bytes_out (received BY user)
                try:
                    u, d = bf.split("/", 1)
                    # Store as MB (historical convention for online_users.bytes_*)
                    queue_bytes[m] = (int(u or 0) / 1048576.0,
                                      int(d or 0) / 1048576.0)
                except Exception:
                    continue
        except Exception:
            queue_bytes = {}

        # PPPoE active
        try:
            pppoe = list(api.path("/ppp/active"))
        except Exception:
            pppoe = []
        for row in pppoe:
            uname = (row.get("name") or "").strip()
            if not uname:
                continue
            seen.add(uname)
            ip_addr = row.get("address") or ""
            mac = row.get("caller-id") or row.get("mac-address") or ""
            uptime_s = _parse_uptime(row.get("uptime") or "")
            b_in, b_out = queue_bytes.get(uname, (0, 0))
            # __VLAN_LIVE__: derive live VLAN parent iface & id for this session
            _live_iface = pppoe_iface_by_user.get(uname, "") or ""
            _live_vid = vlan_map.get(_live_iface) if _live_iface else None
            # Customer status: only Active -> Online, others -> WalledGarden
            crow = con.execute(
                "SELECT status FROM customers WHERE company_id=? AND username=?",
                (company_id, uname)).fetchone()
            cust_status = (crow[0] if crow else "Active") or "Active"
            ou_status = "Online" if cust_status.strip().lower() == "active" else "WalledGarden"
            now_utc = _now_utc_iso()
            from datetime import datetime, timedelta, timezone
            started_at = (datetime.now(timezone.utc) - timedelta(seconds=uptime_s)
                          ).isoformat(sep=" ") if uptime_s > 0 else now_utc
            # Upsert
            cur = con.execute(
                "UPDATE online_users SET ip_address=?, mac_address=?, nas_ip=?, "
                "  uptime_seconds=?, started_at=?, status=?, updated_at=?, "
                "  bytes_in=?, bytes_out=?, "
                "  live_vlan_id=?, live_vlan_iface=?, "
                "  framed_protocol='PPPoE' "
                " WHERE company_id=? AND username=?",
                (ip_addr, mac, ip, uptime_s, started_at, ou_status, now_utc,
                 b_in, b_out, _live_vid, _live_iface, company_id, uname))
            if cur.rowcount == 0:
                con.execute(
                    "INSERT OR REPLACE INTO online_users (company_id, username, ip_address, "
                    " mac_address, nas_ip, uptime_seconds, started_at, status, "
                    " updated_at, framed_protocol, bytes_in, bytes_out, "
                    " live_vlan_id, live_vlan_iface) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (company_id, uname, ip_addr, mac, ip, uptime_s, started_at,
                     ou_status, now_utc, "PPPoE", b_in, b_out,
                     _live_vid, _live_iface))
            cnt += 1

        # Hotspot active
        try:
            hs = list(api.path("/ip/hotspot/active"))
        except Exception:
            hs = []
        for row in hs:
            uname = (row.get("user") or row.get("name") or "").strip()
            if not uname:
                continue
            seen.add(uname)
            ip_addr = row.get("address") or ""
            mac = row.get("mac-address") or row.get("caller-id") or ""
            uptime_s = _parse_uptime(row.get("uptime") or "")
            # Hotspot bytes are in the row itself — convert bytes -> MB
            try: b_in  = float(row.get("bytes-in")  or row.get("bytes_in")  or 0) / 1048576.0
            except Exception: b_in = 0
            try: b_out = float(row.get("bytes-out") or row.get("bytes_out") or 0) / 1048576.0
            except Exception: b_out = 0
            crow = con.execute(
                "SELECT status FROM customers WHERE company_id=? AND username=?",
                (company_id, uname)).fetchone()
            cust_status = (crow[0] if crow else "Active") or "Active"
            ou_status = "Online" if cust_status.strip().lower() == "active" else "WalledGarden"
            now_utc = _now_utc_iso()
            from datetime import datetime, timedelta, timezone
            started_at = (datetime.now(timezone.utc) - timedelta(seconds=uptime_s)
                          ).isoformat(sep=" ") if uptime_s > 0 else now_utc
            cur = con.execute(
                "UPDATE online_users SET ip_address=?, mac_address=?, nas_ip=?, "
                "  uptime_seconds=?, started_at=?, status=?, updated_at=?, "
                "  bytes_in=?, bytes_out=?, "
                "  framed_protocol='Hotspot' "
                " WHERE company_id=? AND username=?",
                (ip_addr, mac, ip, uptime_s, started_at, ou_status, now_utc,
                 b_in, b_out, company_id, uname))
            if cur.rowcount == 0:
                con.execute(
                    "INSERT OR REPLACE INTO online_users (company_id, username, ip_address, "
                    " mac_address, nas_ip, uptime_seconds, started_at, status, "
                    " updated_at, framed_protocol, bytes_in, bytes_out) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (company_id, uname, ip_addr, mac, ip, uptime_s, started_at,
                     ou_status, now_utc, "Hotspot", b_in, b_out))
            cnt += 1
    finally:
        try: api.close()
        except Exception: pass

    # Mark Offline anything previously Online for this NAS that wasn't seen
    if seen:
        ph = ",".join(["?"] * len(seen))
        con.execute(
            f"UPDATE online_users SET status='Offline', updated_at=? "
            f" WHERE company_id=? AND nas_ip=? AND username NOT IN ({ph}) "
            f"   AND LOWER(IFNULL(status,''))='online'",
            [_now_utc_iso(), company_id, ip] + list(seen))
    return cnt, None


def main():
    if not _acquire_lock():
        return 0
    t_start = time.time()
    try:
        con = _conn()
        nases = con.execute(
            "SELECT id, company_id, name, ip_address, "
            "       IFNULL(api_username,'admin'), IFNULL(api_password,''), "
            "       IFNULL(port,8728), IFNULL(use_tls,0) "
            "  FROM nas_devices "
            " WHERE LOWER(IFNULL(status,''))='active'"
        ).fetchall()
        total = 0
        per = []
        for nas in nases:
            try:
                n, err = _sync_one_nas(con, nas)
                total += n
                per.append((nas[2] or f"#{nas[0]}", n, err))
            except Exception as e:
                per.append((nas[2] or f"#{nas[0]}", 0, str(e)))
        # _S58AF_AUTO_REBIND_  For any company that has EXACTLY ONE
        # Active NAS, rebind orphan online_users rows (whose nas_ip no
        # longer maps to any Active NAS) to that single Active NAS.
        # When there are multiple Active NAS rows we leave it alone so
        # admin/SLCO can manually rebind individual users.
        try:
            con.execute("""
                UPDATE online_users ou
                   SET nas_ip = nd.ip_address
                  FROM (SELECT company_id, MAX(ip_address) AS ip_address
                          FROM nas_devices WHERE status='Active'
                         GROUP BY company_id HAVING COUNT(*) = 1) nd
                 WHERE ou.company_id = nd.company_id
                   AND ou.nas_ip NOT IN (
                       SELECT ip_address FROM nas_devices
                        WHERE company_id = ou.company_id AND status='Active')
            """)
        except Exception as _e:
            print(f'[warn] auto-rebind: {_e!r}')
        purged = 0
        try:
            con.commit()
        except Exception:
            pass
        con.close()
        elapsed = time.time() - t_start
        print(f"[ok] synced={total} in {elapsed:.1f}s "
              f"nases={per} purged={purged}")
    except Exception:
        traceback.print_exc()
    finally:
        _release_lock()


if __name__ == "__main__":
    sys.exit(main())
