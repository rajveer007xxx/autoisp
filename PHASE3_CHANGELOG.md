# Phase 3 — SQLite Removal & PostgreSQL Cutover (2026-06-21)

## What Changed
- Data divergence reconciled: 2 SQLite-only invoices (#471 SA-INV-000030, #472 SA-INV-000031) backfilled to PostgreSQL.
- `nas_health_pulse.py` migrated from raw `sqlite3.connect()` to `db_compat.get_raw_conn()` (PG via DATABASE_URL).
- `cron/sla_cron.py` migrated to db_compat; fixed offset-aware datetime handling.
- `cron/outage_cron.py` migrated to db_compat with PG-flavoured SQL (STRING_AGG, INTERVAL).
- `cron/backup_cron.py` rewritten — produces `pg_dump --format=custom` files instead of SQLite snapshots.
- All `/etc/systemd/system/isp-{nas-pulse,sla,outage,db-backup}.service` units switched from `/usr/bin/python3` to `/opt/ispbilling/venv/bin/python`.
- SQLite database file (`/var/lib/autoispbilling/autoispbilling.db`) made **read-only** (`chmod 0444`) — kept on disk for 30-day rollback insurance.
- All 6 `db_compat.py` copies (root + 5 portals) now **refuse to start** if `DATABASE_URL` is anything other than PostgreSQL (`# __PHASE3_STRICT_PG__` block).

## Verified
- isp-admin / isp-employee / isp-public / isp-superadmin / isp-mobile-api all returning HTTP 200 from local probes.
- SQLite mtime stable across 90s observation windows.
- pg_dump backup at `/var/lib/autoispbilling/backups/pg-*.dump.gz` (21 MB compressed).
- PG row counts: customers=231, invoices=464, payments=228.

## Rollback (Emergency)
1. `chmod 0644 /var/lib/autoispbilling/autoispbilling.db`
2. Restore any `*.bak_phase3_*` file with `cp -a foo.py.bak_phase3_<TS> foo.py`
3. `systemctl restart isp-admin isp-employee isp-public isp-superadmin isp-mobile-api isp-queue-worker`
