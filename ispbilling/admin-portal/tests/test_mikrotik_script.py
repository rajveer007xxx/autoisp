#!/usr/bin/env python3
"""
Regression: ensure the generated Mikrotik script does NOT use
'place-before=0' (which fails silently on RouterOS 7 when the target
chain has 0 existing rules).
"""
import sys
sys.path.insert(0, "/opt/ispbilling/superadmin-portal/routes")
import sa_wg_tunnel as wg

script_p = wg._mikrotik_script("PRIV", "10.50.128.6", "SRV",
                                wg.DEFAULT_ENDPOINT_HOST, "51820", "secret")
script_f = wg._mikrotik_script("PRIV", "10.50.128.6", "SRV",
                                wg.DEFAULT_ENDPOINT_HOST, "443", "secret")

errors = []
for label, s in (("primary", script_p), ("fallback", script_f)):
    if "place-before=0" in s:
        errors.append(f"{label}: still contains 'place-before=0' (fails on empty chain)")
    # Required rules must be present
    must = [
        ("input chain accept",   "chain=input in-interface=cloud-tun action=accept"),
        ("forward in accept",    "chain=forward in-interface=cloud-tun action=accept"),
        ("forward out accept",   "chain=forward out-interface=cloud-tun action=accept"),
        ("srcnat masquerade",    "chain=srcnat src-address=10.50.0.0/16 action=masquerade"),
        ("self-cleaning prefix", "REMOVE any previous cloud-tun config"),
    ]
    for name, needle in must:
        if needle not in s:
            errors.append(f"{label}: missing {name!r} ({needle!r})")

if errors:
    print("FAIL:")
    for e in errors: print(" -", e)
    sys.exit(1)

print("PASS: Mikrotik script contains forward + NAT rules and no place-before=0.")
