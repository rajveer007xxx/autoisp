# Phase 19.2 — Smart OLT Provision Popup (dual-band + LAN)

**Date:** 2026-06-21 14:40 IST

## Problem
The ZTP "Quick Provision" popup only collected `SSID` + `Password`. It
did not ask the operator for:
  * 5 GHz SSID/Password (dual-band)
  * Wi-Fi channel / bandwidth / radio toggles (per band)
  * LAN gateway IP / subnet / DHCP range
  * TR-069 inform interval (capped at 60 s)
  * Factory-reset-on-push toggle
Result: only the WAN PPPoE creds got pushed; everything else stayed
empty in the `onus` row and `_genieacs_auto_push()` had nothing to send
for those parameters. There was also no concept of a tenant-wide
"Provision Profile" so each bind required re-typing all the same data.

## Fix
### DB
Added LAN/DHCP columns to `onus`:
`lan_ip`, `lan_netmask`, `dhcp_enabled`, `dhcp_start`, `dhcp_end`,
`factory_reset_on_push`.

Extended `onu_service_profiles` to be a full Smart-OLT profile:
+ `wifi_ssid_5g_tpl`, `wifi_pw_5g_tpl`,
+ `wifi_channel_24/5`, `wifi_bw_24/5`,
+ `wifi_auto_24/5`, `wifi_radio_24/5`,
+ `lan_ip_tpl`, `lan_netmask_tpl`,
+ `dhcp_enabled`, `dhcp_start_tpl`, `dhcp_end_tpl`,
+ `factory_reset_on_push`, `is_default`,
+ CHECK constraint `acs_inform_int BETWEEN 5 AND 60`.

Seeded a tenant-wide `Residential Standard` profile for every company
with smart-default templates:
  * `wifi_ssid_tpl   = FIBERNET-{mobile_last4}-2G`
  * `wifi_ssid_5g_tpl= FIBERNET-{mobile_last4}-5G`
  * `wifi_pw_tpl     = {name_first4}{mobile_last4}` (mirrored on 5 GHz)
  * `lan_ip_tpl      = 192.168.1.1`
  * DHCP `192.168.1.2 - 192.168.1.254`
  * `acs_inform_int  = 60`

### Backend
* `smart_provision.py` — new module exposing
  `build_smart_defaults`, `fetch_customer_for_onu`,
  `merge_with_overrides`, `persist_to_onu`.
* `_genieacs_auto_push()` now also pushes
  `InternetGatewayDevice.LANDevice.1.LANHostConfigManagement.*`
  (gateway IP, subnet, DHCP server enable, MinAddress, MaxAddress).
* `POST /api/admin/olt/onus/{id}/zero-touch-provision` now:
  1. Builds smart defaults from customer + chosen profile
  2. Merges operator overrides from `body`
  3. Persists onto the ONU row
  4. Re-runs the existing OLT-CLI + GenieACS push path
* New CRUD: `GET /POST /DELETE /api/admin/provision-profiles`,
  `GET /api/admin/provision-profiles/preview?customer_id&profile_id`.
* Fixed a pre-existing `UnboundLocalError` where the function
  referenced `wan, wifi, tr069` before they were initialised.

### Frontend
* `templates/_smart_provision_modal.html` — reusable partial with
  4-tab popup (2.4 GHz, 5 GHz, LAN/DHCP, Advanced), profile selector,
  customer field, smart-defaults preview button, debounced live
  re-fetch, dark-theme styles.
* Included in `admin_unmapped_onus.html`, `admin_olt_onus.html`,
  `admin_onu_detail.html`. Legacy `openQuickProvision()` is kept as a
  bridge that calls `openSmartProvision()`.

## Verified
* `/api/admin/provision-profiles` returns the seeded `Residential Standard`.
* `/api/admin/provision-profiles/preview?customer_id=mp.sehbaz.fibernet`
  returns `wifi_ssid=FIBERNET-4477-2G`, `wifi_password=mpse4477`,
  `wifi_ssid_5g=FIBERNET-4477-5G`, `lan_ip=192.168.1.1`,
  `dhcp_start=192.168.1.2 → 192.168.1.254`,
  `inform_interval=60`.
* `POST /api/admin/olt/onus/2894/zero-touch-provision` with
  `{profile_id:36, customer_id:"mp.sehbaz.fibernet"}` persisted the
  resolved values into the `onus` row before the OLT-CLI push.
* Modal renders correctly in `admin_unmapped_onus.html` — all 4 tabs
  visible, fields populated, profile pre-selected.

## Migrate
```bash
psql -d autoispbilling -f db/migrations/2026-06-21_phase19_2_smart_olt_provision.sql
systemctl restart isp-admin
```
