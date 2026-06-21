# Session-2 — Phases A · B · C · D  (2026-06-21)

## D — Lua Rate Limit on High-Impact RPC Endpoints
- `lua_shared_dict rpc_rl_onu 10m;` + `lua_shared_dict rpc_rl_ip 10m;` in `/etc/nginx/conf.d/00-rpc-rate-limit.conf`
- `/etc/nginx/snippets/rpc_rate_limit.conf` regex-matches `/api/admin/olt/onus/<id>/rpc/(reboot|factory-reset)`
- Enforced: **1 RPC per ONU per 15 s** + **5 RPCs per client IP per 60 s**
- 429 response carries JSON body + `Retry-After` + `X-RPC-RateLimit-Scope` (`onu` | `ip`)
- VERIFIED end-to-end: 1st request → 422 (backend), 2nd-7th → 429 onu, after 16 s window → 422 again

## C — SSE "Provisioning Activity Toast"
- New router `/opt/ispbilling/admin-portal/acs_activity_stream.py`
- Endpoints:
  - `GET /api/admin/acs/activity/stream` — SSE feed (polls PG every 2 s)
  - `GET /api/admin/acs/activity/recent?limit=50` — JSON fallback / history
- Sources fed: `acs_push_log`, `ztp_discovered_onus`, `complaints (kind='Auto-Outage')`
  including ZTP discovery + reboot/factory-reset outcomes
- New JS widget `/static/js/acs_activity_toast.js` injected into
  `base_admin.html`, `base_employee.html`, `base_sub_lco.html`
- Toast colours: green/red/violet/orange by event type. FAB with badge.
  Side drawer keeps last 50 events. Auto-reconnect with exponential back-off.

## A — sqlite3 Literal Cleanup (cosmetic)
- All 4 `database.py` files now **fail-fast** if `DATABASE_URL` isn't PostgreSQL
- Legacy `migrate_db()` schema-bootstrap helpers neutered (`__PHASE4_NOOP__`)
- `superadmin-portal/sa_backups.py` rewritten to call `pg_dump` (was `sqlite3.backup`)
- All 3 stale `main_full.py` snapshots guarded with `__PHASE4_INACTIVE__`
- FreeRADIUS radacct.db readers + tests + one-shot migration scripts **intentionally untouched**

## B — Aggressive Env-Var Refactor
- 14 new env vars added to `/etc/ispbilling.env`
  (`ISP_ADMIN_URL`, `ISP_EMPLOYEE_URL`, `ISP_PUBLIC_URL`, `ISP_SUPERADMIN_URL`,
   `ISP_MOBILE_API_URL`, `GENIEACS_NBI_URL`, `GENIEACS_CWMP_URL`,
   `GENIEACS_FS_URL`, `REDIS_URL`, `REDIS_HOST`, `RADIUS_HOST`,
   `RADIUS_AUTH_PORT`, `RADIUS_ACCT_PORT`, `PG_HOST`)
- **32 hard-coded URLs** replaced across **13 files** with
  `os.environ.get("…_URL", "http://127.0.0.1:<port>")` (default preserved
  so single-server deployment still works unmodified)
- Top wins: `mobile-api/main.py` (16 admin-portal API calls),
  `admin-portal/bulk_ztp.py` (4 NBI calls), `task_queue/broker.py` (Redis URL).
- ~66 occurrences left — all in comments, MikroTik RouterOS scripts (where
  127.0.0.1 refers to the *target* router's own loopback), `radtest` invocations,
  string-MATCHING checks (`if "127.0.0.1" in acs_url`), or a known-broken
  `mobile_api_v3.py` file (pre-existing syntax error at line 3179).

## Bug Fixed Along the Way
- `cron/outage_cron.py` referenced `pon_port_index` for `outage_events`
  but the actual PG column is `pon_port`. Patched. First post-fix run
  detected **14 dormant outages** that had been masked by the prior crash.

## E2E After-State
- 28/28 smoke checks GREEN
- All 5 portals + queue worker + ZTP watcher + vlan-radius-sync healthy
- Lua rate limit live and enforcing the policy
- SSE stream returns proper 401 unauth + 200 authed
- SQLite still quarantined (mtime frozen)
- PG row counts unchanged (customers 231, invoices 464, payments 228, onus 388)
