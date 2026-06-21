# Phase 19.1 — Access Request Logs newest-first sort bug

**Date:** 2026-06-21 12:09 IST

## Symptom
Even after Phase 19 fixed the FreeRADIUS post-auth query so MAC and NAS-IP
land in `radpostauth`, the Admin Portal → "Access Request Logs" page was
still showing legacy AccessRequestLog rows (e.g. `30-04-2026 16:41`) at
the top, while the *actually newest* RADIUS rows (`21-06-2026 12:05+`)
appeared **below** them — even though the user filter is `ORDER BY` newest.

## Root cause
In `radius_network.py :: page_access_logs()`:

1. `row["authdate"]` returned from
   `radpostauth_tenant.fetch_tenant_radpostauth()` is a real Python
   `datetime` (PG `timestamptz`), not a string. The code called
   `(authdate or "").split(".")[0]` on it, which raised
   `AttributeError`, and the `except` set `ts_obj = None` for every
   RADIUS row.
2. The merged-sort key returned `""` (empty string) for None
   timestamps; under `reverse=True` empty strings sort *last*. Hence
   RADIUS rows were always shoved to the bottom.

## Fix
- Accept `authdate` as either a `datetime` (PG path) or a string
  (legacy SQLite path) — no parse error.
- Replace the string-sort key with a normalized **float epoch seconds**
  key. Naive datetimes are treated as IST so they line up with the
  tz-aware PG values; all keys are converted to UTC before
  `.timestamp()` so the comparison is total and correct.

## Verified
After the fix, the access-logs page top 20 rows all show
`21-06-2026 12:05 → 12:09` (newest first) with full MAC + NAS-IP.

## Bonus
Removed stale `185.199.53.93` RADIUS entry on the reachable MikroTik
NAS (`/radius remove [find address=185.199.53.93]`). Only the working
`10.50.0.1` entry remains. NAS `10.50.128.2` will be cleaned the next
time it is reachable.
