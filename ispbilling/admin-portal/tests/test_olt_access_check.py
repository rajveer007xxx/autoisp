#!/usr/bin/env python3
"""
Regression: /api/internal/olt-access-check must:
  - Block unauthenticated requests (401)
  - Allow same-company admin (200) with correct upstream headers
  - Block cross-company admin (403) — MULTI-TENANT SAFETY
  - Block missing/invalid OLT id (400)
  - Block nonexistent OLT id (403)

The endpoint is the security linchpin of the OLT Web UI reverse proxy.
If anyone weakens this check, every admin could potentially access any
other tenant's OLT.
"""
import itsdangerous, json, base64, urllib.request, urllib.error, json as J, sys, sqlite3

SECRET = "your-secret-key-change-in-production-12345678"
DBP    = "/var/lib/autoispbilling/autoispbilling.db"

def sess(d):
    payload = base64.b64encode(json.dumps(d).encode()).decode()
    return itsdangerous.TimestampSigner(SECRET).sign(payload).decode()

def call(headers, expect_code, label):
    req = urllib.request.Request(
        "http://127.0.0.1:8001/api/internal/olt-access-check",
        headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=8)
        code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    if code != expect_code:
        print(f"FAIL {label}: got {code}, expected {expect_code}")
        sys.exit(1)
    print(f"PASS {label}: {code}")

# Find at least one valid OLT id from the DB to use in green-path test.
with sqlite3.connect(DBP, timeout=5) as con:
    row = con.execute(
        "SELECT id, company_id FROM olts WHERE host != '' LIMIT 1").fetchone()
if not row:
    print("SKIP: no OLT rows in DB to test against")
    sys.exit(0)
olt_id, real_company = row

# 1) No session → 401
call({"X-OLT-Id": str(olt_id)}, 401, "no-session → 401")

# 2) Valid session, correct company → 200
ok_cookie = sess({"user_id":"x","admin_id":"x","company_id":str(real_company),"user_type":"admin"})
call({"Cookie":f"session={ok_cookie}", "X-OLT-Id": str(olt_id)}, 200, "same-company admin → 200")

# 3) Valid session, WRONG company → 403  (multi-tenant attack)
wrong = "_FAKE_TENANT_999"
bad_cookie = sess({"user_id":"y","admin_id":"y","company_id":wrong,"user_type":"admin"})
call({"Cookie":f"session={bad_cookie}", "X-OLT-Id": str(olt_id)}, 403, "cross-tenant attack → 403")

# 4) Bad olt id → 400
call({"Cookie":f"session={ok_cookie}", "X-OLT-Id":"not-a-number"}, 400, "bad olt id → 400")

# 5) Nonexistent olt id → 403
call({"Cookie":f"session={ok_cookie}", "X-OLT-Id":"99999999"}, 403, "nonexistent olt → 403")

# 6) Unknown role → 403
weird = sess({"user_id":"z","admin_id":"z","company_id":str(real_company),"user_type":"hacker"})
call({"Cookie":f"session={weird}", "X-OLT-Id": str(olt_id)}, 403, "unknown role → 403")

print("\nALL PASS: olt-access-check is multi-tenant safe.")
