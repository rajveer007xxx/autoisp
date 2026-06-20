"""Regression tests for s36b2 — email 'From' display-name format.

Format expected:
    "<COMPANY_NAME_UPPERCASE> - <Suffix>" <sender@domain>

Suffixes by email type:
  * send_receipt_email        → "Receipt"
  * send_payment_link_email   → "Payment Reminder"
  * send_invoice_email        → "Invoice"

We assert on the source code (reliable, no SMTP required) that each call site
uses formataddr with an uppercase company name + correct suffix. This is a
"shape" test — enough to catch any future edit that reverts or mis-wires the
From header.
"""
from __future__ import annotations
import re

MAIN = "/opt/ispbilling/admin-portal/main.py"


def _load_main() -> str:
    with open(MAIN, "r", encoding="utf-8") as f:
        return f.read()


def test_receipt_email_from_uses_uppercase_company_and_suffix():
    src = _load_main()
    m = re.search(
        r"msg\['From'\] = _fmtaddr_s36b2\(\(f\"\{(?P<cname>\w+)\} - Receipt\",",
        src,
    )
    assert m, "Receipt email From header not using formataddr w/ 'Receipt' suffix"
    # The variable referenced must carry an .upper() call right above
    idx = m.start()
    context = src[max(0, idx - 300): idx]
    assert ".upper()" in context, "Receipt From: company name not uppercased"


def test_payment_reminder_email_from_uses_uppercase_company_and_suffix():
    src = _load_main()
    m = re.search(
        r"msg\['From'\] = _fmtaddr_s36b2\(\(f\"\{(?P<cname>\w+)\} - Payment Reminder\",",
        src,
    )
    assert m, "Payment Reminder email From header not using formataddr w/ correct suffix"
    idx = m.start()
    context = src[max(0, idx - 300): idx]
    assert ".upper()" in context, "Payment Reminder From: company name not uppercased"


def test_invoice_email_from_uses_uppercase_company_and_suffix():
    src = _load_main()
    m = re.search(
        r"msg\['From'\] = _fmtaddr_s36b2\(\(f\"\{(?P<cname>\w+)\} - Invoice\",",
        src,
    )
    assert m, "Invoice email From header not using formataddr w/ 'Invoice' suffix"
    idx = m.start()
    context = src[max(0, idx - 300): idx]
    assert ".upper()" in context, "Invoice From: company name not uppercased"


def test_formataddr_produces_expected_header():
    """Sanity: verify the actual RFC-5322 header stdlib produces."""
    from email.utils import formataddr
    got = formataddr(("CITY WIFI - Receipt", "no-reply@autoispbilling.com"))
    assert got == "CITY WIFI - Receipt <no-reply@autoispbilling.com>"

    got = formataddr(("CITY WIFI - Invoice", "billing@citywifi.in"))
    assert got == "CITY WIFI - Invoice <billing@citywifi.in>"
