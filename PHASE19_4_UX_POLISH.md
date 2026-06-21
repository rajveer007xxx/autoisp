# Phase 19.4 — Smart Provision UX polish

**Date:** 2026-06-21 15:30 IST

## Bugs reported by operator + fixes

| # | Bug | Fix |
|---|---|---|
| 1 | Cancelling the Smart Provision popup still left a "Running Zero-Touch Provision…" floating popup | `actZTP()` in `admin_onu_detail.html` no longer pre-shows a progress toast or `confirm()`; opens the modal directly. Modal's `hidden.bs.modal` handler also sweeps any leftover legacy toast that contains "Running Zero-Touch / Provisioning…" text. |
| 2 | OLT field in the modal showed nothing | `openSmartProvision()` now calls `GET /api/admin/olt/onus/{id}` whenever `label` / `olt_label` are not supplied, populating ONU serial + `OLT-name / PON / ONU-idx` automatically. Bound customer is also auto-prefilled when known. |
| 3 | No way to choose "auto-fetch from customer" vs "manual override" — wrong manual values could leak | New **WAN Source** dropdown at the top of the WAN tab. Default = `Auto-fetch from customer account (recommended)` → WAN fields locked read-only and re-fetched from `customers.*`. Switch to `Manual entry (override)` to edit. When `wan_source=auto`, server strips `wan_*` from the body so the customer record is always authoritative. |
| 4 | No DNS field for DHCP mode | DHCP sub-section added with a "DNS (comma sep — leave blank for ISP-assigned)" input. Routed to `wan_dns` payload key when mode=dhcp. |
| 5 | "Preview Smart Defaults" button gave no feedback | Button now shows a spinner and "Loading…" while in flight, then fires a green toast `Smart defaults loaded` on success (red toast on failure). |
| 6 | TR-069 ACS only reached the ONU in PPPoE mode | `_genieacs_auto_push()` now ALWAYS pushes `InternetGatewayDevice.ManagementServer.URL/Username/Password/PeriodicInformEnable/PeriodicInformInterval=60` (pulled from `superadmin_settings`) regardless of WAN mode, so static-IP, DHCP, and bridge ONUs still phone home to GenieACS for parameter pushes + drift correction. |

## Files
* `admin-portal/olt_routes.py`
* `admin-portal/templates/_smart_provision_modal.html`
* `admin-portal/templates/admin_onu_detail.html`

## Verified
* Modal opens with OLT field pre-filled: `OLT-49 / PON 1`
* WAN Source = Auto → all WAN fields disabled, PPPoE creds pre-pulled
  from customer (`mp.sehbaz.fibernet` / `sehbaz123`)
* WAN Source = Manual → all fields editable
* WAN Mode = DHCP → PPPoE section hidden, DHCP DNS field shown
* `freeradius`, `isp-admin` healthy
