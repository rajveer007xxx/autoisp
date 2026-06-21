# Phase 20.1 — Device Configuration Tabs Overhaul (Live ONU reads)

## Goal
On the ONU detail page (`/admin/onu/{id}/detail`), the **WiFi**, **WAN** and
**ACS / TR-069** tabs should show **LIVE values** read straight from
GenieACS (the ONU's real-time CWMP parameters), not cached DB values.

## What changed

### Backend — `olt_routes.py`  /  `/api/admin/olt/onus/{onu_id}/acs-live`
1. **WiFi parser** now also pulls `KeyPassphrase` (and falls back to
   `PreSharedKey.1.KeyPassphrase` for vendors that nest it). Returned
   in `wifi[].password`.
2. **WAN parser** now extracts:
   - `SubnetMask` / `ExternalIPMask` → `wan[].netmask`
   - `DefaultGateway` / `DefaultIPGateway` → `wan[].gateway`
   - `DNSServers` / `DNSServerIPAddress` → `wan[].dns`
   - `MACAddress` → `wan[].mac`
3. **Persistence** to the `onus` table extended with `wifi_password`,
   `wifi_password_5g`, `wan_netmask`, `wan_gateway`, `wan_dns` (using
   `COALESCE(NULLIF(?,''), ...)` so empty live reads never overwrite
   good DB values).
4. Return JSON's `persisted` dict mirrors the new fields.

### Frontend — `templates/admin_onu_detail.html`
1. WiFi tab fields gained unique IDs:
   - `p20_wifi_ssid_24`, `p20_wifi_ssid_5`
   - `p20_wifi_pw_24`, `p20_wifi_pw_5` (with `data-pw` attribute and
     an **eye icon** for show/hide).
2. WAN tab fields gained unique IDs:
   - `p20_wan_user`, `p20_wan_pw` (with eye icon)
   - `p20_wan_ip`, `p20_wan_netmask`, `p20_wan_gateway`, `p20_wan_dns`,
     `p20_wan_status`, `p20_wan_mode`, `p20_wan_vlan`.
3. **"Sync from ONU"** buttons added on WiFi and WAN tabs — triggers
   `refresh=1` (connection-request).
4. **TR-069 Registered At** field gained `data-testid="tr069-registered-at"`.
5. **Overlay JS** auto-runs on `DOMContentLoaded`:
   - Calls `/api/admin/olt/onus/{ONU_ID}/acs-live?refresh=0` (cached,
     fast).
   - Patches the WiFi & WAN tab DOM with the returned live values.
   - Shows a green **`Live · HH:MM:SS`** badge above the tab strip.
6. `window.p20TogglePw(spanId, btn)` — eye icon handler.

## Files touched
- `/opt/ispbilling/admin-portal/olt_routes.py`
- `/opt/ispbilling/admin-portal/templates/admin_onu_detail.html`

## Testing (Playwright)
- ✅ ONU 2638 (offline) — WAN tab shows persisted netmask/gateway/DNS
  from previous successful read: `255.255.255.0 / 192.168.7.1 /
  8.8.8.8,4.2.2.2`.
- ✅ ONU 2652 (online) — WiFi tab shows live overlay:
  - SSID 5 GHz: `RAJEEV-6699-5G` (came from live ACS, DB was empty)
  - Password 2.4 GHz eye icon reveals `1215245454`.
- ✅ Green `Live · …` badge appears.
- ✅ Sync from ONU button present on both tabs.

