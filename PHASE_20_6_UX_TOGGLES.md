# Phase 20.6 - UX Toggle Fixes Batch

User-reported on screenshots:
1. "Edit Wi-Fi" Quick-Action popup only had SSID/PW + Radio dropdown -
   needed proper Radio/Auto-Channel/Auto-Bandwidth toggles with greying.
2. Smart OLT Provision: toggles weren't greying their inputs; DNS field
   could not be edited because WAN-Source defaulted to Auto.
3. Geo-fence creation impossible because the Track-Employees map kept
   auto-zooming back via fitBounds() every 10 seconds.

## Fixes

### 1. Edit Wi-Fi modal (`admin_onu_detail.html`)
- Replaced single-page form with 2.4 GHz / 5 GHz tabs (plain JS swap,
  no Bootstrap-4-vs-5 dependency).
- Each band has:
  - Radio switch (ON/OFF) with live label
  - Auto-Channel switch + Manual Channel select (greyed when Auto on)
  - Auto-Bandwidth switch + Manual Bandwidth select (greyed when Auto on)
- Channel ranges: 1-13 (2.4 GHz), 36-165 (5 GHz).
- Bandwidth: 20/40 MHz (2.4 GHz); 20/40/80/160 MHz (5 GHz).
- When Radio is OFF, ALL SSID/PW/Channel/BW inputs grey out and the
  payload sent to the server omits them (only radio_*_enabled=0 fires).
- All elements have `data-testid` attributes.

### 2. Smart OLT Provision modal (`_smart_provision_modal.html`)
- `_applyWanSource()` rewritten: in Auto mode, every WAN field gets
  disabled EXCEPT `sp_wan_dns` and `sp_wan_dns_dhcp`. Operators
  routinely need to pin upstream DNS (8.8.8.8 / 1.1.1.1) per ONU
  without flipping the whole WAN to manual.
- New `_spApplyBandToggles(band)` handler: when Radio toggle is OFF,
  all per-band SSID/PW/Channel/BW inputs grey out. When Auto-Channel
  is ON the Manual Channel select is greyed (still tied to Radio).
- Band-Split toggle now visibly toggles the 5-GHz cred panel and
  mirrors the 2.4 G credentials into 5 G when off.
- `shown.bs.modal` hook applies all toggles on every modal open.

### 3. Track Employees map (`admin_track_employees.html`)
- Added 3 flags: `_p206_userMoved`, `_p206_drawing`,
  `_p206_didInitialFit`.
- `fitBounds()` in `updateMap()` now only fires when ALL of:
  * `_p206_didInitialFit === false` (i.e. first paint only)
  * `_p206_userMoved === false` (no manual pan/zoom)
  * `_p206_drawing === false` (not in middle of polygon draw)
- `dragend`/`zoomend` on the map sets `_p206_userMoved = true`
  (with a 1.2 s post-init cooldown so the initial fitBounds doesn't
  set the flag).
- Leaflet-Draw `draw:drawstart` / `draw:drawstop` / `draw:created`
  toggle `_p206_drawing`.

## Verified end-to-end via Playwright
- Edit Wi-Fi: Radio OFF -> ssid24_disabled=True, ch24_disabled=True, bw24_disabled=True.
  Auto-Ch OFF -> ch_disabled=False (manual channel enabled).
- Smart Provision: WAN AUTO -> wan_dns_dis=False, wan_user_dis=True.
  Wi-Fi Radio OFF -> ssid/ch/bw all disabled. Auto-Ch OFF -> ch enabled.
- Track Employees: `didInitialFit=true` after first paint; subsequent
  10-second refreshes do not call fitBounds.
