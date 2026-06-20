"""s61F_ZTP — Background GenieACS watcher worker (live param sync).

Runs every N minutes via systemd timer. Two responsibilities:

1. Scan GenieACS NBI for recently-informed devices, match them against
   ztp_onu_customer_mapping, and trigger ZTP config-push for new matches.

2. Pull LIVE parameter values (WiFi SSID, WAN status, WAN IP, PPPoE user,
   software/firmware, last inform time) from the device's GenieACS
   document into the `onus` billing table so the admin portal always
   reflects what the ONU actually has on it.

Safe to run concurrently with the live admin portal — uses the same DB
connection pool via olt_routes.engine.

Run with:
    cd /opt/ispbilling/admin-portal &&
    /opt/ispbilling/venv/bin/python3 ztp_watcher_cron.py
"""
import os, sys, time, json

# Load env vars from /etc/ispbilling.env BEFORE importing olt_routes
# (the SQLAlchemy engine is built at import-time from DATABASE_URL).
try:
    with open("/etc/ispbilling.env") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
except Exception:
    pass

sys.path.insert(0, '/opt/ispbilling/admin-portal')

_LOG = "/var/log/autoispbilling/ztp-watcher.log"
try:
    os.makedirs(os.path.dirname(_LOG), exist_ok=True)
except Exception:
    pass


def _log(msg: str) -> None:
    try:
        with open(_LOG, "a") as fh:
            fh.write(f"[{int(time.time())}] {msg}\n")
    except Exception:
        pass
    print(msg, flush=True)


# Projection used for the live-param sync pass. Keep this list small —
# every field is a separate index hit in GenieACS' MongoDB.
_LIVE_PROJECTION = ",".join([
    "_id", "_deviceId", "_lastInform", "_lastBoot",
    "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
    "InternetGatewayDevice.DeviceInfo.HardwareVersion",
    "InternetGatewayDevice.DeviceInfo.ModelName",
    "InternetGatewayDevice.DeviceInfo.Manufacturer",
    "InternetGatewayDevice.DeviceInfo.UpTime",
    "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID",
    "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Enable",
    "InternetGatewayDevice.LANDevice.1.WLANConfiguration.5.SSID",
    "InternetGatewayDevice.LANDevice.1.WLANConfiguration.5.Enable",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ConnectionStatus",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ExternalIPAddress",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ConnectionStatus",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.ExternalIPAddress",
])


def _dig(node, *path):
    """Safe nested-dict walker. Returns None if any link is missing."""
    cur = node
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    if isinstance(cur, dict) and "_value" in cur:
        return cur.get("_value")
    return cur


def _sync_live_params(eng, company_id: str, devices: list) -> int:
    """Push live ACS values into the onus table. Matches each GenieACS
    device by SerialNumber/MAC against onus.serial / onus.mac.

    Returns: number of onus rows updated.
    """
    if not devices:
        return 0
    updated = 0
    with eng.begin() as conn:
        for d in devices:
            di = d.get("_deviceId") or {}
            serial = (di.get("_SerialNumber") or "").strip()
            if not serial:
                continue
            igd = d.get("InternetGatewayDevice") or {}
            # Walk the well-known paths. _dig returns None gracefully.
            ssid24 = _dig(igd, "LANDevice", "1", "WLANConfiguration", "1", "SSID")
            ssid5  = _dig(igd, "LANDevice", "1", "WLANConfiguration", "5", "SSID")
            pppoe_user = _dig(igd, "WANDevice", "1", "WANConnectionDevice", "1",
                              "WANPPPConnection", "1", "Username")
            pppoe_status = _dig(igd, "WANDevice", "1", "WANConnectionDevice", "1",
                                "WANPPPConnection", "1", "ConnectionStatus")
            pppoe_ip = _dig(igd, "WANDevice", "1", "WANConnectionDevice", "1",
                            "WANPPPConnection", "1", "ExternalIPAddress")
            ipc_ip = _dig(igd, "WANDevice", "1", "WANConnectionDevice", "1",
                          "WANIPConnection", "1", "ExternalIPAddress")
            ipc_status = _dig(igd, "WANDevice", "1", "WANConnectionDevice", "1",
                              "WANIPConnection", "1", "ConnectionStatus")
            wan_ip = pppoe_ip or ipc_ip
            wan_status = pppoe_status or ipc_status
            sw_version = _dig(igd, "DeviceInfo", "SoftwareVersion")
            last_inform = d.get("_lastInform")

            # Skip rows where there's literally nothing to write — avoids
            # gratuitous UPDATEs that bump updated_at for no reason.
            if not any([ssid24, ssid5, pppoe_user, pppoe_status, pppoe_ip,
                        ipc_ip, ipc_status, sw_version, last_inform]):
                continue

            # Match by serial OR mac (V-SOL devices register their MAC as
            # serial in the billing system but the ONU serial in GenieACS).
            res = conn.exec_driver_sql(
                "UPDATE onus SET "
                "  wifi_ssid          = COALESCE(NULLIF(?,''), wifi_ssid), "
                "  wifi_ssid_5g       = COALESCE(NULLIF(?,''), wifi_ssid_5g), "
                "  wan_username       = COALESCE(NULLIF(?,''), wan_username), "
                "  wan_status         = COALESCE(NULLIF(?,''), wan_status), "
                "  wan_ip             = COALESCE(NULLIF(?,''), wan_ip), "
                "  firmware_version   = COALESCE(NULLIF(?,''), firmware_version), "
                "  last_acs_inform    = COALESCE(NULLIF(?,'')::timestamptz, last_acs_inform) "
                "WHERE company_id = ? AND ( "
                "  serial=? OR mac=? OR "
                "  REPLACE(UPPER(mac),':','') = UPPER(?) OR "
                "  UPPER(serial) LIKE ('%%' || UPPER(?) || '%%') OR "
                "  UPPER(?) LIKE ('%%' || UPPER(REPLACE(mac,':','')) || '%%') "
                ")",
                (ssid24 or "", ssid5 or "", pppoe_user or "",
                 str(wan_status or ""), wan_ip or "",
                 sw_version or "", last_inform or "",
                 str(company_id),
                 serial, serial, serial, serial, serial))
            if res.rowcount:
                updated += res.rowcount
    return updated


def main():
    from olt_routes import engine as eng
    from ztp_engine import ZTPEngine
    import urllib.request, urllib.parse, base64

    # Discover all tenants with a configured genieacs_url.
    with eng.begin() as conn:
        tenants = conn.exec_driver_sql(
            "SELECT DISTINCT ON (company_id) "
            "  company_id, genieacs_url, genieacs_username, "
            "  genieacs_password FROM olt_settings "
            "WHERE genieacs_url IS NOT NULL AND genieacs_url != '' "
            "ORDER BY company_id, ctid"
        ).fetchall()

    total_matched = total_seen = total_synced = 0
    for t in tenants:
        cid, url, user, pw = t
        url = (url or "").rstrip("/")
        if not url:
            continue
        auth_hdr = None
        if user:
            tok = base64.b64encode(
                f"{user}:{pw or ''}".encode()).decode()
            auth_hdr = "Basic " + tok

        # Pass 1: small projection just for ZTP matching (fast).
        try:
            req = urllib.request.Request(
                url + "/devices?projection=_id,_deviceId,_lastInform")
            if auth_hdr:
                req.add_header("Authorization", auth_hdr)
            with urllib.request.urlopen(req, timeout=15) as r:
                devices = json.loads(
                    r.read(2_000_000).decode("utf-8", "ignore"))
        except Exception as e:
            _log(f"cid={cid} NBI fetch (match-pass) failed: {e}")
            continue

        zte = ZTPEngine(cid, actor="watcher_cron")
        matched = 0
        for d in devices or []:
            di = d.get("_deviceId") or {}
            info = {
                "SerialNumber": di.get("_SerialNumber"),
                "ProductClass": di.get("_ProductClass"),
                "OUI": di.get("_OUI"),
                "Manufacturer": di.get("_Manufacturer"),
            }
            res = zte.acs_match_and_push(d.get("_id") or "", info)
            if res.get("matched"):
                matched += 1
        total_matched += matched
        total_seen += len(devices or [])

        # Pass 2: bigger projection for live param sync into onus table.
        try:
            req = urllib.request.Request(
                url + "/devices?projection=" +
                urllib.parse.quote(_LIVE_PROJECTION, safe=","))
            if auth_hdr:
                req.add_header("Authorization", auth_hdr)
            with urllib.request.urlopen(req, timeout=30) as r:
                full = json.loads(
                    r.read(5_000_000).decode("utf-8", "ignore"))
            n = _sync_live_params(eng, cid, full or [])
            total_synced += n
            _log(f"cid={cid} devices={len(devices or [])} matched={matched} live_synced={n}")
        except Exception as e:
            import traceback
            _log(f"cid={cid} NBI fetch (live-sync) failed: {e}\n{traceback.format_exc()}")

    _log(f"watcher run complete: total_seen={total_seen} "
         f"total_matched={total_matched} total_synced={total_synced}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log(f"watcher fatal: {e}")
        sys.exit(1)
