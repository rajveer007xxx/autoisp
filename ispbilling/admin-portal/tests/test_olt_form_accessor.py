#!/usr/bin/env python3
"""
Regression test: ensure admin_olt_system.html does NOT use the buggy
`f.id.value = ...` or `f.<name>.value = ...` patterns inside oltOpen.

These patterns silently fail when the input's `name` collides with a
reserved property on HTMLFormElement (id, name, action, method, target,
elements, length, etc.). The safe replacement is f.elements.namedItem('x').

Run periodically (CI/cron) or manually after edits:
    python3 /opt/ispbilling/admin-portal/tests/test_olt_form_accessor.py
"""
import re
import sys
from pathlib import Path

TEMPLATE = Path("/opt/ispbilling/admin-portal/templates/admin_olt_system.html")
src = TEMPLATE.read_text(encoding="utf-8")

# Locate the oltOpen function body
m = re.search(r"function\s+oltOpen\s*\([^)]*\)\s*\{(.*?)\n\}\s*\n", src, re.DOTALL)
if not m:
    print("FAIL: could not locate oltOpen() function")
    sys.exit(2)
body = m.group(1)

errors = []

# Bug pattern 1: f.id.value = ...   (id is reserved on HTMLFormElement)
if re.search(r"\bf\.id\.value\s*=", body):
    errors.append("`f.id.value = ...` found — use f.elements.namedItem('id') instead")

# Bug pattern 2: f.name.value = ...  (name is reserved on HTMLFormElement)
if re.search(r"\bf\.name\.value\s*=", body):
    errors.append("`f.name.value = ...` found — use f.elements.namedItem('name') instead")

# Bug pattern 3: f[k].value = ... inside a loop iterating over field names
# This is dangerous because k may be 'id' / 'name' / 'action' etc.
# Allowed: f.elements.namedItem(k) or f.elements[k]
if re.search(r"\bf\[\s*k\s*\]\.(?:value|checked)\s*=", body):
    errors.append("`f[k].value/checked = ...` found — use f.elements.namedItem(k) instead")

if errors:
    print("FAIL: bug pattern(s) detected in oltOpen():")
    for e in errors:
        print(" - " + e)
    sys.exit(1)

print("PASS: oltOpen() uses safe form-element accessors.")
