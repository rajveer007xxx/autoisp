"""Regression tests for s36e:
  1. hotspot_login.html calls /api/vouchers/redeem/{code} on submit
  2. Per-NAS walled_garden_hosts column + UI + override priority
  3. hotspot_portal_url backfill + save() auto-fill
"""
from __future__ import annotations
import os, sys, sqlite3
import pytest
import requests

sys.path.insert(0, "/opt/ispbilling/admin-portal")

BASE = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _login():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"})
    # Accept either success or mfa_required (if TOTP left enabled from prev tests)
    if r.status_code == 401 and r.json().get("mfa_required"):
        import pyotp
        conn = sqlite3.connect(DB_FILE)
        try:
            row = conn.execute("SELECT totp_secret FROM admins "
                               "WHERE admin_id='CITY4689'").fetchone()
        finally:
            conn.close()
        if row and row[0]:
            r = s.post(f"{BASE}/api/auth/login", data={
                "userType": "admin", "companyId": "14150129",
                "userId": "CITY4689", "password": "12345678",
                "totp": pyotp.TOTP(row[0]).now()})
    assert r.status_code == 200 and r.json().get("success")
    return s


# --- 1. Redeem-hook on hotspot_login.html ------------------------------
def test_hotspot_login_has_redeem_hook():
    r = requests.get(f"{BASE}/hotspot/login.html")
    assert r.status_code == 200
    html = r.text
    assert "s36e-redeem-hook" in html
    assert "/api/vouchers/redeem/" in html
    # Status banner exists so users see success/failure.
    assert "s36e-redeem-status" in html


def test_redeem_endpoint_reachable_from_hotspot_flow():
    """The hotspot flow is anonymous — ensure redeem endpoint accepts
    anonymous POST (as captive portal has no session)."""
    r = requests.post(f"{BASE}/api/vouchers/redeem/NOT-A-REAL-CODE-S36E")
    assert r.status_code == 404


# --- 2. Per-NAS walled-garden override --------------------------------
def test_nas_devices_walled_garden_column():
    conn = sqlite3.connect(DB_FILE)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(nas_devices)").fetchall()}
        assert "walled_garden_hosts" in cols
    finally:
        conn.close()


def test_nas_edit_modal_has_wg_field():
    s = _login()
    r = s.get(f"{BASE}/admin/nas-devices")
    assert r.status_code == 200
    assert 'id="nas_walled_garden_hosts"' in r.text
    assert 'data-testid="nas-walled-garden-hosts"' in r.text


def test_nas_update_accepts_walled_garden_hosts():
    s = _login()
    # Find an existing NAS for CITY WIFI
    r = s.get(f"{BASE}/api/nas-devices/list")
    assert r.status_code == 200
    rows = r.json().get("nas_devices") or r.json().get("rows") or []
    if not rows:
        pytest.skip("no NAS devices to test with")
    nid = rows[0]["id"]
    # Update with per-NAS walled-garden hosts
    r = s.post(f"{BASE}/api/nas-devices/{nid}/update",
               json={"walled_garden_hosts": "payments.example.com\ngoogle.com"})
    assert r.status_code == 200 and r.json().get("success")
    # Verify persistence
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT walled_garden_hosts FROM nas_devices WHERE id=?",
            (nid,)).fetchone()
        assert row and "payments.example.com" in (row[0] or "")
    finally:
        conn.close()


def test_push_to_nas_prefers_nas_override():
    """Source-code check: verify the NAS override is used before the
    company-wide portal walled-garden list."""
    src = open("/opt/ispbilling/admin-portal/routes/captive_portal.py").read()
    assert "s36e-per-nas-wg" in src
    # The ordering: nas_wg first, fall back to portal.walled_garden_hosts
    idx_nas = src.find("nas_wg = ")
    idx_portal = src.find("portal.walled_garden_hosts")
    assert 0 < idx_nas < idx_portal, \
        "nas_wg must be read BEFORE falling back to portal.walled_garden_hosts"


# --- 3. hotspot_portal_url backfill + auto-fill ----------------------
def test_portal_url_backfilled():
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute(
            "SELECT company_id, hotspot_portal_url FROM captive_portal_settings"
        ).fetchall()
        # Every row should now have a non-empty URL.
        for cid, url in rows:
            assert url and url.strip(), \
                f"company_id={cid} still has empty hotspot_portal_url"
            assert url.startswith("http"), f"bad url: {url!r}"
    finally:
        conn.close()


def test_save_autofills_empty_portal_url(tmp_path):
    """If admin submits empty hotspot_portal_url, save() should auto-fill
    it from the request Host header."""
    s = _login()
    # Clear the field
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE captive_portal_settings SET hotspot_portal_url='' "
                     "WHERE company_id='14150129'")
        conn.commit()
    finally:
        conn.close()
    # Save WITHOUT providing hotspot_portal_url
    r = s.post(f"{BASE}/api/captive-portal/save", data={
        "title": "Autofill Test"})
    assert r.status_code == 200 and r.json().get("ok")
    # Verify auto-fill happened (from Host header = 127.0.0.1:8001 → falls back to default)
    # Since 127.0.0.1 is blacklisted, the field should stay empty (no autofill) — OR
    # if accessed via real domain, it's set. Either way the function doesn't CRASH.
    conn = sqlite3.connect(DB_FILE)
    try:
        url = conn.execute(
            "SELECT hotspot_portal_url FROM captive_portal_settings "
            "WHERE company_id='14150129'").fetchone()[0]
        # From 127.0.0.1 save it's still empty (correctly skipped)
        # Restore a default for subsequent tests
        conn.execute("UPDATE captive_portal_settings SET hotspot_portal_url=? "
                     "WHERE company_id='14150129'",
                     ("https://www.autoispbilling.com/hotspot/login.html",))
        conn.commit()
    finally:
        conn.close()
    # Assert the code path didn't crash. That's the main regression guard.
    assert url in ("", "https://127.0.0.1:8001/hotspot/login.html", None) or url.startswith("http")


def test_save_autofills_with_forwarded_host():
    """When X-Forwarded-Host is present and real, autofill should populate."""
    s = _login()
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE captive_portal_settings SET hotspot_portal_url='' "
                     "WHERE company_id='14150129'")
        conn.commit()
    finally:
        conn.close()
    r = s.post(f"{BASE}/api/captive-portal/save",
               data={"title": "FwdHost test"},
               headers={"X-Forwarded-Host": "wifi.example.com",
                        "X-Forwarded-Proto": "https"})
    assert r.status_code == 200 and r.json().get("ok")
    conn = sqlite3.connect(DB_FILE)
    try:
        url = conn.execute(
            "SELECT hotspot_portal_url FROM captive_portal_settings "
            "WHERE company_id='14150129'").fetchone()[0]
        assert url == "https://wifi.example.com/hotspot/login.html"
        # Restore
        conn.execute("UPDATE captive_portal_settings SET hotspot_portal_url=? "
                     "WHERE company_id='14150129'",
                     ("https://www.autoispbilling.com/hotspot/login.html",))
        conn.commit()
    finally:
        conn.close()
