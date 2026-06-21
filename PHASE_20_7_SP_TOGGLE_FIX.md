# Phase 20.7 - Smart Provision WiFi Toggle Accessibility Fix

## User report
> "Smart OLT Provision popup still error for toggle switch for WIFI
> settings, plz fix very carefully again"

## Root analysis
Investigation via Playwright DOM diagnostics found the toggles were
*functionally* working - clicking the label fired the change event,
`_spApplyBandToggles()` greyed fields, state was correct. But:
1. The slider was only **36 x 18 px** - tiny, easy to mis-click.
2. There was **no visible ON/OFF label** so operators had no obvious
   confirmation that their click landed.
3. The state badge in the larger Edit Wi-Fi modal (Phase 20.6) was
   missing here, breaking visual parity.

## Fix
### Larger, accessible toggles (CSS override scoped to #smartProvisionModal)
- Width 54 px, height 26 px, dot 20 px (~2x bigger click target).
- Explicit `!important` overrides since the original `.switch` CSS
  lives in a non-scoped stylesheet.
- New `.sp-toggle-row` flex layout: switch + label + state badge.

### Live ON / OFF state badges
- Each of `sp_radio_24`, `sp_auto_24`, `sp_radio_5`, `sp_auto_5`
  now has a sibling `<span class="sp-toggle-state">ON</span>` badge.
- Green pill when checked (`#065f46` bg, `#a7f3d0` text).
- Red pill when un-checked (`#7f1d1d` bg, `#fecaca` text).
- `_spApplyBandToggles(band)` extended with `_setBadge()` that flips
  text + classes on every change.

## Verified via Playwright
- Initial: `radio24_badge=ON, auto24_badge=ON, radio24_size={w:43, h:21}`
  (~50% bigger click target vs old ~29x14).
- Click Radio toggle: `radio24_badge=OFF` (red), `ssid_dis=True`,
  `ch_dis=True`, `bw_dis=True` (entire band greyed).
- Click Auto-Channel toggle: `auto24_badge=OFF`, `ch_dis=False`
  (Manual Channel select unlocked).

## Files touched
- `/opt/ispbilling/admin-portal/templates/_smart_provision_modal.html`
