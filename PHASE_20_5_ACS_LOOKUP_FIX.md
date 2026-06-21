# Phase 20.5 - Fix "ONU not registered" false-negative on 5 RPC endpoints

## Bug
TR-069 Diagnostic, ACS Diagnostic, Refresh Parameters, Connected
Devices, Change LAN IP and Run Speedtest all reported
`not_registered` / "ONU has never sent a CWMP Inform" for ONU 2652
even though the device was actively informing GenieACS every 5 minutes
(`acs-live` worked, `device.inform_enable=true`,
`device_id=989DB2-SY%2DGPON%2D1110%2DWDONT-F3242989DB2CFD6FF`).

## Root cause (two layered bugs)

### Bug 1 - Wrong serial used for ACS lookup
DB stores `onus.serial = "98:9d:b2:cf:d6:ff"` (MAC format).
GenieACS stores `_deviceId._SerialNumber = "F3242989DB2CFD6FF"`
(NetLink vendor-prefixed format). The 5 RPC endpoints queried by raw
`onus.serial` and never matched, despite the correct
`genieacs_device_id` already living in `acs_device_mapping`.

### Bug 2 - URL double-encoding regression
GenieACS `_id` contains literal `%2D` characters. The endpoints
used `requests.get(f"{base}/devices/?query={q}")` which doesn't
re-encode `%` to `%25`, so the JSON `_id` query arrived at the
ACS as `...989DB2-SY-GPON-1110-WDONT-F3242989DB2CFD6FF` (`%2D`
decoded to `-`) and never matched the stored value.

## Fix

1. **Shared resolver** `_p205_resolve_acs_device(cid, onu_id)`:
   * First reads `acs_device_mapping` (most reliable - kept warm by
     ACS inform watcher + `_genieacs_auto_push` success path).
   * Falls back to fuzzy `_deviceId._SerialNumber` lookup with 6
     candidate forms (raw / no-colons / upper / lower / suffix-regex).
   * Persists any fresh discovery back into `acs_device_mapping` so
     subsequent calls are O(1).
   * Returns `(device_id, base_url, last_inform, hint)`.
2. **TR-069 Diagnostic** + **ACS Diagnostic** Check-4 rewritten to
   call the resolver directly.
3. **Refresh Parameters / Connected Devices / Change LAN IP /
   Run Speedtest** now run the resolver first; on success they
   short-circuit the broken serial query with a direct `_id` lookup
   that DOES match.
4. **URL encoding** - every `f"{base}/devices/?query={q}"`
   replaced with `requests.get(f"{base}/devices/", params={"query": q}, ...)`
   so `requests` correctly URL-encodes literal `%2D` characters.

## Verified on ONU 2652
- TR-069 Diagnostic: `onu_registered_with_acs.ok = true`,
  `last_inform = 2026-06-21T14:34:12`.
- ACS Diagnostic: "All checks pass - ONU should be visible."
  `id=989DB2-SY%2DGPON%2D1110%2DWDONT-F3242989DB2CFD6FF`.
- Refresh Parameters: `{ok: true, last_inform, params_count: 8}`.
- Connected Devices: `{ok: true, hosts: [], count: 0}` (bridge mode,
  no LAN clients - legit).

## Also clarified - 'saved profile' in Factory Reset confirmation
The text "the saved profile is pushed back" refers to the per-ONU
row in the `onus` table itself (`wifi_ssid`, `wifi_password`,
`wifi_ssid_5g`, `wifi_password_5g`, `wan_mode`, `wan_username`,
`wan_password`, `wan_vlan`, `wan_netmask`, `wan_gateway`,
`wan_dns`, etc.). After factory-reset, `api_onu_factory_reset_and_provision`
schedules a delayed (~90s) Zero-Touch Provision which re-reads those
columns and re-pushes them via OLT CLI + TR-069 ACS auto-fallback.
The values are persisted on every successful WiFi/WAN edit and on
every ZTP push.
