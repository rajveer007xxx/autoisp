"""Daily auto-renew cron.

Finds admins whose subscription end_date has passed and auto-renews them:
  1. creates a renewal invoice + transaction under SUPERADMIN namespace
  2. extends company.end_date by company.period_months (or 1 month default)
  3. adds the invoice total to company.balance_amount (new due)
  4. sets company.status = 'Deactivated' (because due > 0)
  5. generates the invoice PDF and emails it to the admin

Skipped:
  - Trial admins (package == 'Trial')
  - Soft-deleted admins (deleted_at IS NOT NULL)
  - Admins with auto_renew_enabled explicitly set to 0
  - The SUPERADMIN pseudo-row

Run manually:
    python3 /opt/ispbilling/superadmin-portal/auto_renew_cron.py

Installed as systemd timer isp-auto-renew.timer (daily at 02:00 IST).
"""
import os
import sys
from datetime import datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import text

# Make `database` and `main` importable when run as a script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import (
    SessionLocal,
    Company,
    Admin,
    Invoice,
    Transaction,
    SuperAdminPackage,
    SuperAdminSettings,
)

# Importing `main` defines a FastAPI app at module level but does not start
# a server, which is what we want: we only need the helpers below.
import main as sa_main


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def renew_one(db, company: Company) -> bool:
    """Run a single auto-renew. Returns True on success."""
    now = datetime.now()

    admin = db.query(Admin).filter(Admin.company_id == company.company_id).first()
    if not admin:
        _log(f"[SKIP] {company.company_id}: no admin row")
        return False

    package = (
        db.query(SuperAdminPackage)
        .filter(SuperAdminPackage.package_name == company.package)
        .first()
    )
    if not package:
        _log(
            f"[SKIP] {company.company_id}: package '{company.package}' not found"
        )
        return False

    period_months = int(company.period_months or 1)
    gst_invoice_needed = bool(company.gst_invoice_needed or 0)

    existing_end = company.end_date or now
    start_date = existing_end if existing_end > now else now
    end_date = start_date + relativedelta(months=period_months)

    price = float(package.package_price) * period_months
    cgst_rate = float(package.cgst_rate) if package.cgst_rate is not None else 9.0
    sgst_rate = float(package.sgst_rate) if package.sgst_rate is not None else 9.0
    igst_rate = float(package.igst_rate) if package.igst_rate is not None else 18.0

    sa_settings = db.query(SuperAdminSettings).first()
    sa_state = (sa_settings.state if sa_settings and sa_settings.state else "") or ""
    sa_state_name, _ = sa_main.get_state_info(sa_state)
    ad_state_name, _ = sa_main.get_state_info(company.state or "")
    intra_state = (
        bool(sa_state_name)
        and bool(ad_state_name)
        and sa_state_name.strip().lower() == ad_state_name.strip().lower()
    )

    if gst_invoice_needed:
        if intra_state:
            cgst = round(price * cgst_rate / 100.0, 2)
            sgst = round(price * sgst_rate / 100.0, 2)
            igst = 0.0
        else:
            cgst = 0.0
            sgst = 0.0
            igst = round(price * igst_rate / 100.0, 2)
        base_amt = round(price, 2)
        total_amt = round(base_amt + cgst + sgst + igst, 2)
    else:
        base_amt = round(price, 2)
        cgst = 0.0
        sgst = 0.0
        igst = 0.0
        total_amt = base_amt

    invoice_counter = db.execute(
        text("SELECT COUNT(*) FROM invoices WHERE company_id = :cid"),
        {"cid": "SUPERADMIN"},
    ).scalar()
    invoice_no = f"SA-INV-{(invoice_counter or 0) + 1:06d}"
    issue_date = now
    due_date = issue_date + relativedelta(days=7)

    inv = Invoice(
        company_id="SUPERADMIN",
        customer_id=company.company_id,
        invoice_no=invoice_no,
        issue_date=issue_date.strftime("%Y-%m-%d"),
        due_date=due_date.strftime("%Y-%m-%d"),
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        period_months=period_months,
        plan_id=None,
        plan_name=company.package,
        base_amount=base_amt,
        cgst_tax=cgst,
        sgst_tax=sgst,
        igst_tax=igst,
        total_amount=total_amt,
        status="generated",
    )
    db.add(inv)
    db.flush()

    txn = Transaction(
        company_id="SUPERADMIN",
        customer_id=company.company_id,
        transaction_type="renewal",
        amount=total_amt,
        invoice_id=inv.id,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        period_months=period_months,
        remarks=f"Auto-renewal - {period_months} month(s)",
        payment_method=None,
        reference_no=invoice_no,
        note=f"Admin auto-renewal: {invoice_no}",
        transaction_date=now,
        created_at=now,
    )
    db.add(txn)

    # Apply renewal to the company: extend expiry, add to due, deactivate
    company.end_date = end_date
    company.balance_amount = float(company.balance_amount or 0.0) + total_amt
    company.status = "Deactivated"
    company.last_renewal_date = now

    # Build the invoice PDF (same shape as manual renew)
    superadmin_gst = (
        sa_settings.gst_number if (sa_settings and gst_invoice_needed) else ""
    )
    invoice_data = {
        "invoice_no": invoice_no,
        "issue_date": issue_date.strftime("%Y-%m-%d"),
        "due_date": due_date.strftime("%Y-%m-%d"),
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date": end_date.strftime("%Y-%m-%d"),
        "plan_name": company.package,
        "period_months": period_months,
        "base_amount": base_amt,
        "cgst_tax": cgst,
        "sgst_tax": sgst,
        "igst_tax": 0.0,
        "total_amount": total_amt,
        "customer_name": admin.admin_name,
        "prev_due_total": 0.0,
    }
    company_data = {
        "company_name": "AUTO ISP BILLING",
        "company_address": "India",
        "company_phone": "+91-8085868114",
        "company_email": "support@autoispbilling.com",
        "state": "Madhya Pradesh",
        "gst_number": superadmin_gst,
        "bank_name": (sa_settings.bank_name if sa_settings else "") or "",
        "account_number": (sa_settings.account_no if sa_settings else "") or "",
        "branch_ifsc": (sa_settings.ifsc_code if sa_settings else "") or "",
        "upi_id": (sa_settings.upi_id if sa_settings else "") or "",
        "declaration": (sa_settings.declaration if sa_settings else "")
        or "Thank you for choosing Auto ISP Billing.",
        "terms_conditions": (sa_settings.terms_conditions if sa_settings else "")
        or "Payment is due within 7 days of invoice date.",
    }
    customer_data = {
        "customer_name": admin.admin_name or "",
        "username": admin.admin_id or "",
        "address": company.company_address or "",
        "customer_gst_no": (company.gst_number or "") if gst_invoice_needed else "",
        "state": company.state or "",
        "city": company.city or "",
        "mobile": admin.admin_mobile or "",
        "billing_type": "PREPAID",
        "category": "ISP Management Software",
        "gst_invoice_needed": "Yes" if gst_invoice_needed else "No",
    }

    pdf_data = None
    try:
        pdf_data = sa_main.generate_invoice_pdf(
            invoice_data, company_data, customer_data, []
        )
        pdf_dir = "/var/lib/autoispbilling/invoices/SUPERADMIN"
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_path = f"{pdf_dir}/{invoice_no}.pdf"
        with open(pdf_path, "wb") as f:
            f.write(pdf_data)
        inv.pdf_path = pdf_path
    except Exception as pdf_err:  # noqa: BLE001
        _log(
            f"[WARN] PDF generation failed for {company.company_id}: {pdf_err}"
        )

    db.commit()

    if admin.admin_email and pdf_data:
        admin_info = {
            "name": admin.admin_name,
            "email": admin.admin_email,
            "mobile": admin.admin_mobile,
            "company_id": company.company_id,
            "admin_id": admin.admin_id,
        }
        invoice_info = {
            "invoice_no": invoice_no,
            "issue_date": invoice_data["issue_date"],
            "due_date": invoice_data["due_date"],
            "start_date": invoice_data["start_date"],
            "end_date": invoice_data["end_date"],
            "package_name": company.package,
            "total_amount": total_amt,
        }
        try:
            sa_main.send_admin_welcome_email_background(
                db, admin_info, False, invoice_info, pdf_data, True
            )
        except Exception as mail_err:  # noqa: BLE001
            _log(
                f"[WARN] email failed for {company.company_id}: {mail_err}"
            )

    _log(
        f"[OK] {company.company_id} renewed {invoice_no} "
        f"{period_months}mo \u20b9{total_amt} end={end_date:%Y-%m-%d}"
    )
    return True


def main() -> int:
    db = SessionLocal()
    try:
        expired = (
            db.query(Company)
            .filter(
                Company.end_date != None,  # noqa: E711
                Company.end_date < datetime.now(),
                Company.deleted_at == None,  # noqa: E711
            )
            .all()
        )
        # Skip trial admins, the SUPERADMIN row, and admins with auto_renew off
        expired = [
            c
            for c in expired
            if (c.package or "").strip().lower() != "trial"
            and c.company_id != "SUPERADMIN"
            and (c.auto_renew_enabled is None or int(c.auto_renew_enabled) != 0)
        ]
        _log(f"auto-renew: found {len(expired)} expired admin(s)")
        ok = 0
        for c in expired:
            try:
                if renew_one(db, c):
                    ok += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                import traceback

                _log(f"[ERR] {c.company_id}: {e}")
                _log(traceback.format_exc())
        _log(f"auto-renew: done, {ok}/{len(expired)} renewed")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
