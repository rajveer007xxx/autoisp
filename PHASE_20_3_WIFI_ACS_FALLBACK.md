# Phase 20.3 - WiFi CLI Safety Net + Auto Fallback to TR-069 (ACS)

## Background
Phase 20.2 added 7 WiFi enable syntax variants for Netlink V1600D.
End-to-end validation on ONU 2652 revealed the V1600D 3.1.02-250124
firmware rejects EVERY `wifi_*` CLI command with `% Unknown command`
or `% Command incomplete` (including the existing
`wifi_switch enable world-wide`).

## Safety net (`olt_telnet_actions.py`)
- In both `push_wifi()` and `zero_touch_provision_vsol()`, count
  attempts vs. rejection markers. When >= 50% of attempts return
  one of:
    * `% Unknown command`
    * `% Command incomplete`
    * `% Invalid input`
    * `% Incomplete command`
  the result is tagged `firmware_rejected_cli=True` with a clear
  human-readable error string.

## Auto-fallback (`olt_routes.py` zero-touch-provision handler)
- When `firmware_rejected_cli` is true and wifi_arg was supplied,
  immediately call `_genieacs_auto_push(cid, onu_id)` which reads
  the just-persisted WiFi config from the `onus` row and pushes
  via TR-069 SetParameterValues to GenieACS.
- On success, `results.wifi.ok` is flipped to True and
  `recovered_via='tr069_acs'` is set so the UI knows.
- Summary line appends "wifi=RECOVERED via ACS".

## Verified on ONU 2652 (Netlink V1600D)
```
firmware_rejected_cli: True
acs_fallback.ok: True   (HTTP 202 from GenieACS)
recovered_via: tr069_acs
wifi.ok: True
summary: wan=OK | wifi=FAIL | tr069=OK | wifi=RECOVERED via ACS
```
The TR-069 SetParameterValues payload included:
- LANDevice.1.WLANConfiguration.1.SSID = RAJEEV-6699-2G
- LANDevice.1.WLANConfiguration.1.KeyPassphrase = 1215245454
- LANDevice.1.WLANConfiguration.1.Enable = true
- Same for WLANConfiguration.5 (5 GHz)
- WAN PPPoE + LAN/DHCP all included.

## Net result
Operators with V1600D firmware that lacks the wifi_* CLI namespace now
get WiFi pushed transparently via TR-069 with zero extra clicks.
