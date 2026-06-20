"""patch_s36f_safe.py — Re-apply ONLY the safe parts of s36f to main.py.

The aggressive refactor (data-mgmt + notifications extraction) is dropped;
those stay in main.py for now.
"""
import os, shutil, datetime

ROOT = "/opt/ispbilling/admin-portal"
TS = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
main = os.path.join(ROOT, "main.py")


def _bak(p):
    if os.path.exists(p): shutil.copy2(p, f"{p}.bak_s36fsafe_{TS}")


_bak(main)
with open(main) as f: m = f.read()

if "# s36f-recovery-codes" in m:
    print("↷ already applied"); exit(0)

# Helper + /api/admin/totp/regenerate-codes endpoint (at tail)
helper = '''

# s36f-recovery-codes
def _s36f_gen_recovery_codes(n: int = 10) -> list:
    """Generate n human-friendly recovery codes of format XXXX-XXXX."""
    import secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    codes = []
    for _ in range(n):
        a = "".join(secrets.choice(alphabet) for _ in range(4))
        b = "".join(secrets.choice(alphabet) for _ in range(4))
        codes.append(f"{a}-{b}")
    return codes


def _s36f_hash_codes(plain: list) -> list:
    import hashlib
    return [hashlib.sha256(c.upper().strip().encode()).hexdigest() for c in plain]


def _s36f_check_recovery(admin, submitted: str) -> bool:
    """Returns True if `submitted` matches a stored recovery hash and
    consumes the code (removes it from the list)."""
    import json, hashlib
    if not admin or not (admin.totp_recovery_codes or "").strip():
        return False
    try:
        stored = json.loads(admin.totp_recovery_codes)
    except Exception:
        return False
    sub_hash = hashlib.sha256(submitted.upper().strip().encode()).hexdigest()
    if sub_hash in stored:
        stored.remove(sub_hash)
        admin.totp_recovery_codes = json.dumps(stored)
        return True
    return False


@app.post("/api/admin/totp/regenerate-codes")
async def api_admin_totp_regen_codes(request: Request, db: Session = Depends(get_db)):
    auth = require_admin(request)
    if auth: return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    from database import Admin
    import json
    body = await request.json()
    code = (body.get("code") or "").strip()
    admin = db.query(Admin).filter(
        Admin.admin_id == request.session.get("user_id"),
        Admin.company_id == request.session.get("company_id")).first()
    if not admin or not admin.totp_secret:
        return JSONResponse({"ok": False, "error": "2FA not enabled"}, status_code=400)
    import pyotp
    if not pyotp.TOTP(admin.totp_secret).verify(code, valid_window=1):
        return JSONResponse({"ok": False, "error": "invalid code"}, status_code=400)
    plain = _s36f_gen_recovery_codes(10)
    admin.totp_recovery_codes = json.dumps(_s36f_hash_codes(plain))
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id,
                           summary=f"regenerated {len(plain)} recovery codes")
    except Exception:
        pass
    return {"ok": True, "codes": plain}
'''

# Append before if __name__ == "__main__":
if 'if __name__ == "__main__":' in m:
    idx = m.rfind('if __name__ == "__main__":')
    m = m[:idx] + helper + "\n" + m[idx:]
else:
    m = m.rstrip() + helper + "\n"
print("✓ helper + regenerate-codes endpoint appended")

# Patch enable endpoint to return recovery codes on first enable
enable_old = '''    admin.totp_enabled = 1
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA enabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": True}'''
enable_new = '''    admin.totp_enabled = 1
    # s36f: generate one-time recovery codes at first-enable.
    import json as _json_s36f
    _plain_codes = None
    if not (admin.totp_recovery_codes or "").strip():
        _plain_codes = _s36f_gen_recovery_codes(10)
        admin.totp_recovery_codes = _json_s36f.dumps(_s36f_hash_codes(_plain_codes))
    db.commit()
    try:
        log_admin_activity(db, request, "update", "admin_totp",
                           target_id=admin.admin_id, summary="2FA enabled")
    except Exception:
        pass
    return {"ok": True, "totp_enabled": True, "recovery_codes": _plain_codes}'''
if enable_old in m:
    m = m.replace(enable_old, enable_new, 1)
    print("✓ enable returns recovery_codes")

# Patch login to accept recovery codes
login_old = '''            try:
                import pyotp
                tot = pyotp.TOTP(user.totp_secret)
                if not tot.verify(str(otp).strip(), valid_window=1):
                    return JSONResponse({"success": False, "mfa_required": True,
                        "message": "Invalid TOTP code"}, status_code=401)
            except Exception as _e:'''
login_new = '''            try:
                import pyotp
                tot = pyotp.TOTP(user.totp_secret)
                otp_stripped = str(otp).strip()
                if not tot.verify(otp_stripped, valid_window=1):
                    # s36f: fall back to recovery code consumption.
                    if _s36f_check_recovery(user, otp_stripped):
                        db.commit()
                    else:
                        return JSONResponse({"success": False, "mfa_required": True,
                            "message": "Invalid TOTP code"}, status_code=401)
            except Exception as _e:'''
if login_old in m:
    m = m.replace(login_old, login_new, 1)
    print("✓ login accepts recovery codes")

with open(main, "w") as f: f.write(m)
print("✓ main.py written")
