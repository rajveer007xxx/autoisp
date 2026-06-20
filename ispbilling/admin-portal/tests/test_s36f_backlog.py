"""Regression tests for s36f:
  1. 2FA recovery codes (enable returns them, login accepts them, regenerate)
  2. Redemption dashboard + log table
"""
from __future__ import annotations
import os, sys, sqlite3, json
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
    if r.status_code == 401 and r.json().get("mfa_required"):
        import pyotp
        conn = sqlite3.connect(DB_FILE)
        try:
            row = conn.execute("SELECT totp_secret FROM admins "
                               "WHERE admin_id='CITY4689'").fetchone()
        finally:
            conn.close()
        r = s.post(f"{BASE}/api/auth/login", data={
            "userType": "admin", "companyId": "14150129",
            "userId": "CITY4689", "password": "12345678",
            "totp": pyotp.TOTP(row[0]).now()})
    assert r.status_code == 200
    return s


# --- 1. Recovery codes ---------------------------------------------------
def test_admins_totp_recovery_column():
    conn = sqlite3.connect(DB_FILE)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(admins)").fetchall()}
        assert "totp_recovery_codes" in cols
    finally:
        conn.close()


def test_enable_returns_recovery_codes_first_time():
    """Reset 2FA state for CITY4689, enable from scratch, expect 10 plaintext codes."""
    import pyotp
    # Reset state (but keep/create totp_secret so we can sign)
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE admins SET totp_enabled=0, totp_recovery_codes='' "
                     "WHERE admin_id='CITY4689'")
        conn.commit()
    finally:
        conn.close()
    s = _login()
    # Visit TOTP page — auto-creates secret if empty (handles the case where
    # an earlier test disabled 2FA and cleared the secret).
    s.get(f"{BASE}/admin/security/totp")
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute("SELECT totp_secret FROM admins "
                           "WHERE admin_id='CITY4689'").fetchone()
        secret = row[0]
    finally:
        conn.close()
    assert secret, 'totp_secret should have been auto-generated'
    s_login_again = _login()  # alias for readability below
    s = s_login_again
    r = s.post(f"{BASE}/api/admin/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") and j.get("totp_enabled")
    codes = j.get("recovery_codes")
    assert isinstance(codes, list) and len(codes) == 10
    # Sanity: format check
    import re
    for c in codes:
        assert re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", c), f"bad format: {c}"
    # Stash for later test
    with open("/tmp/s36f_codes.json", "w") as f:
        json.dump({"secret": secret, "codes": codes}, f)


def test_recovery_code_login_consumes_code():
    """Using a recovery code to log in must consume it (one-time use)."""
    with open("/tmp/s36f_codes.json") as f:
        blob = json.load(f)
    codes = blob["codes"]
    # Log in with the first recovery code
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678",
        "totp": codes[0]})
    assert r.status_code == 200, f"recovery-code login failed: {r.text}"
    assert r.json().get("success")
    # Same code must NOT work a second time
    s2 = requests.Session()
    r2 = s2.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678",
        "totp": codes[0]})
    assert r2.status_code == 401
    assert r2.json().get("mfa_required") is True


def test_regenerate_endpoint_returns_fresh_10():
    import pyotp
    with open("/tmp/s36f_codes.json") as f:
        blob = json.load(f)
    secret = blob["secret"]
    s = _login()
    r = s.post(f"{BASE}/api/admin/totp/regenerate-codes",
               json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") and len(j["codes"]) == 10
    # New codes != old codes
    old_first = blob["codes"][0]
    assert old_first not in j["codes"]


def test_admin_totp_page_shows_recovery_ui():
    s = _login()
    r = s.get(f"{BASE}/admin/security/totp")
    assert r.status_code == 200
    assert "s36f-recovery-ui" in r.text
    assert "Recovery Codes" in r.text
    # Only show regen button when 2FA is enabled
    if "2FA is ENABLED" in r.text:
        assert 'data-testid="totp-recov-regen-btn"' in r.text


# Teardown-ish: leave 2FA disabled so subsequent test runs aren't forced
@pytest.fixture(scope="module", autouse=True)
def _cleanup():
    yield
    import pyotp
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE admins SET totp_enabled=0, totp_recovery_codes='' "
                     "WHERE admin_id='CITY4689'")
        conn.commit()
    finally:
        conn.close()


# --- 2. Redemption dashboard --------------------------------------------
def test_voucher_redemptions_table_exists():
    conn = sqlite3.connect(DB_FILE)
    try:
        tbls = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "voucher_redemptions" in tbls
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(voucher_redemptions)").fetchall()}
        for c in ("company_id", "code", "batch_id", "mac_address",
                  "ip_address", "user_agent", "created_at"):
            assert c in cols
    finally:
        conn.close()


def test_redemptions_page_renders():
    s = _login()
    r = s.get(f"{BASE}/admin/vouchers/redemptions")
    assert r.status_code == 200
    assert 'data-testid="redemptions-table"' in r.text
    assert 'data-testid="redemptions-filter-batch"' in r.text


def test_redemptions_list_endpoint():
    s = _login()
    r = s.get(f"{BASE}/api/vouchers/redemptions/list?limit=5")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert isinstance(j.get("rows"), list)


def test_redeem_writes_to_redemption_log():
    """Full flow: seed voucher → redeem → verify log row exists."""
    import datetime as _dt
    code = "LOGTEST" + _dt.datetime.utcnow().strftime("%H%M%S%f")
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            "INSERT INTO hotspot_vouchers (company_id, batch_id, code, status, "
            "duration_minutes, data_cap_mb) "
            "VALUES ('14150129', 'LOG-TEST', ?, 'unused', 60, 500)", (code,))
        conn.commit()
    finally:
        conn.close()

    # Redeem (note: endpoint is anonymous-callable)
    r = requests.post(f"{BASE}/api/vouchers/redeem/{code}",
                      headers={"X-Real-IP": "10.99.99.99",
                               "X-MAC": "aa:bb:cc:dd:ee:ff",
                               "User-Agent": "s36f-test-agent/1.0"})
    assert r.status_code == 200
    assert r.json().get("ok")

    # Verify log row
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT code, batch_id, mac_address, ip_address, user_agent, "
            "duration_minutes, data_cap_mb "
            "FROM voucher_redemptions WHERE code=?", (code,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    c, b, mac, ip, ua, dur, cap = row
    assert c == code
    assert b == "LOG-TEST"
    assert mac == "aa:bb:cc:dd:ee:ff"
    assert ip == "10.99.99.99"
    assert "s36f-test-agent" in (ua or "")
    assert dur == 60 and cap == 500


def test_redemptions_list_includes_seeded_row():
    s = _login()
    r = s.get(f"{BASE}/api/vouchers/redemptions/list?batch_id=LOG-TEST&limit=10")
    assert r.status_code == 200
    rows = r.json().get("rows") or []
    assert len(rows) >= 1
    first = rows[0]
    for k in ("code", "batch_id", "mac", "ip", "ua", "at"):
        assert k in first


def test_sidebar_has_redemptions_link():
    s = _login()
    r = s.get(f"{BASE}/admin/vouchers/redemptions")
    assert r.status_code == 200
    assert 'data-testid="sidebar-redemptions"' in r.text
