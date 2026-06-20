"""Regression tests for multi-auth dispatcher (s36b6)."""
from __future__ import annotations
import os, sys
sys.path.insert(0, "/opt/ispbilling/admin-portal")

from auth_backends import (
    get_backend, backend_for_customer,
    PPPoEBackend, StaticIPBackend, HotspotBackend,
    WALLED_GARDEN_LIST, ALLOWED_LIST,
)


class _FakeCust:
    def __init__(self, auth_type, username="test.user", customer_id="cust1",
                 static_ip="10.1.1.50", mac=""):
        self.auth_type = auth_type
        self.username = username
        self.customer_id = customer_id
        self.static_ip_address = static_ip
        self.mac_address = mac


def test_dispatcher_returns_correct_backend_for_each_auth_type():
    assert isinstance(get_backend("pppoe"), PPPoEBackend)
    assert isinstance(get_backend("static_ip"), StaticIPBackend)
    assert isinstance(get_backend("hotspot"), HotspotBackend)


def test_dispatcher_defaults_to_pppoe_on_unknown_or_none():
    assert isinstance(get_backend(None), PPPoEBackend)
    assert isinstance(get_backend(""), PPPoEBackend)
    assert isinstance(get_backend("garbage"), PPPoEBackend)


def test_backend_for_customer_reads_auth_type():
    f = _FakeCust("static_ip")
    assert isinstance(backend_for_customer(f), StaticIPBackend)
    f.auth_type = "hotspot"
    assert isinstance(backend_for_customer(f), HotspotBackend)


def test_all_backends_implement_full_verb_contract():
    required = {"provision", "deprovision", "disable", "enable", "kick"}
    for be in (PPPoEBackend(), StaticIPBackend(), HotspotBackend()):
        for verb in required:
            assert hasattr(be, verb) and callable(getattr(be, verb)), (
                f"{be.__class__.__name__} missing verb {verb!r}"
            )


def test_address_list_constants_unchanged():
    """The router-side list names are user-visible on the router. Changing
    them requires a migration on every deployed MikroTik."""
    assert WALLED_GARDEN_LIST == "isp-walled-garden"
    assert ALLOWED_LIST == "isp-active-static"


def test_main_dispatcher_uses_auth_backends():
    """The /router helper must import from auth_backends, not the legacy
    hard-coded PPPoE-only path."""
    with open("/opt/ispbilling/admin-portal/main.py", "r") as f:
        src = f.read()
    # Marker proves the refactor happened
    assert "# s36b6: multi-auth dispatcher" in src
    # And the dispatcher imports backend_for_customer
    assert "from auth_backends import backend_for_customer" in src


def test_quick_setup_endpoint_registered():
    with open("/opt/ispbilling/admin-portal/main.py", "r") as f:
        src = f.read()
    assert '@app.post("/api/nas-devices/{nas_id}/quick-setup/{profile}")' in src
    assert "profile_map = {" in src
    # All 4 profiles exposed
    for p in ("pppoe", "hotspot", "static", "walled_garden"):
        assert f'"{p}"' in src, f"quick-setup profile {p!r} missing from profile_map"


def test_edit_popup_has_auth_type_and_related_fields():
    with open(
        "/opt/ispbilling/admin-portal/templates/admin_users.html", "r"
    ) as f:
        src = f.read()
    assert 'id="edit_auth_type"' in src, "Edit popup missing auth_type dropdown"
    assert 'id="edit_static_ip_address"' in src
    assert 'id="edit_mac_address"' in src
    assert 'id="edit_hotspot_session_timeout"' in src
    assert "toggleEditAuthTypeFields" in src, (
        "Edit popup missing show/hide JS for auth_type conditional fields"
    )


def test_customer_model_declares_multi_auth_columns():
    with open("/opt/ispbilling/admin-portal/database.py", "r") as f:
        src = f.read()
    for col in ("auth_type", "static_ip_address", "static_netmask",
                "hotspot_session_timeout", "hotspot_idle_timeout"):
        assert f"{col} = Column(" in src, (
            f"Customer model missing Column declaration for {col}"
        )
