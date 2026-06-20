"""AuthBackend edge-case: NAS unreachable must fail gracefully."""
from __future__ import annotations
import sys, socket, os
sys.path.insert(0, "/opt/ispbilling/admin-portal")


class FakeNasUnreachable:
    id = 999; name = "ghost-nas"
    ip_address = "10.255.255.254"   # unroutable
    api_username = "admin"; api_password = "x"
    port = 8728; use_tls = False; use_ssh = False
    ssh_port = 22; company_id = "14150129"; status = "Active"


def test_routeros_client_connect_fails_gracefully():
    from routeros_provision import RouterOSClient
    socket.setdefaulttimeout(2)
    raised = None
    try:
        with RouterOSClient(FakeNasUnreachable()) as cli: _ = cli
    except Exception as e:
        raised = e
    socket.setdefaulttimeout(None)
    assert raised is not None, "connecting to unroutable IP should raise"


def test_upload_file_sftp_unreachable_returns_error():
    from routeros_provision import RouterOSClient
    cli = RouterOSClient.__new__(RouterOSClient)
    cli.nas = FakeNasUnreachable(); cli.dry_run = False
    cli._api = None; cli._ssh = None
    cli.transport_used = None; cli.commands = []
    socket.setdefaulttimeout(2)
    res = cli.upload_file_sftp("hotspot/login.html", b"<html/>")
    socket.setdefaulttimeout(None)
    assert res.get("success") is False
    assert "error" in res


def test_push_to_nas_endpoint_survives_unreachable():
    import requests
    BASE = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"})
    assert r.status_code == 200
    r = s.post(f"{BASE}/api/captive-portal/push-to-nas/999999")
    assert r.status_code in (404, 500, 502)
    assert r.json().get("success") is False
