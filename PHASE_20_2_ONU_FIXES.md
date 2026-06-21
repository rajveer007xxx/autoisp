# Phase 20.2 — Critical ONU UX Fixes

User-reported issues from ONU 2652 (RAJEEV TRADERS):

## Issue 1 — Stale 2.4 GHz WiFi SSID in portal
- Portal showed `WIF2026147` for 2.4 G even though the Netlink ONU's
  built-in GUI showed the new name `RAJEEV-6699-2G` (post-ZTP push).
- Root cause: After the OLT CLI push, the ONU's WiFi was still
  **disabled** (see Issue 2 below), so the ONU never re-informed
  GenieACS with the new SSID. GenieACS kept the pre-push cache.

### Fix
- After every successful ZTP push (`/api/admin/olt/onus/{id}/zero-touch-provision`),
  spawn a daemon thread that:
  1. Resolves the GenieACS device `_id` (from `acs_device_mapping`
     or by serial lookup),
  2. Queues a `refreshObject InternetGatewayDevice` task with
     `connection_request=1` so ACS pings the ONU and pulls fresh
     parameters within ~5 s.
- Fire-and-forget; never blocks the ZTP response.

## Issue 2 — WiFi still disabled after Smart OLT Provision push
- Netlink V1600D ONU GUI showed `WiFi-2.4G Status: disable` AND
  per-SSID `WiFi Status: disable` after a fresh push, even though the
  ZTP form had radio_24_enabled=1.

### Root cause
- The `wifi_switch enable world-wide` master command was sent, but
  on V1600D firmware ≥ 3.1 it only flips an umbrella flag. The
  per-band toggles (2.4 GHz / 5 GHz) and the per-SSID state stay
  `disable` unless explicitly flipped.

### Fix (`olt_telnet_actions.py`)
In both `_vsol_wifi_one()` and `push_wifi()` we now send a wider
enable sequence (each variant is independent; the firmware silently
rejects ones it doesn't recognise):

```
onu N pri wifi_ssid <slot> enable
onu N pri wifi_ssid <slot> state enable
onu N pri wifi_switch enable world-wide          (existing)
onu N pri wifi_switch 2g enable world-wide       (NEW)
onu N pri wifi_switch 5g enable world-wide       (NEW)
onu N pri wifi 2g enable                         (NEW)
onu N pri wifi 5g enable                         (NEW)
```

## Issue 3 — TR-069 ENABLED column showing "NO"
- Even when the ONU was actively informing every 300 s, the TR-069
  tab said `TR-069 ENABLED: NO`.
- Root cause: the column `onus.tr069_enabled` simply didn't exist in
  the schema — every read returned `NULL` → Jinja rendered `NO`.

### Fix
- DB migration: `ALTER TABLE onus ADD COLUMN tr069_enabled SMALLINT
  DEFAULT 0, tr069_registered_at TIMESTAMPTZ;`
- Backfill: set `tr069_enabled=1` for every ONU with an
  `acs_device_mapping` row OR a non-NULL `last_acs_inform`.
- Live overlay (`/api/admin/olt/onus/{id}/acs-live`) now also
  persists `tr069_enabled = 1` when `PeriodicInformEnable` is `true`
  OR when a GenieACS device_id was resolved.
- Frontend overlay JS sets `p20_tr069_enabled` to `YES` (green) when
  the live response confirms TR-069 is on.

## Files touched
- `/opt/ispbilling/admin-portal/olt_routes.py`
- `/opt/ispbilling/admin-portal/olt_telnet_actions.py`
- `/opt/ispbilling/admin-portal/templates/admin_onu_detail.html`
- DB columns added live on PostgreSQL.

## Verified
- ONU 2652 now has `tr069_enabled=1`, `tr069_registered_at=2026-06-17`.
- `/api/.../acs-live` returns `device.inform_enable: true` for 2652.
- Overlay JS sets TR-069 ENABLED = YES on page load.
- Phase 20.2 enable variants will fire on next ZTP push attempt.

