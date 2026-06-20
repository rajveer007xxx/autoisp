"""Regression test for s36b4 — Renew-Subscription popup Suspend/Activate loop.

The fix lives in the client JS of admin_users.html. We assert the patched
guard + suppression flag are present so the infinite-alert bug cannot sneak
back in.
"""
from __future__ import annotations

USERS_HTML = "/opt/ispbilling/admin-portal/templates/admin_users.html"


def _load() -> str:
    with open(USERS_HTML, "r", encoding="utf-8") as f:
        return f.read()


def test_activate_guard_only_fires_on_turn_on():
    src = _load()
    # The old unconditional check must be gone
    assert "if ($('#renew_suspend_toggle').prop('checked')) {\n                            alert('Please turn off Suspend first before activating');" not in src, (
        "Old unconditional activate-guard still present — will cause "
        "infinite alert loop when suspend is enabled."
    )
    # The new conditional (isChecked && suspend) must be present
    assert "if (isChecked && $('#renew_suspend_toggle').prop('checked'))" in src, (
        "New conditional activate-guard missing — alert may fire on turn-off."
    )


def test_suppression_flag_exists_and_is_used():
    src = _load()
    assert "window._s36b4_suppress_activate" in src, (
        "Suppression flag missing — cascade from Suspend will re-enter "
        "Activate handler and trigger loop."
    )
    # Must be used at BOTH the top of activate handler AND around the cascade
    assert src.count("window._s36b4_suppress_activate = true") >= 2, (
        "Flag must be set TRUE at both alert-branch and suspend-cascade."
    )
    assert src.count("if (window._s36b4_suppress_activate) { return; }") >= 1, (
        "Flag check at top of activate handler missing."
    )
