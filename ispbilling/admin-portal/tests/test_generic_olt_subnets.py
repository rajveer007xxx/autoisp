#!/usr/bin/env python3
"""
Regression: ensure OLT-via-NAS plumbing is fully generic (no hardcoded
IP subnets in the runtime code paths). This protects against any future
edit that accidentally hardcodes a specific subnet.
"""
import sys
sys.path.insert(0, "/opt/ispbilling/superadmin-portal/routes")
import sa_wg_tunnel as wg

errors = []

# 1) _olt_subnet_from_host must derive correctly for diverse IPv4 ranges
cases = [
    ("192.168.22.107", "192.168.22.0/24"),
    ("10.10.1.5",      "10.10.1.0/24"),
    ("172.16.50.200",  "172.16.50.0/24"),
    ("192.168.5.10",   "192.168.5.0/24"),
    ("203.0.113.7",    "203.0.113.0/24"),
    ("100.64.1.99",    "100.64.1.0/24"),
]
for host, want in cases:
    got = wg._olt_subnet_from_host(host)
    if got != want:
        errors.append(f"_olt_subnet_from_host({host!r}) => {got!r}, expected {want!r}")

# 2) Empty / invalid input must NOT crash and must return empty string
for bad in ("", "not-an-ip", "10.10", "::1"):
    got = wg._olt_subnet_from_host(bad)
    if got != "":
        errors.append(f"_olt_subnet_from_host({bad!r}) => {got!r}, expected ''")

# 3) Mikrotik script must NOT reference any specific LAN subnet
script = wg._mikrotik_script("PRIV", "10.50.128.6", "SRV",
                              wg.DEFAULT_ENDPOINT_HOST, "51820", "secret")
# It's fine to reference 10.50.0.0/16 (the WG tunnel subnet) — that's universal.
# But there must be NO occurrence of any private LAN subnet token.
banned = ["192.168.22", "192.168.5", "192.168.168", "172.16.", "10.10.1"]
for needle in banned:
    if needle in script:
        errors.append(f"Mikrotik script contains hardcoded LAN ref {needle!r}")

# 4) Masquerade rule must filter by src-address=10.50.0.0/16, not by any LAN
if "chain=srcnat src-address=10.50.0.0/16 action=masquerade" not in script:
    errors.append("masquerade rule does not use canonical src-address=10.50.0.0/16 syntax")

# 5) Verify the sync helper is callable for any (cid, nas_id) shape
import inspect
sig = inspect.signature(wg.sync_via_nas_olt_routes)
params = list(sig.parameters)
if params[:2] != ["company_id", "nas_id"]:
    errors.append(f"sync_via_nas_olt_routes signature changed: {params}")

if errors:
    print("FAIL:")
    for e in errors: print(" -", e)
    sys.exit(1)
print("PASS: OLT-via-NAS plumbing is fully generic (no hardcoded LAN subnets).")
