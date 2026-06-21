# Phase 20.10 - Customer Portal "Connected Devices" menu

New self-service page that lets a subscriber see every phone, laptop,
TV or smart device currently associated to their router - pulled live
from GenieACS via TR-069.

## Routes (in `phase25_customer.py`)
- `GET /customer/connected-devices` - HTML page (requires customer session).
- `GET /api/customer/me/connected-devices` - JSON, resolves the
  customer's most-recent ONU and uses the Phase 20.5 resolver
  (`_p205_resolve_acs_device`) to find the GenieACS device, then
  projects `InternetGatewayDevice.LANDevice.1.Hosts` and returns
  `{ok, onu_id, last_inform, hosts:[...], count}`.

## Template (`customer_connected_devices.html`)
- Status banner with last-inform timestamp and device count.
- Refresh button (`data-testid=cd-refresh-btn`) + auto-refresh every 60s.
- Table with: online/idle dot, device name, IP, MAC, connection type
  (WiFi / Ethernet auto-detected from `Layer1Interface`).
- Friendly empty-state when the ONU is in bridge mode.

## Menu (`base_user.html`)
- Added `<a href="/customer/connected-devices" data-testid="user-nav-connected">Connected Devices</a>`
  right after the existing WiFi Settings link.

## Verified
- GET /customer/connected-devices -> 302 (redirect to /login when unauth, expected).
- GET /api/customer/me/connected-devices -> 401 (no session, expected).
- No Jinja or Python errors in logs after restart.
