# Phase 19.3 — Smart-default naming refresh + full 4-mode WAN

**Date:** 2026-06-21 15:05 IST

## What changed

### (1) Default Wi-Fi template now uses the customer's first name
| Field | Old | New |
|---|---|---|
| 2.4 GHz SSID | `FIBERNET-{mobile_last4}-2G` | `{first_name}-{mobile_last4}-2G` |
| 5 GHz SSID | `FIBERNET-{mobile_last4}-5G` | `{first_name}-{mobile_last4}-5G` |
| Wi-Fi password (both bands) | `{name_first4}{mobile_last4}` | `{first_name4}{mobile_last4}` |

`{first_name}` resolves to the **first token** of `customers.customer_name`,
split on whitespace **or** `.` / `_` / `-`, with non-alphanumerics
stripped. `{first_name4}` is its first 4 letters (no padding).

Examples:
* `Rajveer Singh`, `9876543210` → SSID `Rajveer-3210-2G`, pw `Rajv3210`
* `mp.sehbaz.fibernet`, `918855224477` → SSID `mp-4477-2G`, pw `mp4477`
* `Ibraahim121`, `9603310004` → SSID `Ibraahim121-0004-2G`, pw `Ibra0004`

DB migration: in-place `UPDATE onu_service_profiles SET … WHERE is_default=1`.

### (2) WAN settings now come from the customer's account
`fetch_customer_for_onu()` joins `customers.auth_type / username /
pppoe_password / static_ip_address / static_netmask / vlan_enabled /
vlan_id` and exposes a `customer.wan{}` object. `build_wan_for_onu()`
normalises into the 4 canonical modes:

| Mode | Pushed parameters |
|---|---|
| `pppoe`     | `Username`, `Password`, optional `PPPoEServiceName`, VLAN |
| `static_ip` | `AddressingType=Static`, `ExternalIPAddress`, `SubnetMask`, `DefaultGateway`, `DNSServers`, VLAN |
| `dhcp`      | `AddressingType=DHCP`, VLAN |
| `bridge`    | `WANIPConnection.1.ConnectionType=IP_Bridged`, both routed connections disabled, VLAN |

VLAN is honoured **in every mode** when the customer has
`vlan_enabled=1`. It's pushed via `WANEthernetLinkConfig.VLANIDMark`
(TR-098) **and** `.VLAN` (vendor alias) so OEMs that follow either
spec apply it.

`_genieacs_auto_push()` now branches on `wan_mode`, with `static_ip /
dhcp / bridge / pppoe` aliases normalised (`static`, `staticip`,
`dynamic`, `bridged` accepted). When mode != pppoe, the conflicting
WANPPPConnection.1 is explicitly disabled (and vice-versa) so the ONU
never has dual-stack confusion.

### (3) Smart Provision modal grows a "WAN" tab
A 5th tab between **5 GHz** and **LAN/DHCP** shows:
* WAN Mode (PPPoE / Static IP / DHCP / Bridge) dropdown — auto-detected
  from the customer.
* VLAN ID + PPPoE Service Name (both optional)
* PPPoE Username / Password (auto-fetched, editable)
* Static-IP fields (IP / Netmask / Gateway / DNS) — shown only in static mode
* Bridge mode info banner

Modal JS re-shows the right sub-section on mode change.

### Verified
* Preview API → SSID `mp-4477-2G`, pw `mp4477`, WAN `pppoe` w/ correct
  username + password.
* Preview for `static_ip` customer (`Ibraahim121`) → SSID
  `Ibraahim121-0004-2G`, pw `Ibra0004`, WAN `static_ip` with
  `wan_static_ip=10.10.40.17`, `wan_netmask=255.255.255.0`.
* Modal renders WAN tab correctly; PPPoE creds pre-filled from
  customer account; mode-switch shows/hides sub-sections.
* `freeradius`, `isp-admin`, `psql` all healthy.

## Migrate (one-shot SQL)
```sql
UPDATE onu_service_profiles SET
  wifi_ssid_tpl    = '{first_name}-{mobile_last4}-2G',
  wifi_ssid_5g_tpl = '{first_name}-{mobile_last4}-5G',
  wifi_pw_tpl      = '{first_name4}{mobile_last4}',
  wifi_pw_5g_tpl   = '{first_name4}{mobile_last4}'
WHERE is_default = 1;
```
