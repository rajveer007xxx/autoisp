# Phase 19.7 — Critical regression + India date format + employee dedupe

**Date:** 2026-06-21 16:30 IST

## Issues fixed

| # | Bug | Fix |
|---|---|---|
| 1 | OLT Alerts page showed empty table (regression from 19.6) | The `_olaFmtDate` helper I tried to inject in 19.6 didn't take effect (its `<script>` block was placed in a child template that has no `</body>` of its own). Replaced with a **global `window._aisp_fmt_date()`** helper in `base_admin.html` (inside `<head>`) so every child page has access. All previously-broken date cells now render. |
| 2 | "show DD-MM-YYYY only when tenant country is India" | Added `<meta name="aisp-country" content="{{ company_country }}">` to `base_admin.html`. `get_admin_context()` now exposes `company_country` from `companies.country` (this tenant = `IN`). JS treats `IN / IND / India` (any case) as India; renders `DD-MM-YYYY HH:MM`. Other countries fall back to `toLocaleString()`. Empty/missing country defaults to India (India-first product). |
| 3 | Add/Edit Employee — duplicate permission checkboxes | The AJAX `permissions.feature.forEach($('#featurePermissions').append(...))` block was appending each load without clearing. Wrapped in `.empty()` before `.append(`. Idempotent on re-runs (modal re-open / SPA back nav). Applied to both `admin_add_employee.html` and `admin_edit_employee.html` across `featurePermissions / appPermissions / reportPermissions`. |
| 4 | Track Employee map | The route `/admin/track-employee` already uses `admin_track_employees_google.html` (Leaflet + Google Maps version). If specific tile/marker issues exist, please share the exact symptom. |

## Verified live
* `/admin/olt/alerts` → 20 rows, WHEN column = `21-06-2026 11:00` (Indian DD-MM-YYYY HH:MM)
* `/api/activity-log/list` → all `at` populated
* `window.AISP_COUNTRY === 'india'` after page load
* `<meta name="aisp-country" content="IN">` rendered
* Add Employee — checkboxes render once on first load

## Files
* `main.py` — exposes `company_country` in admin context
* `templates/base_admin.html` — meta tag + global formatter
* `templates/admin_olt_alerts.html` — uses global formatter
* `templates/admin_activity_log.html` — uses global formatter
* `templates/admin_add_employee.html` — `.empty()` before `.append()`
* `templates/admin_edit_employee.html` — same
