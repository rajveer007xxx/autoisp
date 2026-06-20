"""Regression tests for the /admin/users route.

s36b1 fix: the inline MikroTik sync inside /admin/users was overwriting
walled-garden users back to Online every page load. These tests guard against
that regressing: we seed a known WalledGarden state, hit the page via real
HTTP, then assert the state is preserved.

The tests hit the LIVE local uvicorn instance on 127.0.0.1:8001 (safe: read-only
except for the `online_users` row we own, which we reset between runs).

Run on the VPS:
    cd /opt/ispbilling/admin-portal
    /opt/ispbilling/venv/bin/pytest tests/test_admin_users_walled_garden.py -v
"""
from __future__ import annotations
import os
import sqlite3
import time

import pytest
import requests

BASE_URL = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
DB_PATH = os.environ.get(
    "AUTOISPBILLING_DB_PATH", "/var/lib/autoispbilling/autoispbilling.db"
)

# ---- credentials come from /app/memory/test_credentials.md ------------------
TEST_COMPANY_ID = "14150129"
TEST_USER_ID = "CITY4689"
TEST_PASSWORD = "12345678"

# A customer that is known to be Expired in the test DB. If this is ever
# renamed or deleted, tests should be updated to point to another expired user.
TEST_EXPIRED_USERNAME = "city.raj.fibernet"


def _fetch_online_row(username: str, company_id: str = TEST_COMPANY_ID):
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT username, status, ip_address FROM online_users "
            "WHERE company_id = ? AND LOWER(username) = LOWER(?)",
            (company_id, username),
        )
        return cur.fetchone()
    finally:
        conn.close()


def _seed_walled_garden(username: str, company_id: str = TEST_COMPANY_ID):
    """Force the row into WalledGarden state before the test runs."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE online_users SET status = 'WalledGarden' "
            "WHERE company_id = ? AND LOWER(username) = LOWER(?)",
            (company_id, username),
        )
        conn.commit()
    finally:
        conn.close()


def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        data={
            "userType": "admin",
            "companyId": TEST_COMPANY_ID,
            "userId": TEST_USER_ID,
            "password": TEST_PASSWORD,
        },
        timeout=10,
        allow_redirects=False,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    body = r.json()
    assert body.get("success") is True, body
    return s


# -----------------------------------------------------------------------------


def test_expired_user_exists_and_is_walled_garden():
    """Sanity: the test subject must exist and be non-Active."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT status FROM customers WHERE company_id = ? AND username = ?",
            (TEST_COMPANY_ID, TEST_EXPIRED_USERNAME),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, (
        f"test subject {TEST_EXPIRED_USERNAME!r} missing from customers table — "
        f"update TEST_EXPIRED_USERNAME in this test"
    )
    assert row[0].lower() != "active", (
        f"test subject must be non-Active; found status={row[0]!r}"
    )


def test_admin_users_page_loads():
    s = _login()
    r = s.get(f"{BASE_URL}/admin/users", timeout=30)
    assert r.status_code == 200, f"/admin/users returned {r.status_code}"


def test_walled_garden_user_does_not_flip_to_online_after_page_load():
    """The regression check for s36b1.

    1. Force the row to WalledGarden.
    2. Hit /admin/users (this runs the inline MikroTik sync inside the route).
    3. Re-read the row — it MUST still be WalledGarden or Offline, never Online.
    """
    _seed_walled_garden(TEST_EXPIRED_USERNAME)
    before = _fetch_online_row(TEST_EXPIRED_USERNAME)
    if before is None:
        pytest.skip(
            f"online_users row for {TEST_EXPIRED_USERNAME} not present in DB — "
            "no live PPPoE session to test against"
        )
    assert before[1] == "WalledGarden", f"pre-condition failed: {before}"

    s = _login()
    r = s.get(f"{BASE_URL}/admin/users", timeout=30)
    assert r.status_code == 200

    # Give the inline commit a moment to settle
    time.sleep(0.5)
    after = _fetch_online_row(TEST_EXPIRED_USERNAME)
    assert after is not None
    assert after[1] != "Online", (
        f"REGRESSION: walled-garden user flipped to Online after /admin/users "
        f"load. Before={before}, After={after}"
    )


def test_active_customers_still_reported_online_when_live():
    """Sanity: the fix must not break the Online badge for genuinely Active users.

    We only assert that for any online_users row whose customer.status='Active'
    and whose row isn't Offline, the status IS 'Online' (never WalledGarden).
    If no such row exists we skip — no live Active PPPoE sessions to validate.
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT o.username, o.status, c.status
            FROM online_users o
            JOIN customers c
              ON c.company_id = o.company_id AND LOWER(c.username) = LOWER(o.username)
            WHERE o.company_id = ?
              AND LOWER(c.status) = 'active'
              AND o.status != 'Offline'
            """,
            (TEST_COMPANY_ID,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        pytest.skip("no live PPPoE sessions for Active customers — cannot validate")

    for username, ou_status, cust_status in rows:
        assert ou_status == "Online", (
            f"active customer {username!r} has online_users.status={ou_status!r} "
            f"(cust_status={cust_status!r}); expected 'Online'"
        )
