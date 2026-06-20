#!/usr/bin/env python3
"""
wa_expiry_reminder.py — Session 27.6
Daily cron that sends WhatsApp expiry reminders to customers whose
subscription ends in the next 3 days.

Who gets a reminder?
  * customer's company has `enable_whatsapp_api = 1` (superadmin flag)
  * customer.end_date ∈ {today+1, today+2, today+3}   (IST)
  * customer.status is 'Active' or 'Auto Renew'       (no point nagging
    users who are already Expired / Suspended — they're in captive portal)
  * customer has a valid phone number
  * customer.do_not_send_whatsapp != 'Yes'

Idempotency: wal-expiry-reminder.state.json records per-customer reminder
send dates; if we've already sent today, we skip.  That way running the
script twice or under-/over-lapping timers doesn't spam users.

Invoke:
    /opt/ispbilling/venv/bin/python /opt/ispbilling/admin-portal/wa_expiry_reminder.py [--dry-run] [--force]

Logs:  /var/log/ispbilling/wa_expiry_reminder.log  (auto-created)
State: /var/lib/ispbilling/wa_expiry_reminder.state.json
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/ispbilling/admin-portal")

# --- config paths
LOG_DIR   = "/var/log/ispbilling"
STATE_DIR = "/var/lib/ispbilling"
LOG_PATH   = f"{LOG_DIR}/wa_expiry_reminder.log"
STATE_PATH = f"{STATE_DIR}/wa_expiry_reminder.state.json"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

IST = timezone(timedelta(hours=5, minutes=30))
NOW_IST  = datetime.now(IST)
TODAY    = NOW_IST.date()


def log(line: str) -> None:
    stamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    full = f"[{stamp}] {line}"
    print(full)
    with open(LOG_PATH, "a") as f:
        f.write(full + "\n")


def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
    except Exception as e:
        log(f"[warn] state save failed: {e}")


def parse_end_date(s: str):
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s.strip().split()[0], fmt).date()
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="compute recipient list but don't actually call Twilio")
    ap.add_argument("--force", action="store_true",
                    help="ignore state file — re-send even if already sent today")
    args = ap.parse_args()

    # import here so we reuse the portal's SQLAlchemy engine / models
    from database import SessionLocal, Customer, Company
    from msg91_whatsapp import send_plan_expiring_soon, normalise_phone

    db = SessionLocal()
    state = load_state()
    today_key = TODAY.isoformat()

    # companies with the superadmin flag ON
    allowed_companies = {
        c.company_id: c
        for c in db.query(Company).filter(
            Company.enable_whatsapp_api == 1).all()
    }
    log(f"start run — companies with WA enabled: {len(allowed_companies)}")

    if not allowed_companies:
        log("no companies eligible — exiting")
        return 0

    target_dates = {TODAY + timedelta(days=d): d for d in (1, 2, 3)}

    sent_ok  = 0
    sent_err = 0
    skipped  = 0
    scanned  = 0

    for comp_id, comp in allowed_companies.items():
        customers = db.query(Customer).filter(
            Customer.company_id == comp_id,
            Customer.status.in_(["Active", "Auto Renew"]),
        ).all()
        log(f"  company {comp_id} ({comp.company_name or ''}): "
            f"{len(customers)} Active/Auto-Renew customers")
        for c in customers:
            scanned += 1
            ed = parse_end_date(c.end_date or "")
            if not ed or ed not in target_dates:
                continue
            days_left = target_dates[ed]
            if (c.do_not_send_whatsapp or "").strip().lower() in ("yes", "true", "1"):
                skipped += 1
                log(f"    skip {c.customer_id} — opted out of WhatsApp")
                continue
            phone = (c.customer_phone or "").strip()
            if not phone:
                skipped += 1
                continue

            # idempotency key
            sk = f"{comp_id}:{c.customer_id}:{ed.isoformat()}"
            prior = state.get(sk) or {}
            if not args.force and prior.get("last_sent_date") == today_key:
                skipped += 1
                continue

            # compose message
            name = (c.customer_name or c.customer_id or "").strip()
            comp_name = (comp.company_name or "").strip()
            comp_phone = (comp.company_phone or "").strip()
            day_word = "tomorrow" if days_left == 1 else f"in {days_left} days"
            msg = (f"Hi {name}, this is a friendly reminder from "
                   f"{comp_name or 'your ISP'}: your internet subscription "
                   f"expires {day_word} ({ed.strftime('%d-%b-%Y')}). "
                   f"Please renew on time to avoid service interruption."
                   + (f" Support: {comp_phone}" if comp_phone else ""))

            if args.dry_run:
                log(f"    DRY RUN {c.customer_id} ({phone}) → "
                    f"{days_left}d left, template=plan_expiring_soon")
                continue

            to_ = normalise_phone(phone)
            if not to_:
                skipped += 1
                continue
            # Outstanding amount: best-effort, fall back to 0 if unavailable.
            outstanding = "0"
            try:
                from sqlalchemy import text as _t
                bal = db.execute(_t(
                    "SELECT COALESCE(total_bill_amount,0)+COALESCE(security_deposit,0)"
                    "+COALESCE(installation_charges,0)-COALESCE(discount_credit,0)"
                    "-COALESCE((SELECT SUM(amount)+SUM(discount) FROM payments "
                    " WHERE customer_id=:cid AND company_id=:co),0) AS due "
                    " FROM customers WHERE customer_id=:cid AND company_id=:co"
                ), {"cid": c.customer_id, "co": comp_id}).fetchone()
                if bal and bal[0] is not None:
                    due = float(bal[0]); outstanding = str(int(round(due))) if abs(due-round(due))<0.01 else f"{due:.2f}"
            except Exception:
                pass
            try:
                res = send_plan_expiring_soon(
                    phone=to_,
                    customer_name=name or 'Customer',
                    plan_name=(c.plan_name or 'Internet Plan'),
                    expiry_date=ed.strftime('%d-%b-%Y'),
                    outstanding=outstanding,
                    support_phone=(comp_phone or '+918085868114'),
                    company_name=(comp_name or 'AUTO ISP BILLING'),
                    company_address=(getattr(comp, 'company_address', '') or ''),
                    customer_id=c.customer_id,
                    company_id=comp_id,
                )
            except Exception as e:
                res = {"success": False, "error": str(e)}
            if res.get("success"):
                sent_ok += 1
                state[sk] = {
                    "last_sent_date": today_key,
                    "msg91_id": res.get("msg91_message_id"),
                    "log_id": res.get("log_id"),
                    "days_left": days_left,
                    "phone": phone,
                }
                log(f"    ✓ sent {c.customer_id} ({to_}) days_left={days_left} "
                    f"msg_id={res.get('msg91_message_id')}")
            # __s56t_email_fanout__ — also send email reminder.
            try:
                em = (getattr(c, "email", "") or "").strip()
                if em and (getattr(c, "do_not_send_email", "") or "").strip().lower() != "yes":
                    email_sk = f"email:{c.customer_id}:{days_left}:{today_key}"
                    if state.get(email_sk, {}).get("last_sent_date") != today_key:
                        from email_sender import send_plan_expiring_email as _send_exp_email
                        ok_em = _send_exp_email(
                            to_email=em,
                            customer_name=name or "Customer",
                            plan_name=(c.plan_name or "Internet Plan"),
                            expiry_date=ed.strftime("%d-%b-%Y"),
                            days_left=days_left,
                            outstanding_amount=float(outstanding) if str(outstanding).replace(".","",1).isdigit() else 0.0,
                            company_name=(comp_name or "AUTO ISP BILLING"),
                            company_phone=(comp_phone or ""),
                        )
                        if ok_em:
                            state[email_sk] = {"last_sent_date": today_key, "days_left": days_left, "email": em}
                            log(f"    ✓ EMAIL sent {c.customer_id} ({em}) days_left={days_left}")
                        else:
                            log(f"    ✗ EMAIL failed {c.customer_id} ({em}) days_left={days_left}")
            except Exception as _e_em:
                log(f"    ✗ EMAIL exception {c.customer_id}: {_e_em}")
            else:
                sent_err += 1
                log(f"    ✗ FAILED {c.customer_id} ({to_}) error={res}")

            # rate-limit 1 msg/sec to stay under Twilio WhatsApp throttles
            time.sleep(float(os.environ.get("TWILIO_RATE_LIMIT_PER_SEC", "1") or 1) ** -1
                       if False else 1.0)

    db.close()
    if not args.dry_run:
        save_state(state)
    log(f"finished — scanned={scanned}  sent_ok={sent_ok}  "
        f"sent_err={sent_err}  skipped={skipped}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"FATAL {type(e).__name__}: {e}")
        raise
