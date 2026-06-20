# Phase 13-17 — Nginx → OpenResty Cutover (2026-06-21)

## Steps Executed
1. Added OpenResty APT repo (`http://openresty.org/package/ubuntu`).
2. Installed `openresty 1.31.1.1` (Lua + LuaJIT bundled).
3. Verified config compatibility — `openresty -t -c /etc/nginx/nginx.conf` passes.
4. Created systemd drop-in `/etc/systemd/system/openresty.service.d/00-use-etc-nginx.conf`
   that overrides `ExecStart` / `ExecStartPre` to point at `/etc/nginx/`.
5. Stopped & disabled `nginx.service`; started & enabled `openresty.service`.
6. Verified the 4 production domains during and after cutover.

## Topology after cutover
- /etc/nginx/  — unchanged (still the source-of-truth conf dir)
- /usr/local/openresty/bin/openresty  — the new binary serving traffic
- `systemctl status openresty` — active, enabled
- `systemctl status nginx`     — inactive, disabled

## Rollback (Emergency, ~3 s)
```
systemctl stop openresty
systemctl start nginx
systemctl disable openresty
systemctl enable nginx
```
The drop-in at /etc/systemd/system/openresty.service.d/00-use-etc-nginx.conf
can be removed if reverting permanently.

## Why OpenResty?
- Adds Lua scripting → enables future SSE provisioning toasts, rate-limiting,
  custom auth headers, etc. without recompiling nginx modules.
- 100% nginx config compatible (same syntax + directives).
- Drop-in replacement; binary swap, zero config rewrite needed.
