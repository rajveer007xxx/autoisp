"""Regression tests for s36b9 final backlog:

  1. RouterOSClient.upload_file_sftp signature + dry-run path.
  2. /api/captive-portal/nas-list returns JSON.
  3. /api/captive-portal/push-to-nas/{id} rejects bad NAS and requires auth.
  4. Captive-portal designer page shows the Push-to-NAS UI block.
  5. Dashboard contains the four new Hotspot widget cards + stats keys.
  6. Activity-log routes still work after refactor into routes/activity_log.py.
  7. routes/activity_log.py module is the actual registered source of the endpoints.
"""
from __future__ import annotations
import os, sys, inspect
import pytest
import requests

sys.path.insert(0, "/opt/ispbilling/admin-portal")

BASE = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")


def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"
    }, timeout=10)
    assert r.status_code == 200 and r.json().get("success")
    return s


# --- 1. RouterOSClient.upload_file_sftp ---------------------------------
def test_routeros_upload_file_sftp_exists():
    from routeros_provision import RouterOSClient
    assert hasattr(RouterOSClient, "upload_file_sftp"), \
        "RouterOSClient.upload_file_sftp is missing"
    sig = inspect.signature(RouterOSClient.upload_file_sftp)
    params = list(sig.parameters.keys())
    assert "remote_name" in params and "content" in params


def test_routeros_upload_file_sftp_dry_run():
    """Dry run must not touch the network."""
    from routeros_provision import RouterOSClient

    class FakeNas:
        ip_address = "203.0.113.1"
        api_username = "admin"; api_password = "x"
        port = 8728; use_tls = False; use_ssh = False; ssh_port = 22
    with RouterOSClient(FakeNas(), dry_run=True) as cli:
        res = cli.upload_file_sftp("hotspot/login.html", b"<html/>")
    assert res.get("dry_run") is True
    assert res["remote"] == "hotspot/login.html"
    assert res["size"] == 7


# --- 2. Captive portal NAS list -----------------------------------------
def test_captive_portal_nas_list_endpoint():
    s = _login()
    r = s.get(f"{BASE}/api/captive-portal/nas-list")
    assert r.status_code == 200
    j = r.json()
    assert j.get("success") is True
    assert isinstance(j.get("rows"), list)


# --- 3. push-to-nas guards ---------------------------------------------
def test_push_to_nas_bad_id_returns_404():
    s = _login()
    r = s.post(f"{BASE}/api/captive-portal/push-to-nas/999999")
    assert r.status_code == 404
    assert r.json().get("success") is False


def test_push_to_nas_requires_auth():
    # anonymous request must not leak — either 303 redirect to login or 401
    r = requests.post(f"{BASE}/api/captive-portal/push-to-nas/1", allow_redirects=False)
    assert r.status_code in (303, 401, 302)


# --- 4. Designer UI contains Push-to-NAS block -------------------------
def test_designer_page_has_push_ui():
    s = _login()
    r = s.get(f"{BASE}/admin/captive-portal")
    assert r.status_code == 200
    assert "s36b9_nas_select" in r.text
    assert "s36b9_push_btn" in r.text
    assert "Push to MikroTik NAS" in r.text
    assert 'data-testid="cp-push-btn"' in r.text


# --- 5. Dashboard widgets ----------------------------------------------
def test_dashboard_has_hotspot_widgets():
    s = _login()
    r = s.get(f"{BASE}/admin/dashboard")
    assert r.status_code == 200
    html = r.text
    assert 'data-testid="hs-active-sessions"' in html
    assert 'data-testid="hs-vouchers-sold-today"' in html
    assert 'data-testid="hs-vouchers-redeemed-today"' in html
    assert 'data-testid="hs-vouchers-expired-pending"' in html
    assert "Hotspot — Active Sessions" in html


# --- 6. Activity-log routes still work after extraction ---------------
def test_activity_log_still_works():
    s = _login()
    r = s.get(f"{BASE}/api/activity-log/list")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert isinstance(j.get("rows"), list)


def test_activity_log_page_still_renders():
    s = _login()
    r = s.get(f"{BASE}/admin/activity-log")
    assert r.status_code == 200
    assert "Activity Log" in r.text


# --- 7. Router module exists and is loaded -----------------------------
def test_activity_log_router_module_exists():
    from routes import activity_log as mod
    assert hasattr(mod, "router")
    assert hasattr(mod, "register")
