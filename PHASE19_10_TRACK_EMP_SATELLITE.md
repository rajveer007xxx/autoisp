# Phase 19.10 — Track Employee: satellite view + employees load

**Date:** 2026-06-21 18:10 IST

## Fixed
1. **Employees stuck at "Loading…"** — the frontend was calling
   `/api/employees/locations` which mis-routed to
   `/api/employees/{employee_id}` (treating "locations" as int → 422).
   The correct endpoint is `/api/employee-locations` (hyphenated).
   Fixed in one line.

2. **No satellite view** — added 4 base layers via Leaflet's standard
   layer-control widget (top-right), matching the OLT Network Map:
   * **Map** (OpenStreetMap) — default
   * **Satellite** (Esri World Imagery)
   * **Hybrid** (Esri Imagery + place labels overlay)
   * **Dark** (CARTO Dark Matter)
   No API keys, no quotas, no paid SDK.

## Verified
* Track Employee page loads `FIB22622529` in the sidebar with location.
* Marker drawn on map; layer picker offers 4 options; click cycles tiles.
