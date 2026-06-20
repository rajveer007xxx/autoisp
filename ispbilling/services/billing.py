"""
Shared billing services for renewal and invoice generation.
This module contains core business logic used by both manual renewal (API endpoints)
and automated renewal (cron jobs).
"""

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication


def renew_customer_core(
    company_id: str,
    customer_id: str,
    start_date: datetime,
    period_months: int,
    with_invoice: bool,
    db: Session,
    source: str = "manual",
    invoice_date_override: "Optional[datetime]" = None,  # __S44N__
) -> Dict[str, Any]:
    """
    Core customer renewal logic shared between manual and auto-renewal.
    
    Args:
        company_id: Company ID for multi-tenant context
        customer_id: Customer ID to renew
        start_date: Start date for the new period
        period_months: Number of months to renew for
        with_invoice: Whether to generate an invoice
        db: Database session
        source: 'manual' or 'auto' for tracking
    
    Returns:
        Dictionary with success status, invoice details, and error messages
    """
    from database import Customer, Company, Plan, Invoice, Transaction, ReceivedTracker
    from main import compute_tax_breakdown, generate_invoice_number, generate_invoice_pdf, compute_customer_balance
    
    try:
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.company_id == company_id
        ).first()
        
        if not customer:
            return {"success": False, "message": "Customer not found"}
        
        # _S39R5FIX13_ — terminated/disabled/deactivated customers cannot be billed
        _terminal = ("Terminated", "Disabled", "Deactive")
        if (customer.status or "").strip() in _terminal:
            return {"success": False, "message": f"Customer is {customer.status}; renewal blocked."}
        
        # _S39R5FIX6_ — inclusive end (29-Apr +1mo = 28-May, not 29-May)
        from datetime import timedelta as _td_fix6
        end_date = start_date + relativedelta(months=period_months) - _td_fix6(days=1)
        
        plan = None
        plan_name = "Internet Service"
        base_amount = customer.monthly_amount or 0.0
        
        if customer.plan_id:
            plan = db.query(Plan).filter(
                Plan.id == customer.plan_id,
                Plan.company_id == company_id
            ).first()
            if plan:
                plan_name = plan.plan_name
                if plan.after_tax_amount:
                    base_amount = plan.base_amount or (plan.after_tax_amount / 1.18)
                else:
                    base_amount = plan.base_amount or base_amount
        
        company = db.query(Company).filter(
            Company.company_id == company_id
        ).first()
        
        # === S41R-RENEW-TAX-RATES ===
        # Pass the plan's own CGST/SGST/IGST percentages so the renewal
        # invoice always reflects the plan's tax config — regardless of
        # the customer'''s `gst_invoice_needed` flag. The flag only
        # decides whether seller / buyer GSTINs are PRINTED on the PDF
        # (handled in generate_invoice_pdf); it must NOT silently strip
        # the tax that the plan was created with.
        tax_breakdown = compute_tax_breakdown(
            company.state if company else '',
            (getattr(customer,'billing_state','') or customer.state or ''),  # _S39R5FIX6_ — bill-state takes priority
            base_amount * period_months,
            cgst_rate=(plan.cgst_tax if plan else None),
            sgst_rate=(plan.sgst_tax if plan else None),
            igst_rate=(plan.igst_tax if plan else None),
        )
        # === S41W-RECEIVED-RESET ===
        # Reset received_amount on the customer row AND
        # received_tracker.received_since_reset so the "Received" column
        # on /admin/users only ever reflects the CURRENT renewal cycle
        # (per user request 2026-05-16).
        try:
            from database import ReceivedTracker as _RT
            from datetime import datetime as _dtnow
            try:
                customer.received_amount = 0.0
            except Exception:
                pass
            _tr = db.query(_RT).filter(
                _RT.company_id == customer.company_id,
                _RT.customer_id == customer.customer_id).first()
            if _tr is None:
                _tr = _RT(company_id=customer.company_id,
                          customer_id=customer.customer_id,
                          received_since_reset=0.0,
                          last_reset_at=_dtnow.utcnow(),
                          updated_at=_dtnow.utcnow())
                db.add(_tr)
            else:
                _tr.received_since_reset = 0.0
                _tr.last_reset_at = _dtnow.utcnow()
                _tr.updated_at = _dtnow.utcnow()
            db.commit()
        except Exception as _r:
            print(f"[s41w-received-reset] {_r}")
            db.rollback()
        
        cgst_tax = tax_breakdown['cgst_tax']
        sgst_tax = tax_breakdown['sgst_tax']
        igst_tax = tax_breakdown['igst_tax']
        total_amount = tax_breakdown['total_amount']
        
        customer.start_date = start_date.strftime('%Y-%m-%d')
        customer.end_date = end_date.strftime('%Y-%m-%d')
        customer.period = period_months
        
        received_tracker = db.query(ReceivedTracker).filter(
            ReceivedTracker.company_id == company_id,
            ReceivedTracker.customer_id == customer_id
        ).first()
        
        if received_tracker:
            received_tracker.received_since_reset = 0.0
            received_tracker.last_reset_at = datetime.now()
            received_tracker.updated_at = datetime.now()
        else:
            received_tracker = ReceivedTracker(
                company_id=company_id,
                customer_id=customer_id,
                received_since_reset=0.0,
                last_reset_at=datetime.now(),
                updated_at=datetime.now()
            )
            db.add(received_tracker)
        
        invoice_id = None
        invoice_no = None
        pdf_data = None
        
        if with_invoice:
            # _S40zπ_PREV_BAL_FIX_  (permanent fix — see PRD entry)
            # The auto-renew invoice MUST always display the customer's running
            # account balance, regardless of sign:
            #   • Positive  → outstanding amount carried forward
            #   • Zero      → paid in full (still show the line for audit trail)
            #   • Negative  → advance / credit; reduces the new bill's Total Due
            # Never clamp to 0 — that hides credits and makes the running ledger
            # un-auditable. The PDF renderer (main.generate_invoice_pdf) reads
            # this list with key 'amount' (NOT 'total_amount').
            prev_due_balance = compute_customer_balance(customer_id, company_id, db, exclude_invoice_no=None)

            all_prior_invoices = db.query(Invoice).filter(
                Invoice.company_id == company_id,
                Invoice.customer_id == customer_id,
            ).order_by(Invoice.issue_date.asc(), Invoice.id.asc()).all()

            # Prefer unpaid (any non-final status) for the displayed label.
            # Fall back to the most recent invoice if everything is paid.
            unpaid_invoices = [
                i for i in all_prior_invoices
                if (i.status or '').lower() not in ('paid', 'cancelled', 'void')
            ]

            previous_invoices_data = []
            prev_due_total = round(float(prev_due_balance), 2)   # signed — preserves credit
            if all_prior_invoices:
                display_set = unpaid_invoices if unpaid_invoices else all_prior_invoices[-1:]
                _shown = display_set[:5]
                prev_invoice_nos = ', '.join([inv.invoice_no for inv in _shown])
                if len(display_set) > 5:
                    prev_invoice_nos += f", +{len(display_set) - 5} more"
                previous_invoices_data.append({
                    'invoice_no': prev_invoice_nos,
                    'issue_date': display_set[0].issue_date,
                    'amount': prev_due_total,         # ← key matches PDF reader
                    'total_amount': prev_due_total,   # ← compat for any older readers
                })
            
            invoice_no = generate_invoice_number(company_id, db)
            
            # __S44N__ Honour admin-chosen invoice date.
            issue_date = invoice_date_override or datetime.now()
            due_date = issue_date + timedelta(days=7)
            
            invoice = Invoice(
                company_id=company_id,
                customer_id=customer_id,
                invoice_no=invoice_no,
                issue_date=issue_date.strftime('%Y-%m-%d'),
                due_date=due_date.strftime('%Y-%m-%d'),
                start_date=start_date.strftime('%Y-%m-%d'),
                end_date=end_date.strftime('%Y-%m-%d'),
                period_months=period_months,
                plan_id=customer.plan_id,
                plan_name=plan_name,
                base_amount=base_amount * period_months,
                cgst_tax=cgst_tax,
                sgst_tax=sgst_tax,
                igst_tax=igst_tax,
                total_amount=total_amount,
                status='generated'
            )
            
            db.add(invoice)
            db.flush()
            invoice_id = invoice.id
            
            invoice_data = {
                'invoice_no': invoice_no,
                'issue_date': invoice.issue_date,
                'due_date': invoice.due_date,
                'start_date': invoice.start_date,
                'end_date': invoice.end_date,
                'plan_name': plan_name,
                'period_months': period_months,
                'base_amount': base_amount * period_months,
                'cgst_tax': cgst_tax,
                'sgst_tax': sgst_tax,
                'igst_tax': igst_tax,
                'total_amount': total_amount,
                'customer_name': customer.customer_name,
                'prev_due_total': prev_due_total
            }
            
            company_data = {
                'company_name': company.company_name if company else 'AUTO ISP BILLING',
                'company_address': company.company_address if company else '',
                'company_phone': company.company_phone if company else '',
                'company_email': company.company_email if company else '',
                'state': company.state if company else '',
                'gst_number': company.gst_number if company else '',
                'bank_name': company.bank_name if company else '',
                'account_number': company.account_number if company else '',
                'branch_ifsc': company.branch_ifsc if company else '',
                'upi_id': company.upi_id if company else '',
                'declaration': company.declaration if company else 'Thanks for your business. Hope you are enjoying our services.',
                'terms_conditions': company.terms_conditions if company else 'Payment is due within 7 days of invoice date.'
            }
            
            customer_data = {
                'customer_name': customer.customer_name,
                'username': customer.username,
                'address': customer.address or '',
                'customer_gst_no': customer.customer_gst_no or '',
                'state': customer.state or '',
                'city': customer.city or '',
                'mobile': customer.customer_phone or '',
                'billing_type': customer.customer_type or 'PREPAID',
                'category': customer.service_type or 'Broadband',
                'gst_invoice_needed': customer.gst_invoice_needed or 'No'
            }
            
            pdf_data = generate_invoice_pdf(invoice_data, company_data, customer_data, previous_invoices_data)
            
            import os
            pdf_dir = f"/var/lib/autoispbilling/invoices/{company_id}"
            os.makedirs(pdf_dir, exist_ok=True)
            pdf_path = f"{pdf_dir}/{invoice_no}.pdf"
            
            with open(pdf_path, 'wb') as f:
                f.write(pdf_data)
            
            invoice.pdf_path = pdf_path
            
            all_unpaid_invoices = db.query(Invoice).filter(
                Invoice.company_id == company_id,
                Invoice.customer_id == customer_id,
                Invoice.status.in_(['generated', 'overdue', 'partial'])
            ).all()
            
            total_due = sum(inv.total_amount for inv in all_unpaid_invoices)
            customer.total_bill_amount = total_due
        
        transaction = Transaction(
            company_id=company_id,
            customer_id=customer_id,
            transaction_type='renewal',
            amount=total_amount,
            invoice_id=invoice_id,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            period_months=period_months,
            remarks=f"Renewed for {period_months} month(s) ({source})" + (" with invoice" if with_invoice else " without invoice")
        )
        
        db.add(transaction)
        db.commit()
        
        return {
            "success": True,
            "message": f"Customer renewed successfully for {period_months} month(s)" + (" with invoice" if with_invoice else ""),
            "customer_id": customer_id,
            "start_date": customer.start_date,
            "end_date": customer.end_date,
            "invoice_no": invoice_no,
            "invoice_id": invoice_id,
            "transaction_id": transaction.id,
            "pdf_data": pdf_data,
            "total_amount": total_amount,
            "customer_email": customer.customer_email,
            "customer_type": customer.customer_type or 'PREPAID',
            "invoice_data": invoice_data if with_invoice else None,
            "company_data": company_data if with_invoice else None
        }
    
    except Exception as e:
        db.rollback()
        return {"success": False, "message": str(e)}


def _fmt_ddmmyyyy(value) -> str:
    """Convert any YYYY-MM-DD / DD-MM-YYYY (or date/datetime) into DD-MM-YYYY."""
    from datetime import datetime as _dt, date as _d
    if not value:
        return ''
    if isinstance(value, (_dt, _d)):
        return value.strftime('%d-%m-%Y')
    s = str(value).strip()
    for fmt in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return _dt.strptime(s, fmt).strftime('%d-%m-%Y')
        except ValueError:
            continue
    return s


def send_invoice_email_sync(invoice_data: dict, customer_email: str,
                             company_data: dict, pdf_data: bytes,
                             billing_type: str,
                             email_category: str = "Renewal Invoice") -> bool:
    """Send the renewal / invoice email synchronously (used by cron).

    Mirrors the manual-renewal template in admin-portal/main.py::send_invoice_email
    for look-and-feel parity:
      - "Your Broadband subscription has been renewed successfully..."
      - Due Date = issue_date + 7 days
      - Subscription Period = start to (end - 1 day) for Prepaid
      - Total Amount = amount_due = grand_total - payment_received
      - HTML body with optional Scan-to-Pay QR (if bank_qr_code on file)
      - Full company signature
    """
    import os
    from email.utils import formataddr
    from email.mime.image import MIMEImage
    from datetime import datetime as _dt, timedelta as _td

    try:
        from database import get_db, SuperAdmin
        db = next(get_db())

        # --- SMTP credentials (SuperAdmin-level settings, else env fallback) ---
        smtp_server = smtp_port = smtp_username = smtp_password = None
        try:
            superadmin = db.query(SuperAdmin).first()
            if superadmin and all([superadmin.smtp_server, superadmin.smtp_port,
                                   superadmin.smtp_username, superadmin.smtp_password]):
                smtp_server   = superadmin.smtp_server
                smtp_port     = superadmin.smtp_port
                smtp_username = superadmin.smtp_username
                smtp_password = superadmin.smtp_password
        except Exception as e:
            print(f"Error fetching global SMTP settings: {str(e)}")

        if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
            smtp_server   = os.getenv("SMTP_SERVER", "smtp.hostinger.com")
            smtp_port     = int(os.getenv("SMTP_PORT", "465"))
            smtp_username = os.getenv("SMTP_USERNAME", "no-reply@autoispbilling.com")
            smtp_password = os.getenv("SMTP_PASSWORD")
            if not smtp_password:
                raise ValueError("SMTP not configured.")

        # --- Company / customer / invoice context ---
        company_name  = (company_data.get('company_name') or 'AUTO ISP BILLING').strip()
        company_addr  = (company_data.get('company_address') or '').strip()
        company_phone = (company_data.get('company_phone') or '').strip()
        customer_name = (invoice_data.get('customer_name') or 'Customer').strip()
        invoice_no    = invoice_data.get('invoice_no', '')
        plan_name     = (invoice_data.get('plan_name') or 'Broadband').strip()

        # Service label: Cable TV vs Broadband
        _svc_raw = (invoice_data.get('service_type')
                    or invoice_data.get('category') or '').strip().lower()
        if _svc_raw == 'cable' or 'cable' in plan_name.lower():
            svc_label = 'Cable TV'
        else:
            svc_label = 'Broadband'
        package_name = plan_name or svc_label

        # Dates (all DD-MM-YYYY in output)
        issue_raw = invoice_data.get('issue_date')
        issue_date_fmt = _fmt_ddmmyyyy(issue_raw)

        # Due date = issue + 7 days
        due_date_fmt = issue_date_fmt
        try:
            issue_obj = None
            for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
                try:
                    issue_obj = _dt.strptime(str(issue_raw), fmt); break
                except ValueError: continue
            if issue_obj:
                due_date_fmt = (issue_obj + _td(days=7)).strftime('%d-%m-%Y')
        except Exception:
            pass

        start_date_fmt = _fmt_ddmmyyyy(invoice_data.get('start_date'))
        end_raw = invoice_data.get('end_date')
        end_date_fmt = ''
        try:
            if end_raw:
                end_obj = None
                for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
                    try:
                        end_obj = _dt.strptime(str(end_raw), fmt); break
                    except ValueError: continue
                if end_obj:
                    # _S39R5FIX10d_ — DB stores inclusive end; show as-is for both billing types
                    end_date_fmt = end_obj.strftime('%d-%m-%Y')
        except Exception:
            end_date_fmt = _fmt_ddmmyyyy(end_raw)

        period_line = ''
        # _S39R5U_PLAN_SECURITY_ — for postpaid first invoice show Plan Security (matches PDF)
        _is_postpaid_first = (bool(invoice_data.get('is_first_invoice', False))
                              and str(billing_type or '').upper() == 'POSTPAID')
        if _is_postpaid_first:
            period_line = f"- Plan Security: Initial security deposit\n"
        else:
            period_line = ""  # _S39R5FIX6_ — period already in PDF, omit from email

        # Amounts — match PDF "Total Due"
        current_amount    = float(invoice_data.get('total_amount') or 0)
        prev_due          = float(invoice_data.get('prev_due_total') or 0)
        payment_received  = float(invoice_data.get('payment_received') or 0)
        grand_total       = current_amount + prev_due
        amount_due        = int(round(max(0, grand_total - payment_received)))

        invoice_type = invoice_data.get('invoice_type', 'regular')
        is_first = bool(invoice_data.get('is_first_invoice', False))
        _is_reminder = str(email_category or '').strip().lower().startswith('payment reminder')
        # _S39R5R_ — Reminders must show the customer's CURRENT live
        # outstanding (same number on dashboard / mobile / receipt), NOT
        # the per-invoice grand_total math above, which can desync once
        # partial payments are recorded. The cron passes this in as
        # `invoice_data['reminder_outstanding']`. Fall back to the old
        # math only if the caller didn't supply it.
        if _is_reminder and invoice_data.get('reminder_outstanding') is not None:
            amount_due = int(round(max(0, float(invoice_data['reminder_outstanding']))))
        if _is_reminder:
            # Extract stage label if the caller passed "Payment Reminder (Foo)".
            import re as _re
            _m = _re.search(r'\((.+?)\)', str(email_category or ''))
            _stage_label = _m.group(1) if _m else 'Reminder'
            if 'final' in _stage_label.lower():
                head_line = (f"FINAL REMINDER — Invoice {invoice_no} is now {14}+ days overdue. "
                             f"Your {svc_label} service will be suspended if not cleared immediately.")
            elif 'second' in _stage_label.lower():
                head_line = (f"This is a reminder — Invoice {invoice_no} is overdue. "
                             f"Please settle to keep your {svc_label} service active.")
            else:
                head_line = (f"Friendly reminder — Invoice {invoice_no} is still unpaid. "
                             f"Please pay at your earliest to avoid service interruption.")
            inv_type_label = _stage_label
        elif invoice_type == 'addon':
            head_line = f"Thank you for your purchase from {company_name}. Please find the attached invoice for more details."
            inv_type_label = 'Invoice'
        elif is_first:
            head_line = f"Your {svc_label} subscription has been created and the initial invoice is attached."
            inv_type_label = 'Initial invoice'
        else:
            head_line = f"Your {svc_label} subscription has been renewed successfully. Please find the attached renewal invoice for more details."
            inv_type_label = 'Renewal invoice'

        # --- Text body (identical structure to manual-renewal template) ---
        _cta_line = (
            f"Please clear the outstanding amount immediately to avoid service suspension."
            if _is_reminder else
            f"Please make the payment against this {inv_type_label.lower()} so your account stays Active."
        )
        _closer = (
            f"We appreciate your prompt attention to this matter."
            if _is_reminder else
            f"Thank you for continuing with {company_name}!"
        )
        # _S39R5R_ — Reminder uses "Total Due" wording so it never gets
        # confused with a per-invoice "Total Amount" on a PDF.
        _amount_label = "Total Due" if _is_reminder else "Total Amount"
        text_body = f"""Dear {customer_name},

{head_line}

Invoice Details:
- Invoice No: {invoice_no}
- Issue Date: {issue_date_fmt}
- Due Date: Immediately
{period_line}- Package: {package_name}
- {_amount_label}: ₹{amount_due}

{_cta_line}

{_closer}

Best regards,
{company_name}
{company_addr}
{company_phone}
"""

        # --- HTML body (with Scan-to-Pay QR if available) ---
        qr_path = company_data.get('bank_qr_code', None)
        if qr_path and qr_path.startswith('/static/'):
            qr_path = qr_path.lstrip('/')
        has_qr = bool(qr_path and os.path.exists(qr_path))
        qr_block = (
            '<div style="text-align: center; margin: 20px 0;">'
            '<p style="font-weight: bold; margin-bottom: 10px;">Scan to Pay:</p>'
            '<img src="cid:payment_qr" alt="Payment QR Code" '
            'style="max-width: 200px; height: auto; border: 2px solid #ddd; padding: 10px;">'
            '</div>' if has_qr else ''
        )
        # _S39R5U_PLAN_SECURITY_ — postpaid first invoice replaces Subscription Period in HTML too
        if _is_postpaid_first:
            period_html = ('<p style="margin: 5px 0;"><strong>Plan Security:</strong> '
                           'Initial security deposit</p>')
        else:
            period_html = ""  # _S39R5FIX6_ — period already in PDF

        html_body = f"""<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <p>Dear {customer_name},</p>
    <p>{head_line}</p>
    <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid #4A9B9B; margin: 20px 0;">
        <h3 style="margin-top: 0; color: #4A9B9B;">Invoice Details:</h3>
        <p style="margin: 5px 0;"><strong>Invoice No:</strong> {invoice_no}</p>
        <p style="margin: 5px 0;"><strong>Issue Date:</strong> {issue_date_fmt}</p>
        <p style="margin: 5px 0;"><strong>Due Date:</strong> Immediately</p>
        {period_html}
        <p style="margin: 5px 0;"><strong>Package:</strong> {package_name}</p>
        <p style="margin: 5px 0; font-size: 18px; color: #d9534f;"><strong>{_amount_label}: ₹{amount_due}</strong></p>
    </div>
    {qr_block}
    <p>{_cta_line}</p>
    <p style="margin-top: 20px;">
        {_closer}<br><br>
        Best regards,<br>
        <strong>{company_name}</strong><br>
        {company_addr}<br>
        {company_phone}
    </p>
</body>
</html>"""

        # --- Assemble message (multipart/mixed → alternative(text+html) + pdf + qr) ---
        msg = MIMEMultipart()
        from_display = f"{company_name} {email_category}".strip()
        msg['From']    = formataddr((from_display, smtp_username))
        msg['To']      = customer_email
        msg['Subject'] = f"{company_name} {email_category} - {invoice_no}"

        msg_alt = MIMEMultipart('alternative')
        msg_alt.attach(MIMEText(text_body, 'plain'))
        msg_alt.attach(MIMEText(html_body, 'html'))
        msg.attach(msg_alt)

        # _S39R5R_ — Reminders MUST NOT attach the per-invoice PDF — its
        # printed total is the per-invoice grand_total which can disagree
        # with the live "Total Due" once partial payments / extra cycles
        # exist. The reminder email body already shows the authoritative
        # live outstanding (see amount_due override above). Only attach
        # PDF for genuine first / renewal / addon invoices.
        if (not _is_reminder) and pdf_data:
            pdf_attachment = MIMEApplication(pdf_data, _subtype='pdf')
            pdf_attachment.add_header('Content-Disposition', 'attachment',
                                      filename=f'invoice_{invoice_no}.pdf')
            msg.attach(pdf_attachment)

        if has_qr:
            try:
                with open(qr_path, 'rb') as fh:
                    qr_img = MIMEImage(fh.read())
                qr_img.add_header('Content-ID', '<payment_qr>')
                qr_img.add_header('Content-Disposition', 'inline', filename='payment_qr.png')
                msg.attach(qr_img)
            except Exception as qr_err:
                print(f"QR attach failed (non-fatal): {qr_err}")

        # --- Send ---
        if int(smtp_port) == 465:
            with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_username, smtp_password)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send invoice email: {str(e)}")
        return False
