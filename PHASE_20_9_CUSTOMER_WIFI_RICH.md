# Phase 20.9 - Customer Portal WiFi parity with Admin Edit Wi-Fi

## Goal
Bring the rich Wi-Fi toggle UX from the admin Edit Wi-Fi modal
(Phase 20.6) onto the customer self-service page at
`/customer/wifi-settings` so subscribers can:
- Turn each band's radio ON / OFF
- Pick Auto-Channel or a manual channel
- Pick Auto-Bandwidth or a manual bandwidth
- See changes propagate to GenieACS (TR-069) and the ONU itself

## DB
`ALTER TABLE onus ADD COLUMN IF NOT EXISTS`:
`wifi_channel`, `wifi_channel_5g`, `wifi_bandwidth`, `wifi_bandwidth_5g`,
`wifi_auto_channel`, `wifi_auto_channel_5g`, `wifi_auto_bandwidth`,
`wifi_auto_bandwidth_5g`.

## Backend (`phase25_customer.py`)
- `GET  /api/customer/me/wifi` now also returns the 8 rich-edit
  columns (channel/BW + auto flags for both bands).
- `POST /api/customer/me/wifi` accepts the new fields and persists
  them into the `onus` row. Manual channel/BW are cleared (NULL) when
  the corresponding Auto-toggle is ON.
- The existing `_genieacs_auto_push` reads back from the `onus` row,
  so TR-069 picks up the changes automatically. CLI push (and the
  Phase 20.3 CLI->ACS auto-fallback for V1600D firmwares) also reads
  from the same row, so a future admin push converges to the same
  customer-chosen values.

## Frontend (`customer_wifi_settings.html`)
- 2.4 GHz card gained: **Radio** switch + **Auto-Channel** switch +
  Channel select (1-13) + **Auto-Bandwidth** switch + Bandwidth
  select (20/40 MHz).
- 5 GHz card gained the same with Channel (36-165) and BW
  (20/40/80/160 MHz).
- All toggles have `data-testid` (`wf-radio-24`, `wf-auto-ch-24`,
  `wf-auto-bw-24`, `wf-ch-24`, `wf-bw-24`, and 5G equivalents).
- `_wfGrey(band)` greys the entire band when Radio is OFF; greys
  only the Channel/BW select when Auto-Channel/Auto-Bandwidth is ON.
- `saveWifi()` payload extended with all new fields.

## Files touched
- `/opt/ispbilling/admin-portal/phase25_customer.py`
- `/opt/ispbilling/admin-portal/templates/customer_wifi_settings.html`
- DB columns added live.
