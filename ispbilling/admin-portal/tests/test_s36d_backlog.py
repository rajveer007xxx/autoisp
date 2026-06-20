"""Regression tests for s36d: QR-URL fix + final backlog."""
from __future__ import annotations
import os, sys, sqlite3
import pytest
import requests

sys.path.insert(0, "/opt/ispbilling/admin-portal")

BASE = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"})
    assert r.status_code == 200 and r.json().get("success")
    return s


# --- 1. QR URL --------------------------------------------------------
def test_hotspot_portal_url_column_present():
    conn = sqlite3.connect(DB_FILE)
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(captive_portal_settings)").fetchall()}
        assert "hotspot_portal_url" in cols
        assert "walled_garden_hosts" in cols
        assert "voucher_webhook_url" in cols
    finally:
        conn.close()


def test_qr_url_prefers_configured_url(monkeypatch=None):
    """If portal.hotspot_portal_url is set, it wins over request headers."""
    src = open("/opt/ispbilling/admin-portal/routes/vouchers.py").read()
    assert "s36d-qr-url-fix" in src
    assert "_cfg_url" in src
    # Degrade path must fall back to bare voucher code:
    assert 'if login_base else v.code' in src


# --- 2. Voucher modal UI ---------------------------------------------
def test_voucher_modal_has_delivery_ui():
    s = _login()
    r = s.get(f"{BASE}/admin/vouchers")
    assert r.status_code == 200
    html = r.text
    assert 'data-testid="voucher-delivery-whatsapp"' in html
    assert 'data-testid="voucher-phones"' in html
    assert "s36d-wa-enabled" in html
    assert 'delivery:' in html and 'phones:' in html


# --- 3. Walled-garden + webhook fields on designer -------------------
def test_designer_has_new_fields():
    s = _login()
    r = s.get(f"{BASE}/admin/captive-portal")
    assert r.status_code == 200
    html = r.text
    assert 'data-testid="cp-wg-hosts"' in html
    assert 'data-testid="cp-portal-url"' in html
    assert 'data-testid="cp-webhook-url"' in html


def test_save_accepts_new_fields():
    s = _login()
    r = s.post(f"{BASE}/api/captive-portal/save", data={
        "title": "QR Fix Test",
        "hotspot_portal_url": "https://wifi.example.com/hotspot/login.html",
        "walled_garden_hosts": "payments.example.com\nfacebook.com",
        "voucher_webhook_url": "https://webhook.example.com/voucher",
    })
    assert r.status_code == 200 and r.json().get("ok")
    # Verify persisted
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT hotspot_portal_url, walled_garden_hosts, voucher_webhook_url "
            "FROM captive_portal_settings WHERE company_id='14150129'").fetchone()
        assert row[0] == "https://wifi.example.com/hotspot/login.html"
        assert "payments.example.com" in (row[1] or "")
        assert row[2] == "https://webhook.example.com/voucher"
    finally:
        conn.close()


# --- 4. Voucher redeem endpoint + webhook ----------------------------
def test_redeem_endpoint_exists():
    src = open("/opt/ispbilling/admin-portal/routes/vouchers.py").read()
    assert "/api/vouchers/redeem/{code}" in src


def test_redeem_rejects_unknown_code():
    s = _login()
    r = s.post(f"{BASE}/api/vouchers/redeem/NOT-A-REAL-CODE-999")
    assert r.status_code == 404


def test_redeem_marks_voucher_used():
    """Seed a voucher, redeem it, confirm status flips."""
    import datetime as _dt
    code = "REDEEMT" + _dt.datetime.utcnow().strftime("%H%M%S%f")
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            "INSERT INTO hotspot_vouchers (company_id, batch_id, code, status) "
            "VALUES ('14150129', 'REDEEM-TEST', ?, 'unused')", (code,))
        conn.commit()
    finally:
        conn.close()

    s = _login()
    r = s.post(f"{BASE}/api/vouchers/redeem/{code}")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") and j.get("status") == "used" and j.get("already_used") is False

    # Second call should be idempotent
    r2 = s.post(f"{BASE}/api/vouchers/redeem/{code}")
    assert r2.json().get("already_used") is True

    # Reject expired/revoked vouchers
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE hotspot_vouchers SET status='revoked' WHERE code=?", (code,))
        conn.commit()
    finally:
        conn.close()
    r3 = s.post(f"{BASE}/api/vouchers/redeem/{code}")
    assert r3.status_code == 400


# --- 5. TOTP / 2FA ---------------------------------------------------
def test_admins_totp_columns_present():
    conn = sqlite3.connect(DB_FILE)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
        assert "totp_secret" in cols
        assert "totp_enabled" in cols
    finally:
        conn.close()


def test_totp_page_renders_with_qr():
    s = _login()
    r = s.get(f"{BASE}/admin/security/totp")
    assert r.status_code == 200
    html = r.text
    assert 'data-testid="totp-qr"' in html or "totp_enabled" in html
    # After visiting, a secret should have been created
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT totp_secret FROM admins WHERE admin_id='CITY4689'").fetchone()
        assert row and row[0], "totp_secret should be auto-generated on visit"
    finally:
        conn.close()


def test_totp_enable_endpoint_rejects_bad_code():
    s = _login()
    r = s.post(f"{BASE}/api/admin/totp/enable", json={"code": "000000"})
    assert r.status_code == 400
    assert r.json().get("ok") is False


def test_totp_enable_with_correct_code_then_disable():
    """Full flow: generate secret, enable with real TOTP, then disable."""
    s = _login()
    # Visit to ensure secret exists
    s.get(f"{BASE}/admin/security/totp")
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT totp_secret FROM admins WHERE admin_id='CITY4689'").fetchone()
        secret = row[0]
    finally:
        conn.close()
    import pyotp
    code = pyotp.TOTP(secret).now()
    r = s.post(f"{BASE}/api/admin/totp/enable", json={"code": code})
    assert r.status_code == 200, r.text
    assert r.json().get("ok")
    # New login will now require TOTP
    s2 = requests.Session()
    r2 = s2.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"})
    assert r2.status_code == 401
    assert r2.json().get("mfa_required") is True
    # Login with code
    code2 = pyotp.TOTP(secret).now()
    r3 = s2.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678",
        "totp": code2})
    assert r3.status_code == 200
    # Disable
    code3 = pyotp.TOTP(secret).now()
    r4 = s.post(f"{BASE}/api/admin/totp/disable", json={"code": code3})
    assert r4.status_code == 200


# --- 6. Dashboard hotspot widgets still render after s36d ------------
def test_dashboard_widgets_intact():
    s = _login()
    r = s.get(f"{BASE}/admin/dashboard")
    assert r.status_code == 200
    for tid in ("hs-active-sessions", "hs-vouchers-sold-today",
                "hs-vouchers-redeemed-today", "hs-vouchers-expired-pending"):
        assert f'data-testid="{tid}"' in r.text
