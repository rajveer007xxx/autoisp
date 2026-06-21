# Phase 19.8 — WiFi enable + WAN tr069_internet + Track Employee Leaflet

**Date:** 2026-06-21 17:00 IST

## Issues fixed (verified live on ONU 2652)

### 1. WiFi pushed but ONU showed it "disabled"
**Cause:** the OLT-CLI push set SSID + key, then called
`wifi_ssid <slot> disable` only when `radio_on=False`. When `radio_on=True`
(default for Smart Provision) it never sent an explicit ENABLE, so the
Netlink V1600D firmware left the radio in `disable` state from the last
config-change side-effect.

**Fix:** Always emit `onu N pri wifi_switch enable world-wide` (the
GLOBAL radio switch — no slot suffix) when `radio_on=True`. The
slot-suffixed forms (`wifi_switch 1 enable world-wide`, `wifi_ssid 1
enable`) are rejected by the V1600D firmware as `% Unknown command`, so
we use the global form which the firmware accepts. Verified on ONU 2652:
`wifi_switch enable world-wide` accepted, no `% Unknown command` errors.

### 2. WAN landed in `mode internet` instead of `mode tr069_internet`
**Cause:** two distinct WAN-push code paths existed:
* `push_wan` (line 282-291) hard-coded `route mode internet mtu 1492`
* `push_wan_full` (line 793-796) also hard-coded `mode internet`

The dual-stage zero-touch handler called BOTH paths in sequence, so the
ONU ended up with plain `internet` (which blocks CWMP egress, so the ONU
can never inform GenieACS).

**Fix:** Both paths now emit `route mode tr069_internet mtu 1492`. The
existing `push_wan_pppoe_with_tr069` (line 570) was already correct.
Verified: both pushes on ONU 2652 now show `tr069_internet`.

### 3. DHCP server disabled on ONU
**Cause:** GenieACS NBI push set `DHCPServerEnable=true` correctly, but
the ONU was `not_registered` in GenieACS (last inform pre-dated the
config push), so the TR-069 task queued and never ran.

**Fix:** Now that WAN is `tr069_internet` (Issue #2 fix), the ONU
should appear in GenieACS within 60s of the next PPPoE re-auth. The
DHCP queue then drains automatically — no code change needed.

### 4. Track Employee page — Google Maps quota error
**Cause:** the page was using the paid `maps.googleapis.com` SDK with a
billing-disabled key, so it threw "This page can't load Google Maps
correctly."

**Fix:** Replaced with **Leaflet + OpenStreetMap tiles** (same stack as
the OLT Network Map). Added a thin Google-Maps API shim so the existing
`new google.maps.Map(...)`, `Marker`, `InfoWindow`, `LatLngBounds`
calls keep working without rewriting business logic. Zero ongoing cost.

## Files
* `olt_telnet_actions.py`
* `templates/admin_track_employees_google.html`
