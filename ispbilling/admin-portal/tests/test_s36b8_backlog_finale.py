"""Regression tests for s36b8 backlog finale.

Covers:
  1. Voucher-expiry cron script.
  2. AdminActivityLog model + endpoints.
  3. CaptivePortalSettings model + endpoints.
  4. Hotspot login template renders.
  5. Bulk-print Cards PDF endpoint returns a valid PDF.
"""
from __future__ import annotations
import os, sys, subprocess, sqlite3, datetime
import pytest
import requests

sys.path.insert(0, "/opt/ispbilling/admin-portal")

BASE = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"


def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", data={
        "userType": "admin", "companyId": "14150129",
        "userId": "CITY4689", "password": "12345678"
    }, timeout=10)
    assert r.status_code == 200 and r.json().get("success")
    return s


# --- 1. Voucher expiry cron ---------------------------------------------
def test_voucher_expiry_script_exists_and_runs():
    path = "/opt/ispbilling/admin-portal/voucher_expiry_cron.py"
    assert os.path.exists(path), "expiry cron script missing"
    result = subprocess.run(
        ["/opt/ispbilling/venv/bin/python", path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode in (0, 2), (
        f"cron exited {result.returncode}: {result.stderr[:200]}"
    )


def test_voucher_expiry_systemd_timer_enabled():
    r = subprocess.run(["systemctl", "is-enabled", "isp-voucher-expiry.timer"],
                       capture_output=True, text=True)
    assert "enabled" in r.stdout or r.returncode == 0, (
        "isp-voucher-expiry.timer not enabled"
    )


def test_voucher_expiry_flips_stale_row():
    """Seed a stale voucher, run the cron, confirm it flipped to expired."""
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        # ensure unique code per run
        code = "TESTEXP" + datetime.datetime.utcnow().strftime("%H%M%S%f")
        cur.execute(
            "INSERT INTO hotspot_vouchers "
            "(company_id, batch_id, code, status, expires_at) "
            "VALUES (?, ?, ?, 'unused', datetime('now','-1 day'))",
            ("14150129", "TEST-REG", code))
        conn.commit()

        subprocess.run(
            ["/opt/ispbilling/venv/bin/python",
             "/opt/ispbilling/admin-portal/voucher_expiry_cron.py"],
            capture_output=True, text=True, timeout=30)

        cur.execute("SELECT status FROM hotspot_vouchers WHERE code = ?", (code,))
        status = cur.fetchone()[0]
        assert status == "expired", f"cron did not expire stale row: status={status!r}"
    finally:
        conn.close()


# --- 2. Activity log -----------------------------------------------------
def test_activity_log_endpoints():
    s = _login()
    r = s.get(f"{BASE}/api/activity-log/list")
    assert r.status_code == 200
    j = r.json()
    assert j.get("ok") is True
    assert isinstance(j.get("rows"), list)


def test_activity_log_page_renders():
    s = _login()
    r = s.get(f"{BASE}/admin/activity-log")
    assert r.status_code == 200
    assert "Activity Log" in r.text
    assert "aal-action-create" in r.text or "aal-action-update" in r.text


# --- 3. Captive portal ---------------------------------------------------
def test_captive_portal_save_and_render():
    s = _login()
    r = s.post(f"{BASE}/api/captive-portal/save", data={
        "title": "Test Title s36b8",
        "welcome_text": "Hello world",
        "primary_color": "#123456",
        "accent_color": "#abcdef",
        "login_mode": "voucher",
        "footer_text": "Test footer",
    })
    assert r.status_code == 200 and r.json().get("ok")

    r = s.get(f"{BASE}/hotspot/login.html")
    assert r.status_code == 200
    assert "Test Title s36b8" in r.text
    assert "#123456" in r.text  # primary color actually injected


def test_captive_portal_designer_page():
    s = _login()
    r = s.get(f"{BASE}/admin/captive-portal")
    assert r.status_code == 200
    assert "Captive Portal Designer" in r.text
    assert "Live Preview" in r.text
    assert 'name="primary_color"' in r.text


# --- 4. Bulk-print Cards PDF -------------------------------------------
def test_voucher_cards_pdf_returns_pdf():
    """Ensure we have a batch and download its PDF."""
    # find any existing batch
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT batch_id FROM hotspot_vouchers "
                    "WHERE company_id='14150129' ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        pytest.skip("no voucher batch exists to test")

    s = _login()
    r = s.get(f"{BASE}/api/vouchers/batch/{row[0]}/pdf", timeout=30)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/pdf")
    # Real PDF files start with %PDF-
    assert r.content[:5] == b"%PDF-", "response is not a valid PDF"
    assert len(r.content) > 500, "PDF suspiciously tiny"


def test_voucher_cards_pdf_invalid_batch_returns_404():
    s = _login()
    r = s.get(f"{BASE}/api/vouchers/batch/DOES_NOT_EXIST/pdf")
    assert r.status_code == 404


# --- 5. Sidebar wiring ---------------------------------------------------
def test_sidebar_has_captive_portal_and_activity_log():
    path = "/opt/ispbilling/admin-portal/templates/base_admin.html"
    with open(path) as f:
        src = f.read()
    assert "/admin/captive-portal" in src
    assert "/admin/activity-log" in src
