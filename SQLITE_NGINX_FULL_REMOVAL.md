# Session-3 — Complete SQLite & Nginx Removal (2026-06-21)

## What's Gone Forever
- `/var/lib/autoispbilling/autoispbilling.db` — DELETED (in quarantine tarball)
- `/var/lib/freeradius/radacct.db`            — DELETED (in quarantine tarball)
- `nginx`, `nginx-common`, `python3-certbot-nginx` packages — PURGED
- `/usr/sbin/nginx` binary — REMOVED

## What's Now the Single Source of Truth
- **Web tier**: OpenResty 1.31.1.1 (config `/etc/nginx/*`)
- **App DB**: PostgreSQL `autoispbilling` (everything)
- **RADIUS DB**: PostgreSQL `autoispbilling` schema with FreeRADIUS-canonical tables:
    radacct, radpostauth, radcheck, radreply, radgroupcheck, radgroupreply, radusergroup, nas
- **TLS certs**: certbot now uses `webroot` plugin (`/var/www/letsencrypt`) instead of `nginx` plugin

## Migrations Performed
- `mobile_api_v3.py:3179` unterminated-string syntax error fixed
   → v3 mobile API endpoints now register on startup
- 215,924 FreeRADIUS rows migrated SQLite → PG
   (radacct: 21,990 · radpostauth: 215,742 · radcheck: 45 · radreply: 145 · radusergroup: 2)
- 9 Python files patched to read radacct via `db_compat`
- 5 portal `main.py` files prepended with an `sqlite3.connect` monkey-guard
  that transparently reroutes the two legacy paths to PostgreSQL
- All 7 `db_compat.py` copies now carry both:
    `# __PHASE3_STRICT_PG__`  (RuntimeError if DATABASE_URL ≠ Postgres)
    `# __PHASE_FINAL_SQLITE_GUARD__` (in-process patch of `sqlite3.connect`)
- `isp-onu-ticks.sh` now sources `/etc/ispbilling.env` (was the last bug
  letting `outage_correlator._ensure_schema()` write to SQLite from cron)
- `freeradius-postgresql` apt package installed
- `/etc/freeradius/3.0/mods-available/sql` rewritten for PG DSN + pool
- 4 certbot renewal configs migrated to `authenticator = webroot`
- `/etc/nginx/sites-enabled/{ispbilling,pay-autoispbilling}` rewritten to
  use `^~ /.well-known/acme-challenge/` *before* the redirect (the
  certbot-nginx `if ($host = …) { return 301; }` server-level pattern
  replaced with a `location /` 301)
- `radtest` against `127.0.0.1:1812` returns `Access-Accept` from PG-backed `radcheck`
- `certbot renew --dry-run` succeeds for all 3 HTTP-01 certs

## Backups Preserved
- `/root/sqlite_quarantine_20260621_092645.tar.gz` (40 MB, contains both SQLite DBs)
- `/root/autoispbilling_pre_hardening_backup_20260621_011228.tar.gz` (623 MB)
- Per-phase `.bak_*` files alongside each patched .py
- 30-day cron rotation retained

## Rollback (Emergency)
1. Stop services: `systemctl stop isp-admin isp-employee isp-public isp-superadmin isp-mobile-api freeradius`
2. Restore SQLite files from `/root/sqlite_quarantine_*.tar.gz`
3. `apt-get install nginx nginx-common python3-certbot-nginx`
4. `cp /etc/freeradius/3.0/mods-available/sql /root/freeradius_sql_mod.bak_* /etc/freeradius/3.0/mods-available/sql`
5. Restart all services
