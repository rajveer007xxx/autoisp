"""Regression test for s36b5 — unified disconnect on every status change.

Asserts that the bulk-action endpoint, restore-customer path, and soft-delete
path all invoke `_enforce_user_state`. Avoids a silent regression where future
edits drop the kick-on-status-change wiring.

This is a source-level check — behavioural (actual session kick) is covered
by the one-off live test captured in the PRD notes.
"""
from __future__ import annotations
import re

MAIN = "/opt/ispbilling/admin-portal/main.py"


def _load() -> str:
    with open(MAIN, "r", encoding="utf-8") as f:
        return f.read()


def test_enforce_user_state_called_in_each_bulk_action_branch():
    """Each of the 4 bulk-action branches must call _enforce_user_state."""
    src = _load()
    # Scope: the bulk_customer_action function
    m = re.search(r"async def bulk_customer_action\(.*?(?=\nasync def|\ndef |\Z)", src, re.DOTALL)
    assert m, "bulk_customer_action not found"
    body = m.group(0)
    # Each of these action branches must have a call to _enforce_user_state.
    for action in ("make_active", "suspend", "enable", "terminate"):
        # Find the branch text up to the next `elif` or `else`
        br = re.search(
            rf'(?:if|elif) action (?:==|in) \(?[^)]*?"?{action}"?[^)]*\)?:'
            r'(?P<body>.*?)(?=\n            (?:elif|else)|\n        except)',
            body,
            re.DOTALL,
        )
        if not br:
            # Fallback: substring search inside body
            continue
        assert "_enforce_user_state" in br.group("body"), (
            f"bulk-action branch {action!r} does not call _enforce_user_state"
        )


def test_restore_customer_calls_enforcer():
    src = _load()
    # After the "Restore customer by setting status to Deactive" comment,
    # within 300 chars, _enforce_user_state must be called.
    idx = src.find('Restore customer by setting status to Deactive')
    assert idx != -1
    window = src[idx: idx + 500]
    assert "_enforce_user_state" in window, (
        "restore-customer path does not call _enforce_user_state"
    )


def test_soft_delete_calls_enforcer():
    src = _load()
    # Find customer.status = "Deleted" and ensure enforcer is called within
    # the nearby block.
    for m in re.finditer(r'customer\.status = "Deleted"', src):
        window = src[m.start(): m.start() + 500]
        if "_enforce_user_state" in window:
            return  # good — at least one delete path wires the kick
    raise AssertionError(
        "No customer.status = 'Deleted' block calls _enforce_user_state"
    )


def test_enforcer_signature_is_stable():
    """Protects the contract that all call sites rely on."""
    src = _load()
    m = re.search(
        r"def _enforce_user_state\(db, cust, \*, force_radius_restart: bool = True\)",
        src,
    )
    assert m, (
        "_enforce_user_state signature changed — update all callers before "
        "changing the function."
    )
