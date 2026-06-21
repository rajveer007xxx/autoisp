# Phase 19.11 — WiFi `enable` keyword + Track Employee Hybrid Google Maps

**Date:** 2026-06-21 17:55 IST

## Fixed

### 1. ONU still showed WiFi 2.4G + SSID status = disable
Previous fix (Phase 19.8) sent `wifi_switch enable world-wide` (global
radio ON), but the per-SSID radio was still set to disable because the
`wifi_ssid <slot> name <SSID> hide disable ...` command **omits the
`enable` keyword** between `hide disable` and `auth_mode`.

Netlink V1600D inline-flags syntax (from the file header docstring):
`wifi_ssid <slot> name <SSID> hide disable enable wpa2psk <pwd>`.
The `enable` flag turns the per-SSID radio ON. Without it the SSID
broadcast is suppressed even when the global wifi_switch is on.

**Fix:** inserted `enable` in both call sites (`ssid+pw` branch and
`ssid-only` branch). Live-verified on ONU 2652:

```
onu 17 pri wifi_ssid 1 name RAJEEV-6699-2G hide disable enable auth_mode wpapsk/wpa2psk encrypt_type tkipaes shared_key RAJE6699 rekey_interval 86400
onu 17 pri wifi_ssid 5 name RAJEEV-6699-5G hide disable enable auth_mode wpapsk/wpa2psk encrypt_type tkipaes shared_key RAJE6699 rekey_interval 86400
onu 17 pri wifi_switch enable world-wide
```

All three commands now sent every Smart Provision push.

### 2. Track Employee map didn't match Network Map (no Hybrid)
Previous fix used Esri/OSM/CARTO tiles. Per request, switched to the
**exact same Google Maps tile URLs** the Network Map uses:

* **Hybrid** (`lyrs=y`) — default; satellite + place names + roads (matches Network Map)
* **Satellite** (`lyrs=s`) — pure imagery
* **Street** (`lyrs=m`) — standard map
* **Terrain** (`lyrs=p`) — physical features

Verified live: tiles load (e.g. `mt0/mt1/mt2/mt3.google.com`), labels in
local language (Hindi here), all 4 layer options in the picker.

## Files
* `olt_telnet_actions.py` — 2 wifi_ssid call sites patched
* `templates/admin_track_employees.html` — Google Maps tiles
