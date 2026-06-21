# Phase 20.4 — Geo-fence Alerts for Employees

ZTP-style polygon-based geo-fencing with real-time breach detection
and dual-channel alerts (MSG91 WhatsApp + Telegram).

## DB schema (live PostgreSQL)
- `geofences` — polygon, employee_id, work_start/end, active flag.
- `geofence_breaches` — breach log with alerts_sent text trail.

## Backend (`geofences.py`)
- `GET    /api/admin/geofences`              — list polygons.
- `POST   /api/admin/geofences`              — create (name, polygon, employee_id, work hours).
- `DELETE /api/admin/geofences/{id}`         — soft-delete (active=0).
- `GET    /api/admin/geofences/breaches`     — last N breaches.
- `POST   /api/admin/geofences/run-check`    — manual scan (also used by cron).
- `run_geofence_check_for_company(cid)` — point-in-polygon scan with
  10-min duplicate suppression. Reads latest GPS from
  `employee_location_history` (last 5 min).

## Background worker
- `geofence_watcher.py` — runs every 60 s via systemd timer
  `isp-geofence-watcher.timer` (enabled + active).

## Alerts
- **Telegram** — re-uses `_telegram_send_alert(cid, title, message)`
  from `olt_routes.py`. Per-tenant `telegram_bot_token` +
  `telegram_admin_chat_id` from the `companies` row (env-var fallback).
- **MSG91 WhatsApp** — re-uses `_send_template()` from
  `msg91_whatsapp.py` with template `geofence_breach_alert_v1`
  (body vars: employee_name, zone_name, GPS, time). Sent to BOTH
  the admin's `company_phone` AND the employee's `mobile`.
- 10-minute dedup window per (company, employee, geofence).
- Only fires inside the zone's `work_start..work_end` window.

## UI (`templates/admin_track_employees.html`)
- Leaflet-Draw plugin loaded from CDN.
- Top toolbar: employee select, zone-name input, work-hours,
  "Draw Polygon" / "Zones (N)" / "Breaches (N)" buttons.
- Drawer panel: list of active zones (Focus / Delete buttons) and
  breach feed with timestamps, GPS coords, channels-alerted.
- All interactive elements carry `data-testid`.

## End-to-end test (verified)
1. Created zone "Tikamgarh Office Zone" bound to employee 9
   (Indresh Raikwar) with polygon `[24.74..24.76, 78.83..78.85]`.
2. Inserted fake GPS at `(20.0, 70.0)` — far outside polygon.
3. `POST /api/admin/geofences/run-check` →
   `{checked: 1, breaches: 1, alerts: 1}`.
4. Breach row recorded with
   `alerts_sent: "telegram,whatsapp:919826384268,whatsapp:917488824875"`.
5. Second run within 10 min → `breaches: 0` (dedup).
6. UI shows "Indresh Raikwar — Left zone: Tikamgarh Office Zone"
   with full GPS and alert channels.

## Files
- `/opt/ispbilling/admin-portal/geofences.py` (new module)
- `/opt/ispbilling/admin-portal/geofence_watcher.py` (cron worker)
- `/opt/ispbilling/admin-portal/main.py` (mount call)
- `/opt/ispbilling/admin-portal/templates/admin_track_employees.html`
- `/etc/systemd/system/isp-geofence-watcher.timer`
- `/etc/systemd/system/isp-geofence-watcher.service`

