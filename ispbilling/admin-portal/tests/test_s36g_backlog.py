"""Regression tests for s36g."""
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


# --- 1. Notifications extraction -----------------------------------------
def test_notifications_router_extracted():
    from routes import admin_notifications as mod
    assert hasattr(mod, "router") and hasattr(mod, "register")


def test_notifications_feed_still_works():
    s = _login()
    r = s.get(f"{BASE}/api/admin/notifications/feed")
    assert r.status_code == 200


def test_notifications_summary_works():
    s = _login()
    r = s.get(f"{BASE}/api/admin/notifications/summary")
    assert r.status_code == 200


# --- 2. Email recovery codes --------------------------------------------
def test_email_recovery_codes_endpoint_exists():
    s = _login()
    # Bad code → 400 (confirming the endpoint is wired)
    r = s.post(f"{BASE}/api/admin/totp/email-codes", json={"code": "000000"})
    assert r.status_code in (400, 500)  # 400 if no TOTP or bad code; 500 if no SMTP
    assert r.json().get("ok") is False


def test_totp_page_has_email_button():
    import pyotp
    # Ensure 2FA is enabled first
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT totp_secret, totp_enabled FROM admins "
            "WHERE admin_id='CITY4689'").fetchone()
    finally:
        conn.close()
    if not (row and row[0] and row[1]):
        pytest.skip("2FA not enabled for test account")
    s = _login()
    r = s.get(f"{BASE}/admin/security/totp")
    assert r.status_code == 200
    assert 'data-testid="totp-email-btn"' in r.text


# --- 3. Redemption analytics --------------------------------------------
def test_analytics_endpoint_shape():
    s = _login()
    r = s.get(f"{BASE}/api/vouchers/redemptions/analytics?days=30")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert "daily" in j
    assert "hourly" in j and len(j["hourly"]) == 24
    assert "top_batches" in j
    assert "totals" in j
    for k in ("week", "month", "all", "distinct_devices"):
        assert k in j["totals"]


def test_redemptions_dashboard_has_charts():
    s = _login()
    r = s.get(f"{BASE}/admin/vouchers/redemptions")
    assert r.status_code == 200
    html = r.text
    assert "s36g-analytics" in html
    assert 'data-testid="analytics-total-all"' in html
    assert 'data-testid="analytics-daily-chart"' in html
    assert 'data-testid="analytics-hour-chart"' in html
    assert 'data-testid="analytics-top-batches"' in html
    assert "chart.js" in html.lower() or "Chart.js" in html or "Chart(" in html


# --- 4. 2FA enforcement policy ------------------------------------------
def test_mfa_policy_columns_present():
    conn = sqlite3.connect(DB_FILE)
    try:
        comp_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(companies)").fetchall()}
        assert "mfa_required_for_admins" in comp_cols
        assert "mfa_grace_period_days" in comp_cols
        adm_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(admins)").fetchall()}
        assert "mfa_deadline" in adm_cols
    finally:
        conn.close()


def test_mfa_policy_page_renders():
    s = _login()
    r = s.get(f"{BASE}/admin/security/mfa-policy")
    assert r.status_code == 200
    assert 'data-testid="mfa-required-toggle"' in r.text
    assert 'data-testid="mfa-grace-days"' in r.text


def test_mfa_policy_save_and_deadline_flow():
    """Turn policy ON → admin w/o 2FA gets a deadline. Turn OFF → deadlines cleared."""
    s = _login()
    # Ensure an admin has totp_enabled=0 to observe the effect
    conn = sqlite3.connect(DB_FILE)
    try:
        # Count pre-state
        pre = conn.execute(
            "SELECT COUNT(*) FROM admins WHERE company_id='14150129' AND "
            "(totp_enabled IS NULL OR totp_enabled=0)").fetchone()[0]
    finally:
        conn.close()

    # Turn policy ON with 14-day grace
    r = s.post(f"{BASE}/api/admin/mfa-policy/save",
               json={"mfa_required": True, "mfa_grace_period_days": 14})
    assert r.status_code == 200 and r.json().get("ok")

    conn = sqlite3.connect(DB_FILE)
    try:
        # Admins without 2FA should now have a deadline set
        with_deadline = conn.execute(
            "SELECT COUNT(*) FROM admins WHERE company_id='14150129' AND "
            "(totp_enabled IS NULL OR totp_enabled=0) AND "
            "mfa_deadline IS NOT NULL AND mfa_deadline != ''").fetchone()[0]
        assert with_deadline >= min(pre, 1) or pre == 0
    finally:
        conn.close()

    # Turn OFF
    r = s.post(f"{BASE}/api/admin/mfa-policy/save",
               json={"mfa_required": False, "mfa_grace_period_days": 7})
    assert r.status_code == 200 and r.json().get("ok") is True
    conn = sqlite3.connect(DB_FILE)
    try:
        still_deadline = conn.execute(
            "SELECT COUNT(*) FROM admins WHERE company_id='14150129' AND "
            "mfa_deadline IS NOT NULL AND mfa_deadline != ''").fetchone()[0]
        assert still_deadline == 0
    finally:
        conn.close()


def test_dashboard_shows_mfa_warning_when_required():
    s = _login()
    # Turn ON policy with short grace
    s.post(f"{BASE}/api/admin/mfa-policy/save",
           json={"mfa_required": True, "mfa_grace_period_days": 14})

    # Make sure our current admin has totp disabled OR look for banner on any page
    conn = sqlite3.connect(DB_FILE)
    try:
        enabled = conn.execute(
            "SELECT totp_enabled FROM admins WHERE admin_id='CITY4689'"
        ).fetchone()[0]
    finally:
        conn.close()
    r = s.get(f"{BASE}/admin/dashboard")
    assert r.status_code == 200
    if not enabled:
        # Banner must appear
        assert 'data-testid="mfa-warn-banner"' in r.text

    # Reset: turn policy OFF
    s.post(f"{BASE}/api/admin/mfa-policy/save",
           json={"mfa_required": False, "mfa_grace_period_days": 7})


# --- 5. Loyalty engine ---------------------------------------------------
def test_list_includes_mac_total():
    """Seed 3 redemptions for the same MAC, fetch list, expect mac_total ≥ 3."""
    import datetime as _dt, uuid
    batch = "LOY-" + uuid.uuid4().hex[:6].upper()
    mac = "de:ad:be:ef:12:34"
    conn = sqlite3.connect(DB_FILE)
    try:
        for i in range(3):
            conn.execute(
                "INSERT INTO hotspot_vouchers (company_id, batch_id, code, status) "
                "VALUES ('14150129', ?, ?, 'unused')",
                (batch, f"LOY{i}{uuid.uuid4().hex[:6].upper()}"))
        conn.commit()
        codes = [r[0] for r in conn.execute(
            "SELECT code FROM hotspot_vouchers WHERE batch_id=?", (batch,)).fetchall()]
    finally:
        conn.close()
    # Redeem all 3 with the same MAC
    for c in codes:
        r = requests.post(f"{BASE}/api/vouchers/redeem/{c}",
                          headers={"X-MAC": mac, "X-Real-IP": "10.11.12.13"})
        assert r.status_code == 200, r.text
    # Fetch list and verify mac_total >= 3 for these rows
    s = _login()
    r = s.get(f"{BASE}/api/vouchers/redemptions/list?batch_id={batch}")
    assert r.status_code == 200
    rows = r.json().get("rows") or []
    assert len(rows) == 3
    for row in rows:
        assert row["mac"] == mac
        assert row["mac_total"] >= 3


def test_dashboard_template_has_loyalty_column():
    s = _login()
    r = s.get(f"{BASE}/admin/vouchers/redemptions")
    assert r.status_code == 200
    assert 'data-testid="loyalty-header"' in r.text
    assert 's36g-loyalty-col' in r.text
