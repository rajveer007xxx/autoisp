"""Regression tests for s36b3 — subscriber Audit Trail date/time format.

Expected format on /admin/subscribers/<pk> Audit Trail tab:
  * Group heading → DD-MM-YYYY (e.g. "23-04-2026")
  * Event time    → HH:MM     (e.g. "10:34") — 24-hour, no AM/PM
  * Embedded renewal dates → DD-MM-YYYY (e.g. "From 23-04-2026 To 23-05-2026")
"""
from __future__ import annotations
import os
import re
import sqlite3

import pytest
import requests

BASE_URL = os.environ.get("ISP_ADMIN_URL", "http://127.0.0.1:8001")
DB_PATH = os.environ.get(
    "AUTOISPBILLING_DB_PATH", "/var/lib/autoispbilling/autoispbilling.db"
)

TEST_COMPANY_ID = "14150129"
TEST_USER_ID = "CITY4689"
TEST_PASSWORD = "12345678"


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
    )
    assert r.status_code == 200 and r.json().get("success"), r.text[:200]
    return s


def _first_customer_pk() -> int:
    """Prefer a customer that has renewal transactions (so we can test all
    three format rules including the embedded-date one); fall back to any."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT c.id FROM customers c
            JOIN transactions t
              ON t.company_id = c.company_id AND t.customer_id = c.customer_id
            WHERE c.company_id = ?
              AND c.status != 'Deleted'
              AND t.start_date IS NOT NULL AND t.end_date IS NOT NULL
            GROUP BY c.id
            ORDER BY COUNT(t.id) DESC
            LIMIT 1
            """,
            (TEST_COMPANY_ID,),
        )
        row = cur.fetchone()
        if not row:
            cur.execute(
                "SELECT id FROM customers WHERE company_id = ? "
                "AND status != 'Deleted' ORDER BY id ASC LIMIT 1",
                (TEST_COMPANY_ID,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row, "no customer available for test"
    return row[0]


@pytest.fixture(scope="module")
def subscriber_html() -> str:
    s = _login()
    pk = _first_customer_pk()
    r = s.get(f"{BASE_URL}/admin/subscribers/{pk}", timeout=30)
    assert r.status_code == 200, f"subscriber page HTTP {r.status_code}"
    return r.text


def test_audit_heading_is_dd_mm_yyyy(subscriber_html):
    """Group heading must match DD-MM-YYYY."""
    # Look inside the audit-wrap block
    m = re.search(
        r'<div class="audit-date">\s*(\d{2}-\d{2}-\d{4})\s*</div>', subscriber_html
    )
    assert m, "No DD-MM-YYYY heading found in Audit Trail"
    # Explicitly reject old "%d-%b-%Y" like "23-Apr-2026"
    assert not re.search(
        r'<div class="audit-date">\s*\d{2}-[A-Za-z]{3}-\d{4}\s*</div>',
        subscriber_html,
    ), "Old DD-MMM-YYYY format still present in heading"


def test_audit_time_is_12h_with_am_pm(subscriber_html):
    """Event time must be 12-hour with AM/PM (per user preference, s36b4)."""
    times = re.findall(r'<div class="t">\s*(.*?)\s*</div>', subscriber_html)
    times = [t for t in times if ":" in t and any(c.isdigit() for c in t)]
    assert times, "No time entries found in Audit Trail"
    for t in times:
        assert re.fullmatch(r"\d{1,2}:\d{2} (AM|PM)", t), (
            f"Bad time format in audit row: {t!r} (expected 'HH:MM AM/PM')"
        )


def test_renewal_line_uses_dd_mm_yyyy(subscriber_html):
    """Package renewal line must embed DD-MM-YYYY dates (not ISO YYYY-MM-DD)."""
    # find all renewal lines (Jinja escapes quotes to &#34; or &quot;)
    renewals = re.findall(
        r'Package\s+(?:"|&quot;|&#34;)[^"&<]+(?:"|&quot;|&#34;)\s+is renewed '
        r'for the subscriber with validity From ([0-9\-]+) To ([0-9\-]+)',
        subscriber_html,
    )
    if not renewals:
        pytest.skip("No renewal events in this subscriber's audit trail")
    for sd, ed in renewals:
        assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", sd), (
            f"Renewal start_date not DD-MM-YYYY: {sd!r}"
        )
        assert re.fullmatch(r"\d{2}-\d{2}-\d{4}", ed), (
            f"Renewal end_date not DD-MM-YYYY: {ed!r}"
        )
