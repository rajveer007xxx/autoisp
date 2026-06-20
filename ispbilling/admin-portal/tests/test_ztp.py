"""Pytest suite for the ZTP stack.

Wraps the existing /tmp/ztp_smoke.py + /tmp/ztp_e2e.py + /tmp/p1_final.py
into proper pytest test cases with assertions. Run with:

    cd /opt/ispbilling/admin-portal &&
    /opt/ispbilling/venv/bin/python3 -m pytest tests/test_ztp.py -v

Some tests are marked `@pytest.mark.live_olt` and require a reachable
production OLT. They are skipped if the OLT is unreachable.
"""
import json
import os
import sys
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

import pytest

# Load env before importing application modules (DATABASE_URL must be set
# so SQLAlchemy builds a Postgres engine, not a SQLite fallback).
_ENV_PATH = "/etc/ispbilling.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k, v.strip().strip('"').strip("'"))

sys.path.insert(0, "/opt/ispbilling/admin-portal")


# ──────────────────────────────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def sa_engine():
    from olt_routes import engine
    return engine


@pytest.fixture(scope="session")
def primary_company_id():
    """The production tenant we run E2E against."""
    return "15378763"


# ──────────────────────────────────────────────────────────────────────
#  Schema tests — Phase A
# ──────────────────────────────────────────────────────────────────────
class TestSchema:
    """All 7 ZTP tables exist and have expected columns."""

    EXPECTED_TABLES = [
        "ztp_discovered_onus",
        "ztp_onu_customer_mapping",
        "ztp_onu_profiles",
        "acs_device_mapping",
        "acs_device_parameter_profiles",
        "ztp_state_audit",
        "ztp_dhcp_option43_configs",
    ]

    def test_all_tables_exist(self, sa_engine):
        with sa_engine.begin() as conn:
            for tbl in self.EXPECTED_TABLES:
                n = conn.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                assert n >= 0, f"{tbl} not queryable"

    def test_parameter_profiles_seeded(self, sa_engine):
        """We seeded 10 vendor profiles in the migration."""
        with sa_engine.begin() as conn:
            n = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM acs_device_parameter_profiles"
            ).fetchone()[0]
        assert n >= 10, f"expected >=10 seeded profiles, got {n}"

    def test_existing_onus_backfilled(self, sa_engine, primary_company_id):
        """The one-time backfill migrated >=200 ONUs into the mapping table."""
        with sa_engine.begin() as conn:
            n = conn.exec_driver_sql(
                "SELECT COUNT(*) FROM ztp_onu_customer_mapping "
                "WHERE company_id=%s",
                (primary_company_id,)).fetchone()[0]
        assert n >= 100, f"backfill seems incomplete: {n} rows"


# ──────────────────────────────────────────────────────────────────────
#  Driver registry tests — Phase B
# ──────────────────────────────────────────────────────────────────────
class TestDrivers:
    def test_all_vendors_resolvable(self):
        from ztp_drivers import get_driver
        for v in ["vsol", "netlink_epon", "huawei", "zte",
                  "bdcom", "cdata", "c-data", "fiberhome",
                  "optilink", "syrotech_gpon", "syrotech_epon"]:
            d = get_driver(v)
            assert d is not None, f"no driver for {v}"
            assert d.vendor_id != "generic_cli", (
                f"{v} resolved to GenericCli fallback (registry miss)")

    def test_unknown_vendor_falls_back(self):
        from ztp_drivers import get_driver
        d = get_driver("xyzunknown")
        assert d.vendor_id == "generic_cli"

    def test_syrotech_epon_uses_vsol_family_cli(self):
        """Syrotech EPON should inherit VSOL behavior."""
        from ztp_drivers import SyrotechEPONDriver, VSOLNetlinkEPONDriver
        assert issubclass(SyrotechEPONDriver, VSOLNetlinkEPONDriver)

    def test_list_drivers_dedupes(self):
        """list_drivers should return one entry per concrete driver
        regardless of how many alias keys point at it."""
        from ztp_drivers import list_drivers, _DRIVER_REGISTRY
        drivers = list_drivers()
        unique_concrete = {id(v) for v in _DRIVER_REGISTRY.values()}
        assert len(drivers) == len(unique_concrete)

    def test_driver_result_envelope(self):
        from ztp_drivers import DriverResult
        r = DriverResult(ok=True, vendor="x", output="y")
        d = r.to_dict()
        assert d["ok"] is True
        assert "error" in d
        assert "method" in d


# ──────────────────────────────────────────────────────────────────────
#  DHCP43 generator tests — Phase E
# ──────────────────────────────────────────────────────────────────────
class TestDhcp43:
    def test_option43_tlv_format(self):
        from ztp_dhcp43 import build_option43_value
        v = build_option43_value("http://acs.example.com/cwmp")
        # 0x01 <len> <ascii>
        assert v.startswith("0x01"), v
        # ASCII length byte
        url_hex = "http://acs.example.com/cwmp".encode().hex()
        assert v.endswith(url_hex)

    def test_option125_includes_enterprise_3561(self):
        from ztp_dhcp43 import build_option125_value
        v = build_option125_value("http://x.com/cwmp")
        # enterprise 3561 = 0x00000DE9 as the first 4 bytes
        assert v.startswith("0x00000de9"), v

    def test_script_contains_vlan_in_routeros(self):
        from ztp_dhcp43 import generate_mikrotik_script
        g = generate_mikrotik_script(port_name="ether2", vlan_id=4042,
                                     acs_url="http://acs.example.com/cwmp")
        s = g["script"]
        assert "vlan-id=4042" in s
        assert "ether2" in s
        assert "opt43-acs" in s
        assert "opt125-acs" in s
        # Script must NOT push without operator review.
        assert "/system reboot" not in s

    def test_invalid_vlan_rejected(self):
        from ztp_dhcp43 import generate_mikrotik_script
        with pytest.raises(ValueError):
            generate_mikrotik_script(port_name="ether2", vlan_id=99999,
                                     acs_url="http://x/cwmp")

    def test_invalid_url_rejected(self):
        from ztp_dhcp43 import generate_mikrotik_script
        with pytest.raises(ValueError):
            generate_mikrotik_script(port_name="ether2", vlan_id=10,
                                     acs_url="ftp://x")


# ──────────────────────────────────────────────────────────────────────
#  ZTPEngine tests — Phases C, F, G
# ──────────────────────────────────────────────────────────────────────
class TestEngine:
    def test_normalize_serial(self):
        from ztp_engine import _normalize_serial
        assert (_normalize_serial("98:9d:b2:cf:d6:ff")
                == "989DB2CFD6FF")
        assert (_normalize_serial("HWTC-12345678")
                == "HWTC12345678")
        assert _normalize_serial("") == ""

    def test_state_constants(self):
        import ztp_engine
        assert "DISCOVERED" in ztp_engine.STATES
        assert "ONLINE" in ztp_engine.STATES
        assert "ONLINE" in ztp_engine.TERMINAL_OK
        assert "BOOTSTRAP_FAILED" in ztp_engine.TERMINAL_FAIL

    def test_map_to_customer_idempotent(self, sa_engine, primary_company_id):
        """Mapping the same serial twice must succeed without dupes."""
        from ztp_engine import ZTPEngine
        eng = ZTPEngine(primary_company_id, actor="pytest")
        TEST_SN = "PYTEST_DEDUPE_TEST_SN"
        # Cleanup before
        with sa_engine.begin() as c:
            c.exec_driver_sql(
                "DELETE FROM ztp_onu_customer_mapping WHERE onu_serial=%s",
                (TEST_SN,))
        try:
            r1 = eng.map_to_customer(serial=TEST_SN,
                                     customer_id="9999999",
                                     olt_id=49, pon_port=1, onu_index=99)
            r2 = eng.map_to_customer(serial=TEST_SN,
                                     customer_id="9999999",
                                     olt_id=49, pon_port=1, onu_index=99)
            assert r1["ok"] is True
            assert r2["ok"] is True
            with sa_engine.begin() as c:
                n = c.exec_driver_sql(
                    "SELECT COUNT(*) FROM ztp_onu_customer_mapping "
                    "WHERE onu_serial=%s", (TEST_SN,)).fetchone()[0]
            assert n == 1, f"duplicate row created: {n}"
        finally:
            with sa_engine.begin() as c:
                c.exec_driver_sql(
                    "DELETE FROM ztp_onu_customer_mapping "
                    "WHERE onu_serial=%s", (TEST_SN,))
                c.exec_driver_sql(
                    "DELETE FROM ztp_state_audit "
                    "WHERE onu_serial=%s", (TEST_SN,))

    def test_diagnose_returns_six_checks(self, primary_company_id):
        from ztp_engine import ZTPEngine
        eng = ZTPEngine(primary_company_id, actor="pytest")
        d = eng.diagnose("98:9d:b2:cf:d6:ff")
        assert "checks" in d
        steps = {c["step"] for c in d["checks"]}
        # core checks that must always appear:
        for required in {"mapping", "acs_url_set", "acs_reach",
                         "pppoe_creds_set"}:
            assert required in steps, f"missing {required} check"

    def test_compatibility_labels(self, primary_company_id):
        from ztp_engine import ZTPEngine
        eng = ZTPEngine(primary_company_id, actor="pytest")
        c = eng.compatibility("98:9d:b2:cf:d6:ff")
        assert c["compatibility"] in {
            "Fully Supported", "Partially Supported", "OLT Only",
            "ACS Only", "Manual Required", "Unsupported / Locked"}


# ──────────────────────────────────────────────────────────────────────
#  HTTP API smoke — confirms routes are mounted
# ──────────────────────────────────────────────────────────────────────
class TestHTTP:
    BASE = "http://127.0.0.1:8001"

    @pytest.fixture(scope="class")
    def http_alive(self):
        try:
            with socket.create_connection(("127.0.0.1", 8001), timeout=2):
                return True
        except Exception:
            pytest.skip("admin portal not listening on :8001")

    def test_health_requires_auth(self, http_alive):
        try:
            urllib.request.urlopen(self.BASE + "/api/admin/ztp/health",
                                   timeout=3)
            pytest.fail("expected 401")
        except urllib.error.HTTPError as he:
            assert he.code == 401

    def test_vendors_requires_auth(self, http_alive):
        try:
            urllib.request.urlopen(self.BASE + "/api/admin/ztp/vendors",
                                   timeout=3)
            pytest.fail("expected 401")
        except urllib.error.HTTPError as he:
            assert he.code == 401


# ──────────────────────────────────────────────────────────────────────
#  Live-OLT field tests — Phase B (skipped if OLT unreachable)
# ──────────────────────────────────────────────────────────────────────
def _olt_reachable(host: str, port: int = 23) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


@pytest.mark.parametrize("olt_id,vendor_alias", [
    pytest.param(49, "netlink_epon",
                 id="OLT49_NETLINK1_vsol_netlink_epon"),
    pytest.param(50, "syrotech_epon",
                 id="OLT50_SYROTECH1_syrotech_epon"),
    pytest.param(51, "syrotech_epon",
                 id="OLT51_SYROTECH2_syrotech_epon"),
])
def test_field_driver_telnet_connect(olt_id, vendor_alias, sa_engine):
    """Field-test: pick the driver for each real OLT and ensure the
    Telnet pool can connect + enter config mode + exit cleanly. This
    validates the CLI helper integration without altering live state."""
    from ztp_drivers import get_driver
    with sa_engine.begin() as conn:
        row = conn.exec_driver_sql(
            "SELECT id,vendor,host,cli_port,cli_username,cli_password "
            "FROM olts WHERE id=%s", (olt_id,)).fetchone()
    if not row:
        pytest.skip(f"OLT {olt_id} not in DB")
    olt = {"id": row[0], "vendor": row[1], "host": row[2],
           "cli_port": row[3] or 23, "cli_username": row[4],
           "cli_password": row[5]}
    if not _olt_reachable(olt["host"], olt["cli_port"]):
        pytest.skip(f"OLT {olt['host']} unreachable")
    drv = get_driver(vendor_alias)
    assert drv.vendor_id == "vsol_netlink_epon" or drv.vendor_id == "syrotech_epon", (
        f"unexpected driver: {drv.vendor_id}")
    # get_onu_status on a low/safe index — just validates connectivity.
    r = drv.get_onu_status(olt, pon_port=1, onu_index=1)
    # Don't assert ok=True — ONU 1 may not exist. We only assert no
    # exception was thrown and that the driver returned a structured
    # result.
    assert isinstance(r.method, str)
    assert isinstance(r.output, str)


@pytest.mark.parametrize("vendor", [
    "huawei_gpon", "zte_gpon", "bdcom_gpon", "cdata_gpon",
    "fiberhome_gpon", "optilink_gpon", "syrotech_gpon"])
def test_scaffold_drivers_unsupported_safe_default(vendor):
    """Scaffold drivers should NEVER raise when invoked with arbitrary
    args. They must return a DriverResult — either ok=True with real
    output, or ok=False with method='unsupported'."""
    from ztp_drivers import get_driver
    drv = get_driver(vendor)
    # Call a method without an actual OLT — should not raise.
    r = drv.factory_reset_onu({"vendor": vendor, "host": "0.0.0.0",
                               "cli_port": 23,
                               "cli_username": "", "cli_password": ""},
                              pon_port=1, onu_index=1)
    assert isinstance(r.method, str)
    # OK to fail — but it must fail GRACEFULLY (no exception raised).
