#!/usr/bin/env python3
"""S43ZR — wa_reactivation_reminder.py

Sibling of wa_expiry_reminder.py — but fires AFTER expiry (renewal nudges).

Who gets a nudge?
  * customer's company has `enable_whatsapp_api=1` (superadmin flag)
  * customer.status IN ('Expired', 'Suspended', 'Inactive', 'Disabled')
  * customer.end_date == today, today-3, today-7  (IST)
  * customer.do_not_send_whatsapp != 'Yes'
  * customer has a non-empty mobile number

Idempotency: state file mirrors wa_expiry_reminder.py (per-customer
per-end-date per-day key).

Invoke (suggested daily 11:30 IST):
  /opt/ispbilling/venv/bin/python /opt/ispbilling/admin-portal/wa_reactivation_reminder.py
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/ispbilling/admin-portal")

LOG_DIR   = "/var/log/ispbilling"
STATE_DIR = "/var/lib/ispbilling"
LOG_PATH   = f"{LOG_DIR}/wa_reactivation_reminder.log"
STATE_PATH = f"{STATE_DIR}/wa_reactivation_reminder.state.json"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))
NOW_IST = datetime.now(IST)
TODAY = NOW_IST.date()
TODAY_KEY = TODAY.isoformat()


def log(line: str) -> None:
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    msg = f"[{stamp}] {line}"
    print(msg)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log(f"state save failed: {e}")


def parse_end_date(s: str):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.split(" ")[0], fmt).date()
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from database import SessionLocal, Customer, Company
    from msg91_whatsapp import normalise_phone
    # Reuse the existing plan_expiring_soon template; copy text adapted by
    # the caller. (We use the same template to keep MSG91 setup minimal.)
    from msg91_whatsapp import send_plan_expiring_soon  # noqa: F401

    db = SessionLocal()
    state = load_state()
    # offset → label
    OFFSETS = {0: "today", 3: "3-days-overdue", 7: "7-days-overdue"}
    target_dates = {TODAY - timedelta(days=k): v for k, v in OFFSETS.items()}

    log(f"=== reactivation reminder run: targeting {sorted(target_dates)} ===")

    comps = db.query(Company).filter(Company.enable_whatsapp_api == 1).all()
    log(f"companies WA-enabled: {len(comps)}")
    scanned = skipped = sent_ok = failed = 0

    for comp in comps:
        comp_id = comp.company_id
        cs = db.query(Customer).filter(
            Customer.company_id == comp_id,
            Customer.status.in_(("Expired", "Suspended", "Inactive", "Disabled")),
        ).all()
        log(f"  {comp_id} ({(comp.company_name or '').strip()}): {len(cs)} eligible")
        for c in cs:
            scanned += 1
            ed = parse_end_date(c.end_date or "")
            if not ed or ed not in target_dates:
                continue
            label = target_dates[ed]
            if (c.do_not_send_whatsapp or "").strip().lower() in ("yes", "true", "1"):
                skipped += 1; continue
            phone = (c.customer_phone or "").strip()
            if not phone:
                skipped += 1; continue
            sk = f"{comp_id}:{c.customer_id}:{ed.isoformat()}:{label}"
            prior = state.get(sk) or {}
            if not args.force and prior.get("last_sent_date") == TODAY_KEY:
                skipped += 1; continue
            name = (c.customer_name or c.customer_id or "").strip() or "Customer"
            comp_name = (comp.company_name or "").strip() or "your ISP"
            comp_phone = (comp.company_phone or "").strip() or "+918085868114"
            if args.dry_run:
                log(f"    DRY {c.customer_id} ({phone}) | {label} | exp {ed}")
                continue
            to_ = normalise_phone(phone)
            if not to_:
                skipped += 1; continue
            # Best-effort outstanding
            outstanding = "0"
            try:
                from sqlalchemy import text
                bal = db.execute(text(
                    "SELECT COALESCE(total_bill_amount,0)+COALESCE(security_deposit,0)"
                    "+COALESCE(installation_charges,0)-COALESCE(discount_credit,0)"
                    "-COALESCE((SELECT SUM(amount)+SUM(discount) FROM payments "
                    " WHERE customer_id=:cid AND company_id=:co),0) AS due "
                    "FROM customers WHERE customer_id=:cid AND company_id=:co"
                ), {"cid": c.customer_id, "co": comp_id}).fetchone()
                if bal and bal[0] is not None:
                    due = float(bal[0])
                    outstanding = (str(int(round(due))) if abs(due - round(due)) < 0.01
                                   else f"{due:.2f}")
            except Exception:
                pass
            try:
                # MSG91 template `plan_expiring_soon` is the closest match in this
                # tenant's template inventory; the template body is tailored for
                # generic expiry messaging, so we re-use it. (A bespoke
                # `plan_expired_renew` template can be created later.)
                res = send_plan_expiring_soon(
                    phone=to_,
                    customer_name=name,
                    plan_name=(c.plan_name or "Internet Plan"),
                    expiry_date=ed.strftime("%d-%b-%Y"),
                    outstanding=outstanding,
                    support_phone=comp_phone,
                    company_name=comp_name,
                    company_address=(getattr(comp, "company_address", "") or ""),
                    customer_id=c.customer_id,
                    company_id=comp_id,
                )
            except Exception as e:
                res = {"success": False, "error": str(e)}
            if res.get("success"):
                sent_ok += 1
                state[sk] = {"last_sent_date": TODAY_KEY,
                             "message_id": res.get("message_id") or "",
                             "phase": label}
                log(f"    SENT {c.customer_id} ({to_}) | {label}")
            else:
                failed += 1
                log(f"    FAIL {c.customer_id} ({to_}) | {label} | {res.get('error')}")
    save_state(state)
    log(f"=== summary: scanned={scanned} skipped={skipped} "
        f"sent_ok={sent_ok} failed={failed} ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"FATAL: {e}")
        sys.exit(1)
