# Phase 19 — FreeRADIUS Access Request Logs MAC + NAS-IP

**Date:** 2026-06-21

## Problem
After the SQLite → PostgreSQL migration, `radpostauth` rows were being
inserted with NULL `callingstationid` (MAC) and NULL `nasipaddress`.
The Admin Portal → "Access Request Logs" page therefore showed blank
MAC and NAS-IP columns. Latest entries were already first because the
fetch query in `radpostauth_tenant.fetch_tenant_radpostauth` orders by
`authdate DESC`.

## Root cause
`/etc/freeradius/3.0/mods-config/sql/main/postgresql/queries.conf`
shipped with the upstream default `post-auth { query = ... }` which
inserts only `(username, pass, reply, authdate)`. The table has the
new columns but FreeRADIUS never populated them.

## Fix
Replaced the top-level `post-auth { ... }` block to also insert
`calledstationid`, `callingstationid`, and `nasipaddress` (the
packet-source IP of the NAS) — values pulled from the Access-Request
attributes:

```
post-auth {
    query = "INSERT INTO ${..postauth_table}
              (username, pass, reply, calledstationid, callingstationid,
               nasipaddress, authdate ${..class.column_name})
            VALUES('%{User-Name}',
                   '%{%{User-Password}:-%{Chap-Password}}',
                   '%{reply:Packet-Type}',
                   '%{Called-Station-Id}',
                   '%{Calling-Station-Id}',
                   NULLIF('%{Packet-Src-IP-Address}', '')::inet,
                   '%S.%M' ${..class.reply_xlat})"
}
```

`freeradius -CX` validates clean, service reloads cleanly, and new rows
contain the MAC + NAS-IP columns populated (verified live on
`mp.sehbaz.fibernet @ 10.50.128.14`).

## NAS Accounting Verification (auto-detected)
Iterated every Active NAS in `nas_devices` and queried `/radius` via
RouterOS API. All reachable NAS devices forward to FreeRADIUS at
`10.50.0.1:1812/1813` with service `ppp,login,hotspot`. `radacct`
already shows 593 active sessions, confirming Acct-Start packets are
flowing.

| NAS          | NAS-IP        | FreeRADIUS     | Auth/Acct  | Status |
|--------------|---------------|----------------|------------|--------|
| MIKROTIK     | 10.50.128.2   | (n/a)          | (n/a)      | unreachable |
| MIKROTIK2011 | 10.50.128.6   | 10.50.0.1      | 1812/1813  | OK |
| MikrotikHex  | 10.50.128.10  | 10.50.0.1      | 1812/1813  | OK |
| MIKROTIK2    | 10.50.128.14  | 10.50.0.1      | 1812/1813  | OK |
