# Phase 19.6 — Cleanup + date NULL fixes + page hide

**Date:** 2026-06-21 16:10 IST

## What changed

### 1. Hidden legacy menu items
* **PON Tree** (`/admin/olt/pon-tree`) — superseded by **Smart Network**.
* **ZTP Profiles** (`/admin/olt/profiles`) — superseded by **Smart Provision Profiles**
  (`/api/admin/provision-profiles`).

The routes are kept (`{% if False %}` wraps the `<li>` only) so any
internal link still works; only the sidebar entries are removed.

### 2. DB defaults for `created_at` (fixes empty Date/Time column in UI)
| Table | Before | After |
|---|---|---|
| `admin_activity_log.created_at` | `DateTime` column with no DB-level default (Python model `default=datetime.utcnow` not always applied for fire-and-forget logger) → NULL on 7 rows | `ALTER ... SET DEFAULT now()` + NULL backfill |
| `olt_alerts.created_at` | TEXT column, no default → NULL/empty on 4 322 rows | `SET DEFAULT to_char(now() AT TIME ZONE 'Asia/Kolkata', ...)` + backfill |

Migration: `db/migrations/2026-06-21_phase19_6_date_defaults.sql`.

### 3. Frontend date formatter
JS in `admin_activity_log.html` was appending `Z` (UTC) to every string,
which broke tz-aware ISO strings (`+05:30`) → `Invalid Date` → renders
as empty cell.

Fix: only append `Z` when the string has NO tz info. Plus the same
helper `_olaFmtDate()` injected into `admin_olt_alerts.html`.

### 4. Cleanup of stale RADIUS server entry on MIKROTIK (10.50.128.2)
Attempted via RouterOS API — NAS still unreachable at this run.
Script is idempotent; will succeed automatically next time it runs.

## Verified
* `/api/activity-log/list` → `at` field now populated for every row.
* `/api/admin/olt/alerts` → `created_at` populated for every row.
* PON Tree + ZTP Profiles no longer in sidebar.
* `isp-admin`, `freeradius` healthy.
