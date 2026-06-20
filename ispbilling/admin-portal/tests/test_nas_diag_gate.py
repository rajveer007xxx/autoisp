#!/usr/bin/env python3
"""
Regression: ensure the test-connection diagnostic gate covers all the
common failure modes for a tunnel-IP NAS, including socket.timeout
(message="timed out"), ECONNREFUSED, EHOSTUNREACH, ETIMEDOUT.

If any of these fail to match the gate, the user sees a bare error
("timed out") with no actionable next-step. This test protects against
narrowing the gate accidentally during refactors.
"""
import re
import sys
from pathlib import Path

P = Path("/opt/ispbilling/admin-portal/radius_network.py")
src = P.read_text(encoding="utf-8")

# Find the gate block.
m = re.search(
    r"if not res\.get\(\"success\"\):\s*\n"
    r"(.*?)\n"
    r"\s+tunnel_fault\s*=\s*looks_tunnel\s+and\s+\(([^)]+)\)\s*\n",
    src, re.DOTALL,
)
if not m:
    print("FAIL: could not locate tunnel_fault gate")
    sys.exit(2)

# Test each token must be present in the OR set so all failure modes trigger
gate_or = m.group(2)
required = ["no_route", "timed_out", "refused"]
missing = [t for t in required if t not in gate_or]
if missing:
    print("FAIL: tunnel_fault gate missing:", missing)
    print("  current gate:", gate_or)
    sys.exit(1)

# Each token must also be defined upstream with the right substrings.
defs = {
    "timed_out": ["timed out", "timeout", "errno 110"],
    "refused":   ["connection refused", "errno 111"],
    "no_route":  ["no route to host", "errno 113", "errno 101"],
}
errors = []
for tok, needles in defs.items():
    block = m.group(1)
    for needle in needles:
        if needle not in block:
            errors.append(f"{tok} definition missing match for {needle!r}")

# Confirm the unprovisioned branch exists
if "wg_handshake\"] = \"unprovisioned\"" not in src and 'wg_handshake"] = "unprovisioned"' not in src:
    errors.append("unprovisioned diagnostic branch missing")

if errors:
    print("FAIL:")
    for e in errors: print(" -", e)
    sys.exit(1)

print("PASS: test-connection diagnostic gate covers timeout/refused/no-route/unprovisioned.")
