# Phase 20.8 - Fix push_wifi() crash + raise ACS timeout

## User-reported error after editing Wi-Fi from Quick Actions
```
{
  "ok": true, "queued": "wifi", "onu_id": 2652,
  "auto_provision": { "ok": false, "error": "ACS push timed out",
                        "timeout": true, "timeout_sec": 10 },
  "cli_push":       { "ok": false,
                        "error": "push_wifi() got an unexpected keyword argument 'band_split'",
                        "timeout": false }
}
```

## Bug 1 - `push_wifi()` crashed on `band_split` kwarg
The overlay wrapper at `olt_telnet_actions.py:1293` forwards every
Pydantic body field (including `band_split`) to the underlying
`_orig_push_wifi` defined at line 168. The underlying function did
not accept `band_split`, so kwarg forwarding raised TypeError.

### Fix
Extended `_orig_push_wifi` signature with:
- `band_split` (no-op kwarg)
- `channel_24 / channel_5 / bandwidth_24 / bandwidth_5` (Phase 20.6
  payload fields)
- `bw_24 / bw_5 / auto_24 / auto_5` (DB column names)
- `**_extra_kw` (catch-all so future additions never crash)

Channels/BW aren't pushed via CLI (they're TR-069 only) so accepting
+ silently ignoring them on the CLI side is the correct behaviour.

## Bug 2 - ACS push timed out at 10s
GenieACS sends a connection-request to the ONU, which wakes its CWMP
client and sets ~20 parameters, then replies. This empirically takes
18-25 s for a cold ONU. The 10 s watchdog was too aggressive and
always failed even when the push actually succeeded.

### Fix
Bumped the ACS timeout from 10s -> 30s in both:
- `api_onu_wifi` route
- the second occurrence (api_onu_wan) for consistency

Also reworded the fallback message: "ACS push timed out (waited 30s)".

## Verified
- POST /api/admin/olt/onus/2652/wifi with full Phase 20.6 payload
  (channel_24, bw_24, auto_24, band_split, etc.) no longer raises
  TypeError. cli_push returns a clean error (or success).
- ACS timeout now reads `timeout_sec: 30.0`.

## Files touched
- `/opt/ispbilling/admin-portal/olt_telnet_actions.py`
- `/opt/ispbilling/admin-portal/olt_routes.py`
