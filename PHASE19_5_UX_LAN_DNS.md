# Phase 19.5 — Smart OLT polish + UX fixes

**Date:** 2026-06-21 15:45 IST

## Issues fixed

| # | Reported | Fix |
|---|---|---|
| 1 | Cryptic `not_registered` toasts on Connected Devices / Speed Test / Refresh Parameters / Change LAN IP | Global `fetch` interceptor in `admin_olt_onus.html` + `admin_onu_detail.html` detects `{ok:false,error:'not_registered'}` from the 4 RPC endpoints and shows an actionable alert: "ONU has never sent a CWMP Inform — run TR-069 Diagnostic, then Push TR-069 via OLT, then wait 5-10 min". Also handles `acs_not_configured` and `no_device_id`. |
| 2 | "Change LAN IP" popup only had IP + Netmask | Expanded with `LAN Gateway IP`, `Subnet Mask`, `Enable DHCP Server` toggle, `DHCP Range Start`, `DHCP Range End`, `DNS`. Auto-derives DHCP range from gateway IP as the operator types. |
| 3 | No DNS field in Smart Provision modal's LAN/DHCP tab | Added `LAN DNS (comma sep)` input alongside DHCP Range; pre-filled from the customer's `wan_dns` and pushed via TR-069 `DNSServers` on `DHCPServerConfigurable` LAN. |
| 4 | TR-069 ACS reachability only worked for PPPoE | (Phase 19.4) Now pushed for every WAN mode. |

## Backend changes
* `POST /api/admin/olt/onus/{id}/rpc/lan-ip` accepts `dhcp_enabled`, `dhcp_start`, `dhcp_end`, `dns` in body and pushes `LANHostConfigManagement.DHCPServerEnable / MinAddress / MaxAddress / DNSServers` via GenieACS. Values also persisted onto the ONU row.
* `_genieacs_auto_push()` LAN block now pushes `LANHostConfigManagement.DNSServers` (re-using `wan_dns` as the LAN DNS pool, since most ONU stacks share one).

## Verified
* LAN/DHCP tab of Smart Provision modal now shows 3-column DHCP row with **LAN DNS** field present (placeholder `8.8.8.8,1.1.1.1`).
* `freeradius`, `isp-admin`, `psql` healthy.

## Note on the "not_registered" condition
This is **not a code bug** — it's the literal truth from GenieACS: those ONUs haven't sent a CWMP Inform yet. The fix is purely UX so operators understand WHY and what to do next.
