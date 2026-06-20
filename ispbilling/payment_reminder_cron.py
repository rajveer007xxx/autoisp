#!/usr/bin/env python3
"""Payment-reminder cron — dunn Expired customers at 3/7/14 days past invoice
issue. Sends branded reminders using services.billing.send_invoice_email_sync
with email_category="Payment Reminder".

Run daily via isp-payment-reminder.timer (02:00 IST = 20:30 UTC).

Logic:
    For each customer with status='Expired' AND auto_renew='Yes' AND at least
    one unpaid invoice:
      - Load oldest unpaid invoice (status in generated/overdue/partial).
      - days_since_issue = today_ist - invoice.issue_date
      - Stages:  3  →  "First reminder"
                 7  →  "Second reminder"
                14  →  "Final reminder (service will suspend)"
      - Skip stage if we've already logged (invoice_no, stage) in
        invoice_reminder_log (idempotent per stage per invoice).
      - On success, INSERT INTO invoice_reminder_log so we never re-send.
CLI:
    --as-of YYYY-MM-DD   override "today IST" (for testing / catch-up)
    --dry-run            log what would be sent, but do not send
    --stages 3,7,14      override stages to process (default: all three)
"""
import argparse
import os
import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from database import SessionLocal, Customer, Invoice, Company
from services.billing import send_invoice_email_sync


STAGE_LABEL = {
    3:  "First reminder",
    7:  "Second reminder",
    14: "Final reminder",
}


def ensure_log_table(db) -> None:
    """Idempotently create the per-invoice-per-stage reminder log table."""
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS invoice_reminder_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id  VARCHAR NOT NULL,
            customer_id VARCHAR NOT NULL,
            invoice_no  VARCHAR NOT NULL,
            stage       INTEGER NOT NULL,
            sent_at     DATETIME NOT NULL,
            email       VARCHAR,
            dry_run     INTEGER DEFAULT 0
        )
    """))
    db.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS
          uniq_inv_reminder_stage
          ON invoice_reminder_log(invoice_no, stage)
          WHERE dry_run = 0
    """))
    db.commit()


def today_ist(as_of: str = None) -> date:
    if as_of:
        return datetime.strptime(as_of, "%Y-%m-%d").date()
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _norm_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def already_sent(db, invoice_no: str, stage: int) -> bool:
    row = db.execute(text(
        "SELECT 1 FROM invoice_reminder_log "
        "WHERE invoice_no=:no AND stage=:st AND dry_run=0"
    ), {"no": invoice_no, "st": stage}).first()
    return row is not None


def record_sent(db, *, company_id, customer_id, invoice_no, stage,
                 email, dry_run: bool) -> None:
    db.execute(text(
        "INSERT INTO invoice_reminder_log "
        "(company_id, customer_id, invoice_no, stage, sent_at, email, dry_run) "
        "VALUES (:c, :cu, :no, :st, :t, :e, :d)"
    ), {
        "c": company_id, "cu": customer_id, "no": invoice_no,
        "st": stage, "t": datetime.utcnow().isoformat(),
        "e": email, "d": 1 if dry_run else 0,
    })
    db.commit()


def oldest_unpaid_invoice(db, customer_id: str, company_id: str):
    return (
        db.query(Invoice)
          .filter(Invoice.customer_id == customer_id,
                  Invoice.company_id  == company_id,
                  Invoice.status.in_(['generated', 'overdue', 'partial']))
          .order_by(Invoice.issue_date.asc(), Invoice.id.asc())
          .first()
    )


def build_company_data(db, company_id: str) -> dict:
    co = db.query(Company).filter(Company.company_id == company_id).first()
    if not co:
        return {'company_name': 'AUTO ISP BILLING'}
    return {
        'company_name':    co.company_name,
        'company_address': co.company_address or '',
        'company_phone':   co.company_phone or '',
        'company_email':   co.company_email or '',
        'bank_qr_code':    getattr(co, 'bank_qr_code', None),
        'upi_id':          getattr(co, 'upi_id', None),
    }


def prev_due_for(db, customer_id: str, company_id: str, exclude_no: str) -> float:
    """Sum of all unpaid invoices EXCEPT this one (feeds invoice_data.prev_due_total)."""
    rows = (db.query(Invoice.total_amount)
              .filter(Invoice.customer_id == customer_id,
                      Invoice.company_id  == company_id,
                      Invoice.invoice_no  != exclude_no,
                      Invoice.status.in_(['generated', 'overdue', 'partial']))
              .all())
    return float(sum(r[0] or 0 for r in rows))


def live_total_due(db, customer_id: str, company_id: str) -> float:
    """_S39R5R_ — The single authoritative outstanding figure used everywhere
    else in the app (mobile Dues, Pay screen, /pay/upi-link, GST receipt):

        max(0, total_bill_amount − received_amount − discount_credit)

    Reading from the live `customers` row guarantees the reminder email
    shows the same number the customer sees in the portal / mobile app
    and on the receipt — so the email body and any PDF can never disagree.
    """
    row = db.execute(text(
        "SELECT COALESCE(total_bill_amount,0) - COALESCE(received_amount,0) "
        "       - COALESCE(discount_credit,0) AS due "
        "  FROM customers WHERE customer_id=:cid AND company_id=:co"
    ), {"cid": customer_id, "co": company_id}).first()
    return max(0.0, float(row.due if row and row.due is not None else 0.0))


def process(as_of_str: str, dry_run: bool, stages: list) -> None:
    today = today_ist(as_of_str)
    print(f"\n{'='*78}\nPayment-Reminder Cron — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"today IST: {today}")
    print(f"dry_run:   {dry_run}")
    print(f"stages:    {stages}")
    print(f"{'='*78}\n")

    db = SessionLocal()
    try:
        ensure_log_table(db)

        customers = (db.query(Customer)
                       .filter(Customer.status == 'Expired',
                               Customer.auto_renew == 'Yes')
                       .all())
        if not customers:
            print("No Expired+auto_renew customers. Exiting.")
            return

        print(f"Scanning {len(customers)} Expired customers...\n")
        sent = skipped = 0
        for cust in customers:
            inv = oldest_unpaid_invoice(db, cust.customer_id, cust.company_id)
            if not inv:
                continue  # paid in full — no reminder needed

            issue_dt = _norm_date(inv.issue_date)
            if not issue_dt:
                print(f"  ⚠ {cust.customer_id}: invoice {inv.invoice_no} has unparseable issue_date={inv.issue_date!r} — skipping")
                continue
            age = (today - issue_dt).days

            # Pick the LATEST stage ≤ age that hasn't been sent yet.
            applicable = [s for s in stages if s <= age]
            if not applicable:
                continue
            # We dunn once per day — the highest unsent stage ≤ age.
            stage = None
            for s in sorted(applicable, reverse=True):
                if not already_sent(db, inv.invoice_no, s):
                    stage = s
                    break
            if stage is None:
                skipped += 1
                continue

            email = (cust.customer_email or '').strip()
            if not email:
                print(f"  ⊘ {cust.customer_id}: no email on file — skipped")
                continue

            # _S39R5R_ — Reminders must NOT carry the per-invoice PDF (its
            # total can differ from the customer's *current* outstanding,
            # because partial payments / cumulative dues are not reflected
            # on a single invoice document). We only show the live total
            # due in the email body — see live_total_due() below.
            pdf_data = b''

            # Compose invoice_data / company_data for the email helper.
            prev_due = prev_due_for(db, cust.customer_id, cust.company_id,
                                     exclude_no=inv.invoice_no)
            outstanding = live_total_due(db, cust.customer_id, cust.company_id)
            # _v4731_  Hard skip: never email a reminder when the live
            # running balance is zero (or below ₹1). The cron used to
            # send reminders solely based on invoice age, which made
            # paid-up customers receive nag mails when an old invoice
            # row still had status='generated' due to settlement quirks.
            if outstanding < 1.0:
                print(f"  ⊘ {cust.customer_id}: live due ₹{outstanding:.2f} → no reminder needed")
                skipped += 1
                continue
            invoice_data = {
                'invoice_no':     inv.invoice_no,
                'issue_date':     inv.issue_date,
                'start_date':     inv.start_date,
                'end_date':       inv.end_date,
                'total_amount':   inv.total_amount or 0,
                'prev_due_total': prev_due,
                'customer_name':  cust.customer_name or cust.username or 'Customer',
                'plan_name':      inv.plan_name or 'Broadband',
                'invoice_type':   'regular',
                # _S39R5R_ — Drives the headline "Total Amount" in the
                # reminder email + tells billing.py "this is a reminder,
                # don't attach a PDF and use this exact number".
                'reminder_outstanding': outstanding,
            }
            company_data = build_company_data(db, cust.company_id)
            billing_type = (cust.customer_type or 'PREPAID').upper()
            category_suffix = f"Payment Reminder ({STAGE_LABEL[stage]})"
            label = f"{cust.customer_id}[{cust.username}] → {email} | {inv.invoice_no} | age={age}d | stage={stage}"

            if dry_run:
                print(f"  [DRY] would send: {label}")
                record_sent(db, company_id=cust.company_id,
                             customer_id=cust.customer_id,
                             invoice_no=inv.invoice_no, stage=stage,
                             email=email, dry_run=True)
                sent += 1
                continue

            ok = send_invoice_email_sync(
                invoice_data=invoice_data,
                customer_email=email,
                company_data=company_data,
                pdf_data=pdf_data,
                billing_type=billing_type,
                email_category=category_suffix,
            )
            if ok:
                record_sent(db, company_id=cust.company_id,
                             customer_id=cust.customer_id,
                             invoice_no=inv.invoice_no, stage=stage,
                             email=email, dry_run=False)
                print(f"  ✓ SENT: {label}")
                sent += 1
            else:
                print(f"  ✗ FAIL: {label}")

        print(f"\n{'='*78}\nDone. sent={sent}  skipped={skipped}\n{'='*78}")
    finally:
        db.close()


def _parse():
    ap = argparse.ArgumentParser(description="Payment reminder cron")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD",
                    help="Override 'today IST' for this run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log what would be sent but don't actually send.")
    ap.add_argument("--stages", default="3,7,14",
                    help="Comma-separated day offsets (default: 3,7,14)")
    return ap.parse_args()


if __name__ == "__main__":
    args = _parse()
    stages = sorted({int(x.strip()) for x in args.stages.split(",") if x.strip()})
    process(as_of_str=args.as_of, dry_run=args.dry_run, stages=stages)
