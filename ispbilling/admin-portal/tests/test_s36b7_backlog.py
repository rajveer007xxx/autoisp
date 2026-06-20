"""Regression tests for s36b7 backlog items.

Covers:
  1. HotspotVoucher ORM + hotspot_vouchers table.
  2. Voucher endpoints (register check).
  3. _validate_static_ip_unique helper behaviour.
  4. FreeRADIUS huntgroups file presence.
  5. routes/ scaffold presence.
"""
from __future__ import annotations
import os
import re
import sqlite3
import sys

import pytest

sys.path.insert(0, "/opt/ispbilling/admin-portal")

DB_FILE = "/var/lib/autoispbilling/autoispbilling.db"
MAIN_PY = "/opt/ispbilling/admin-portal/main.py"


def _load_main():
    with open(MAIN_PY, "r") as f:
        return f.read()


# --- 1. Voucher model + table -------------------------------------------
def test_hotspot_voucher_model_declared():
    from database import HotspotVoucher  # noqa: F401 (import proves existence)
    assert hasattr(HotspotVoucher, "code")
    assert hasattr(HotspotVoucher, "batch_id")
    assert hasattr(HotspotVoucher, "duration_minutes")


def test_hotspot_vouchers_table_exists():
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name='hotspot_vouchers'")
        assert cur.fetchone() is not None, "hotspot_vouchers table missing"
        cur.execute("PRAGMA table_info(hotspot_vouchers)")
        cols = {row[1] for row in cur.fetchall()}
        for c in ("code", "batch_id", "status", "duration_minutes",
                  "data_cap_mb", "used_by", "expires_at"):
            assert c in cols, f"column {c!r} missing"
    finally:
        conn.close()


# --- 2. Voucher endpoints registered ------------------------------------
def test_voucher_endpoints_registered():
    # s36c: routes moved into routes/vouchers.py — check either location.
    src = _load_main()
    try:
        with open('/opt/ispbilling/admin-portal/routes/vouchers.py') as f:
            src += f.read()
    except Exception:
        pass
    for path in (
        '"/admin/vouchers"',
        '"/api/vouchers/list"',
        '"/api/vouchers/generate"',
        '"/api/vouchers/{voucher_id}/revoke"',
        '"/api/vouchers/batch/{batch_id}/revoke"',
    ):
        assert path in src, f"voucher endpoint {path} missing"


# --- 3. Uniqueness helper -----------------------------------------------
def test_uniqueness_helper_exists():
    src = _load_main()
    assert "def _validate_static_ip_unique(" in src
    # company-wide fallback when nas_id is None (s36b7b)
    assert "s36b7b" in src or "if nas_id:" in src, (
        "helper must narrow by NAS only when given; NULL should fall "
        "back to company-wide check"
    )


def test_uniqueness_helper_called_in_update_customer():
    src = _load_main()
    m = re.search(
        r"async def update_customer\(.*?(?=\nasync def|\ndef |\Z)",
        src, re.DOTALL)
    assert m, "update_customer not found"
    assert "_validate_static_ip_unique(" in m.group(0), (
        "update_customer does not call _validate_static_ip_unique"
    )


# --- 4. FreeRADIUS huntgroups -------------------------------------------
def test_freeradius_huntgroups_file_present():
    path = "/etc/freeradius/3.0/huntgroups"
    if not os.path.exists(path):
        pytest.skip("huntgroups path not present on this host")
    with open(path, "r") as f:
        body = f.read()
    assert "isp-pppoe-session" in body
    assert "isp-hotspot-session" in body
    assert "isp-ethernet-session" in body
    assert "NAS-Port-Type == Virtual" in body


# --- 5. routes/ scaffold -----------------------------------------------
def test_routes_scaffold_exists():
    d = "/opt/ispbilling/admin-portal/routes"
    assert os.path.isdir(d)
    assert os.path.exists(f"{d}/__init__.py")
    assert os.path.exists(f"{d}/_example.py")
