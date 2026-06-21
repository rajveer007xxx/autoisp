# Phase 19.9 — Activity Log + Track Employee regression fixes

**Date:** 2026-06-21 17:55 IST

## Fixed

### 1. Activity Log showed "Loading…" forever (regression from 19.7)
Cause #1 — DataTables auto-init was hijacking the table tbody and
showing its own "No data" message before our AJAX loader populated rows.
**Fix:** added `class="no-dt" data-dt-skip="1"` to the table so the
global `S32 DataTables` auto-init skips it.

Cause #2 — my Phase 19.7 regex replace of `s36b8Fmt` left an orphan
`} catch(e){ return '—'; } }` block (the regex didn't consume the
whole multi-line function). The page-level script then threw
`Unexpected token 'catch'` and never executed `s36b8Load()`.
**Fix:** removed the orphan `catch` tail. Function is now a clean
one-liner: `function s36b8Fmt(s){ return (window._aisp_fmt_date ?
window._aisp_fmt_date(s) : (s||'—')); }`.

**Verified:** 15 rows render, WHEN column shows `21-06-2026 10:40`
(DD-MM-YYYY India format) ✅

### 2. Track Employee page stuck on "Loading…"
The Google-Maps-based template had a deep dependency on
`google.maps.Polyline`, `SymbolPath.FORWARD_CLOSED_ARROW`, etc. — my
Phase 19.8 Leaflet shim covered Map/Marker/InfoWindow but not those.

**Fix:** routed both `/admin/track-employee` and
`/employee/track-employee` to the pre-existing 100%-Leaflet template
`admin_track_employees.html` (the one that already uses
`L.map / L.tileLayer / L.marker` directly — no Google deps).

**Verified:** OpenStreetMap tiles render, employee list populates ✅

## Files
* `main.py` — 3 occurrences swapped to non-Google template
* `templates/admin_activity_log.html` — `no-dt` + clean `s36b8Fmt`
