# TR-069 + GenieACS — End-to-End Setup Guide

_Last refreshed: 2026-02-25 (Session S49)_

This guide walks every step from a bare VPS to a fully working TR-069 fleet with one-click reboot / Wi-Fi push / LAN host visibility / speed-test driven from the AutoISP Billing portal. It reflects the **current shipped state** of the platform (post-S49).

---

## 0. Architecture at a glance

```
┌──────────────┐     CWMP/HTTP                ┌────────────────┐    NBI/HTTP     ┌──────────────────┐
│  ONU/ONT     │ ───────── :7547 ───────────▶ │   GenieACS     │ ◀── :7557 ───── │  Admin Portal     │
│  (TR-069 on, │     periodic Inform           │  (cwmp + nbi +  │   (RPC + read)   │  FastAPI + Jinja  │
│   PPPoE WAN) │ ◀──── RPC reply ────────────  │   fs + ui)     │                  │  olt_routes.py    │
└──────────────┘                               └────────────────┘                  └──────────────────┘
       ▲                                              │                                    │
       │                                              ▼                                    ▼
   Wi-Fi push                                  MongoDB                                SQLite + telnet
   factoryReset                                                                      pool (s48) for OLT
   refreshObject                                                                     CLI fallback
   speedtest (TR-143)
```

Public endpoints already provisioned on the VPS (`185.199.53.93`):

| Service     | Internal           | Public                                 | Purpose                              |
|-------------|--------------------|----------------------------------------|--------------------------------------|
| CWMP        | `0.0.0.0:7547`     | `http://acs.autoispbilling.com/cwmp`   | ONUs `Inform` here every 5 min       |
| NBI         | `127.0.0.1:7557`   | _localhost-only_                       | Portal pushes tasks here             |
| File Server | `0.0.0.0:7567`     | `http://acs.autoispbilling.com/fs`     | Firmware/config downloads            |
| Web UI      | `127.0.0.1:3000`   | `https://acs.autoispbilling.com`       | Manual device inspection             |

---

## 1. Verify GenieACS is running on the VPS

GenieACS is **installed natively** (not Docker) as four systemd services. No re-install needed.

```bash
/app/vps.sh "systemctl status genieacs-cwmp genieacs-nbi genieacs-fs genieacs-ui --no-pager | head -20"
```

Expected: all four `active (running)`. If any is `inactive`:

```bash
/app/vps.sh "systemctl restart genieacs-cwmp genieacs-nbi genieacs-fs genieacs-ui"
```

Smoke-test NBI:

```bash
/app/vps.sh "curl -s http://127.0.0.1:7557/devices?projection=_id | head -c 200; echo"
```

If you get a JSON array (even `[]`), NBI is healthy.

---

## 2. Wire GenieACS into the portal

Both production companies are already configured (verified S47G). To re-verify:

```bash
/app/vps.sh "sqlite3 /var/lib/autoispbilling/autoispbilling.db 'SELECT company_id, genieacs_url, COALESCE(genieacs_username,\"\"), genieacs_auto_provision FROM olt_settings'"
```

Should print one row per company with `http://127.0.0.1:7557`. For a fresh tenant set it via UI: **Admin → OLT → System Config → GenieACS (TR-069) Config**:

| Field                        | Value                                   |
|------------------------------|-----------------------------------------|
| GenieACS NBI URL             | `http://127.0.0.1:7557`                 |
| NBI Username                 | _blank_ (no auth needed on loopback)    |
| NBI Password                 | _blank_                                 |
| Auto-push on Wi-Fi/WAN/Cust  | **Enabled**                             |

---

## 3. Enable TR-069 on every ONU (3 ways)

### 3.1 The recommended path: portal Bulk-Enable (SSE streaming)

Already shipped (S47). Go to **Admin → OLT → System Config → OLT row → "Bulk Enable TR-069"**. The platform telnets into the OLT, walks every ONU in `show onu auth-info`, and runs the vendor-specific TR-069 enable sequence per ONU, streaming live progress over SSE.

Backed by: `GET /api/admin/olt/olts/{olt_id}/tr069-bulk-enable` (SSE).

### 3.2 Manual single ONU (VSOL / Netlink / Syrotech EPON)

Use the persistent telnet pool from a Python shell — single login, one ONU:

```bash
/app/vps.sh "/opt/ispbilling/venv/bin/python -c '
import sys; sys.path.insert(0,\"/opt/ispbilling/admin-portal\")
from olt_telnet_pool import telnet_session
olt={\"host\":\"192.168.22.107\",\"cli_username\":\"admin\",
     \"cli_password\":\"Password@123\",\"telnet_port\":23,
     \"vendor\":\"netlink_epon\"}
with telnet_session(olt) as ts:
    ts.enter_pon(1)
    for cmd in (\"onu 9 pri tr069_mng acs http://acs.autoispbilling.com/cwmp\",
                \"onu 9 pri tr069_mng user genieacs\",
                \"onu 9 pri tr069_mng pwd genieacs-pwd\",
                \"onu 9 pri tr069_mng inform 300\",
                \"onu 9 pri tr069_mng enable\",
                \"onu 9 pri save_config\"):
        print(ts.send(cmd, wait=0.5, iters=3))
    ts.exit_config()
'"
```

### 3.3 Manual bulk script (legacy — only when SSE path is unavailable)

```bash
/app/vps.sh "/opt/ispbilling/venv/bin/python -c '
import sys; sys.path.insert(0,\"/opt/ispbilling/admin-portal\")
from olt_telnet_pool import telnet_session
ACS=\"http://acs.autoispbilling.com/cwmp\"
olt={\"host\":\"192.168.22.107\",\"cli_username\":\"admin\",
     \"cli_password\":\"Password@123\",\"telnet_port\":23,
     \"vendor\":\"netlink_epon\"}
with telnet_session(olt) as ts:
    for pon in range(1, 9):
        ts.enter_pon(pon)
        for onu_id in range(1, 64):
            for cmd in (f\"onu {onu_id} pri tr069_mng acs {ACS}\",
                        f\"onu {onu_id} pri tr069_mng user genieacs\",
                        f\"onu {onu_id} pri tr069_mng pwd genieacs-pwd\",
                        f\"onu {onu_id} pri tr069_mng inform 300\",
                        f\"onu {onu_id} pri tr069_mng enable\",
                        f\"onu {onu_id} pri save_config\"):
                ts.send(cmd, wait=0.3, iters=2)
    ts.exit_config()
'"
```

### 3.4 Per-vendor command reference

| Vendor               | Enable sequence                                                                                                          |
|----------------------|--------------------------------------------------------------------------------------------------------------------------|
| VSOL / Netlink EPON  | `onu N pri tr069_mng acs <URL>` / `user X` / `pwd Y` / `inform <sec>` / `enable` then `pri save_config`                  |
| Huawei MA56xx        | `interface gpon 0/<pon>` → `ont tr069-server <idx> 1 acs-url <URL> user X password Y inform-interval <sec>`              |
| ZTE C300/C320        | `pon-onu-mng gpon-onu_1/1/<pon>:<onu>` → `tr069-server acs-url <URL> username X password Y` then `tr069-server enable`   |
| Nokia 7360 ISAM      | `configure system management-config tr069 url <URL> username X password Y`                                               |

---

## 4. ONU-side requirements

Before TR-069 will actually `Inform`, make sure the ONU:

1. Has a **WAN with internet reachability** (PPPoE or DHCP). TR-069 talks over the WAN, not the OLT management VLAN.
2. Can resolve `acs.autoispbilling.com` (DNS) and reach port `80/7547` outbound. If the ONU is in a NAT'd LAN, the OLT-side WAN must allow outbound HTTP to the ACS IP.
3. Has TR-069 not blocked by a per-WAN ACL. On VSOL run `onu N pri tr069_mng show` to confirm `enable: yes` and the inform interval.

---

## 5. Verify the ONU appears in GenieACS

After enabling TR-069 the ONU sends its first `Inform` within `inform_interval` seconds. To shortcut:

```bash
/app/vps.sh "curl -s 'http://127.0.0.1:7557/devices?query=%7B%22_deviceId._SerialNumber%22%3A%22d0%3Ac9%3A01%3A04%3A59%3Aea%22%7D&projection=_id,_lastInform' | python3 -m json.tool | head -10"
```

(URL-encoded query: `{"_deviceId._SerialNumber":"d0:c9:01:04:59:ea"}`. Replace the MAC/serial with yours.)

If you see a device document with `_lastInform` set, the ONU is bound to GenieACS and **every portal RPC will work**.

You can also browse `https://acs.autoispbilling.com/devices` (Web UI) for a visual list and tap a device to see its full parameter tree.

---

## 6. Use the portal's one-click TR-069 actions

These are all live (shipped S48). They each try TR-069 first, then fall back to the OLT CLI when the vendor is VSOL family and the ONU isn't yet bound to GenieACS.

| UI control                                     | API endpoint                                              | TR-069 task                          | CLI fallback (VSOL)                                  |
|------------------------------------------------|-----------------------------------------------------------|--------------------------------------|------------------------------------------------------|
| ONU row → ⋮ → **Reboot**                       | `POST /api/admin/olt/onus/{id}/rpc/reboot`                | `reboot`                             | _not available on VSOL firmware_ → TR-069 only       |
| ONU row → ⋮ → **Factory Reset**                | `POST /api/admin/olt/onus/{id}/rpc/factory-reset`         | `factoryReset`                       | `onu N pri factory_reset`                            |
| ONU row → ⋮ → **Refresh Parameters**           | `POST /api/admin/olt/onus/{id}/rpc/refresh-params`        | `refreshObject InternetGatewayDevice.`| —                                                    |
| ONU row → ⋮ → **Connected Devices**            | `GET /api/admin/olt/onus/{id}/rpc/connected-devices`      | walk `LANDevice.1.Hosts.Host.*`      | —                                                    |
| ONU row → ⋮ → **Change LAN IP**                | `POST /api/admin/olt/onus/{id}/rpc/lan-ip`                | `setParameterValues` IPInterface     | `onu N pri lan ip X mask Y`                          |
| ONU row → ⋮ → **Run Speed Test**               | `POST /api/admin/olt/onus/{id}/rpc/speedtest`             | TR-143 `DownloadDiagnostics`         | —                                                    |
| ONU row → Wi-Fi modal → **Save & Push**        | `POST /api/admin/olt/onus/{id}/wifi`                      | SPV of `WLANConfiguration.*`         | `onu N pri wifi_ssid <slot> name X … shared_key Y`   |
| ONU row → WAN modal → **Save & Push**          | `POST /api/admin/olt/onus/{id}/wan`                       | SPV of `WANPPPConnection.*`          | `onu N pri wan_adv add route … commit`               |

Every save now **auto-fires `/rpc/refresh-params`** (S49) so the device tree in GenieACS is current within seconds, and any `cli_push.output` tail surfaces in the toast for full debug transparency.

---

## 7. Auto-push of customer changes

When a billing event mutates a customer (plan change, password reset, new PPPoE creds), the platform calls `_genieacs_auto_push` which:

1. Reads the linked ONU's `serial`, `wifi_ssid`, `wifi_password`, `wan_*`, customer PPPoE creds.
2. Builds the standard TR-098 parameter map.
3. POSTs a `setParameterValues` task to GenieACS for the resolved device `_id`.

Toggle per-tenant: **Admin → OLT → System Config → GenieACS → "Auto-push on customer change"**.

---

## 8. Troubleshooting

| Symptom                                                                | Likely cause                                              | Fix                                                                                                       |
|------------------------------------------------------------------------|-----------------------------------------------------------|-----------------------------------------------------------------------------------------------------------|
| `ONU '<serial>' not registered with GenieACS yet`                      | First Inform hasn't happened                              | Wait `inform_interval` or `onu N pri tr069_mng inform-now` from OLT, then retry.                          |
| ONU enabled but no Inform after 10 min                                 | WAN down / DNS / firewall                                 | `onu N pri ping acs.autoispbilling.com` from VSOL → must reply. Else check WAN PPPoE creds.               |
| Inform OK but RPCs time out                                            | Connection-request URL unreachable                        | GenieACS NBI uses CR to wake the device; if NAT-blocked, set `inform_interval=60` and rely on Informs.    |
| `device not registered` on `/rpc/connected-devices`                    | Same as #1                                                | Same as #1; also confirm `Hosts.Host` is exposed by the ONU's data-model (some bridges don't).            |
| Speed test set but no result                                           | TR-143 not implemented on the ONU                         | Call `/rpc/refresh-params` 30 s later; if `DownloadDiagnostics.DiagnosticsState` stays `None`, ONU lacks 143.|
| GenieACS NBI returns 401 from portal                                   | Username/password mismatch on the loopback                | Leave both blank in System Config when NBI is bound to `127.0.0.1` (default).                              |
| Multiple ONUs share the same serial in GenieACS                        | OUI-product-serial collision (vendor-specific quirk)      | Re-flash one ONU with a unique config-id, or set `_S40zd_` device-id resolver to match by `_id` prefix.    |
| `cli_push.output` shows "% Unknown command"                            | Vendor mismatch in `olts.vendor` column                   | Update `vendor` to one of `vsol`, `vsol_epon`, `netlink`, `netlink_epon`, `syrotech`, `syrotech_epon`…    |

### Live log inspection

```bash
# Portal-side
/app/vps.sh "journalctl -u isp-admin --since '10 min ago' -f | grep -iE 'genieacs|tr069|rpc'"

# GenieACS-side
/app/vps.sh "journalctl -u genieacs-cwmp -f"
```

---

## 9. Reference — TR-098 / TR-181 parameters you'll use the most

| Capability        | Parameter                                                                              |
|-------------------|----------------------------------------------------------------------------------------|
| 2.4 GHz SSID      | `InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID`                            |
| 2.4 GHz PSK       | `InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.KeyPassphrase`                   |
| 5 GHz SSID        | `InternetGatewayDevice.LANDevice.1.WLANConfiguration.5.SSID`                            |
| 5 GHz PSK         | `InternetGatewayDevice.LANDevice.1.WLANConfiguration.5.KeyPassphrase`                   |
| PPPoE Username    | `InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username`   |
| PPPoE Password    | `InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password`   |
| LAN gateway IP    | `InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.IPInterface.1.IPInterfaceIPAddress` |
| Uptime            | `InternetGatewayDevice.DeviceInfo.UpTime`                                               |
| Firmware version  | `InternetGatewayDevice.DeviceInfo.SoftwareVersion`                                      |
| RX optical        | `Device.Optical.Interface.1.OpticalSignalLevel` (TR-181, where supported)               |
| TX optical        | `Device.Optical.Interface.1.TransmitOpticalLevel`                                       |
| LAN host list     | `InternetGatewayDevice.LANDevice.1.Hosts.Host.{i}.HostName / MACAddress / IPAddress`    |

---

## 10. Quick reference — files / endpoints inside this codebase

| File                                              | Purpose                                                                       |
|---------------------------------------------------|-------------------------------------------------------------------------------|
| `admin-portal/olt_routes.py`                      | All `/api/admin/olt/...` and `/rpc/...` endpoints. `_genieacs_*` helpers.     |
| `admin-portal/olt_telnet_pool.py`                 | Persistent telnet sessions per OLT (S48).                                     |
| `admin-portal/olt_telnet_actions.py`              | VSOL/Huawei/ZTE/Nokia CLI write actions (rename / Wi-Fi / WAN / factory).     |
| `admin-portal/olt_vendors.py`                     | SNMP profiles + `poll_via_telnet` (pool-aware).                               |
| `admin-portal/templates/admin_olt_onus.html`      | ONU Manager UI — Wi-Fi/WAN modals + RPC menu + hosts/lanip/speed modals.      |
| `admin-portal/templates/admin_olt_detail.html`    | OLT detail page — Refresh Now button (S49).                                   |
| `admin-portal/templates/admin_olt_system.html`    | System Config — GenieACS URL, Auto-push toggle, OVPN/Wireguard add-OLT form. |

---

## 11. Sanity checklist before going live with a new tenant

- [ ] GenieACS systemd services all `active (running)` (`Step 1`).
- [ ] Loopback NBI reachable (`curl http://127.0.0.1:7557/devices?projection=_id` → `[]` or array).
- [ ] `olt_settings.genieacs_url='http://127.0.0.1:7557'` for the company.
- [ ] `olt_settings.genieacs_auto_provision=1`.
- [ ] OLTs added with correct `vendor` matching one of the supported names (case-sensitive).
- [ ] `telnet_interval_sec=60` for VSOL-family OLTs (set automatically S49 patch).
- [ ] Per-ONU TR-069 bulk-enable run (Section 3.1).
- [ ] First Inform observed in GenieACS UI within 5 min for ≥ 1 ONU.
- [ ] Manual `/rpc/refresh-params` returns `ok:true` for that ONU.
- [ ] Wi-Fi modal save → toast shows `CLI tail:` _and_ device tree updated within 30 s.

Once all 10 ticks pass, the tenant is fully operational on TR-069 — every reboot, factory reset, Wi-Fi push, speed test and LAN host enumeration becomes a one-click action.
