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
    invoice_date_override: Optional[datetime] = None,  # __S44N__
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
        
        if company:
            print(f"DEBUG billing.py: company.smtp_server={company.smtp_server!r}, company.smtp_port={company.smtp_port!r}, company.smtp_username={company.smtp_username!r}, company.smtp_password={'SET' if company.smtp_password else 'NOT SET'}")
        else:
            print(f"DEBUG billing.py: company object is None for company_id={company_id}")
        
        tax_breakdown = compute_tax_breakdown(
            company.state if company else '',
            (getattr(customer,'billing_state','') or customer.state or ''),  # _S39R5FIX6_ — bill-state takes priority
            base_amount * period_months,
            cgst_rate=(plan.cgst_tax if plan else None),
            sgst_rate=(plan.sgst_tax if plan else None),
            igst_rate=(plan.igst_tax if plan else None),
        )
        
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
            received_tracker.last_reset_at = datetime.utcnow()
            received_tracker.updated_at = datetime.utcnow()
        else:
            received_tracker = ReceivedTracker(
                company_id=company_id,
                customer_id=customer_id,
                received_since_reset=0.0,
                last_reset_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(received_tracker)
        
        invoice_id = None
        invoice_no = None
        pdf_data = None
        
        if with_invoice:
            prev_due_balance = compute_customer_balance(customer_id, company_id, db, exclude_invoice_no=None)
            
            unpaid_invoices = db.query(Invoice).filter(
                Invoice.company_id == company_id,
                Invoice.customer_id == customer_id,
                Invoice.status.in_(['generated', 'overdue', 'partial'])
            ).order_by(Invoice.issue_date.asc(), Invoice.id.asc()).all()
            
            previous_invoices_data = []
            prev_due_total = round(max(0, prev_due_balance), 2)
            if unpaid_invoices and prev_due_total > 0:
                prev_invoice_nos = ', '.join([inv.invoice_no for inv in unpaid_invoices])
                previous_invoices_data.append({
                    'invoice_no': prev_invoice_nos,
                    'issue_date': unpaid_invoices[0].issue_date,
                    'total_amount': prev_due_total
                })
            
            invoice_no = generate_invoice_number(company_id, db)
            
            # __S44N__ Honour admin-chosen invoice date (defaults to today)
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
            # __MSG91_HOOK_INVOICE__  monthly billing cron
            try:
                from msg91_hooks import notify_invoice_generated as _mh_ni
                _mh_ni(invoice, customer, company)
            except Exception as _mh_e:
                print(f"[msg91 invoice hook billing] {_mh_e}")
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
                'terms_conditions': company.terms_conditions if company else 'Payment is due within 7 days of invoice date.',
                'smtp_server': company.smtp_server if company else '',
                'smtp_port': company.smtp_port if company else 587,
                'smtp_username': company.smtp_username if company else '',
                'smtp_password': company.smtp_password if company else '',
                'bank_qr_code': getattr(company, 'bank_qr_code', None) if company else None
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


def send_invoice_email_sync(invoice_data: dict, customer_email: str, company_data: dict, pdf_data: bytes, billing_type: str) -> bool:
    """
    Send invoice email synchronously (for use in cron jobs).
    
    Args:
        invoice_data: Invoice details dictionary
        customer_email: Customer email address
        company_data: Company details dictionary
        pdf_data: PDF file bytes
        billing_type: 'PREPAID' or 'POSTPAID'
    
    Returns:
        True if email sent successfully, False otherwise
    """
    
    def format_date_ddmmyyyy(date_str: str) -> str:
        """Convert date from YYYY-MM-DD to DD-MM-YYYY format"""
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%d-%m-%Y')
        except:
            try:
                dt = datetime.strptime(date_str, '%d-%m-%Y')
                return date_str
            except:
                return date_str
    
    try:
        customer_name = invoice_data.get('customer_name', 'Customer')
        invoice_no = invoice_data['invoice_no']
        issue_date = format_date_ddmmyyyy(invoice_data['issue_date'])
        due_date = format_date_ddmmyyyy(invoice_data['due_date'])
        
        current_amount = invoice_data.get('total_amount', 0)
        prev_due = invoice_data.get('prev_due_total', 0)
        total_due = round(current_amount + prev_due, 2)
        
        company_name = company_data.get('company_name', 'AUTO ISP BILLING')
        company_phone = company_data.get('company_phone', '')
        
        msg = MIMEMultipart()
        smtp_username = company_data.get('smtp_username', 'no-reply@autoispbilling.com')
        msg['From'] = f'INVOICE <{smtp_username}>'
        msg['To'] = customer_email
        msg['Subject'] = f'INVOICE - {invoice_no}'
        
        body = f"""Dear {customer_name},

Thank you for your subscription renewal. Please find your attached invoice.

Invoice Details:
- Invoice Number: {invoice_no}
- Issue Date: {issue_date}
- Due Date: {due_date}
- Total Amount Due: ₹{total_due}

Please make the payment asap to continue enjoying uninterrupted services.

Thank you for your business!
If you have any questions, please contact us @ {company_phone}.

Best regards,
{company_name}
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        pdf_attachment = MIMEApplication(pdf_data, _subtype='pdf')
        pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'invoice_{invoice_data["invoice_no"]}.pdf')
        msg.attach(pdf_attachment)
        
        smtp_server = company_data.get('smtp_server')
        smtp_port = int(company_data.get('smtp_port', 465))
        smtp_username = company_data.get('smtp_username')
        smtp_password = company_data.get('smtp_password')
        
        if not all([smtp_server, smtp_port, smtp_username, smtp_password]):
            print(f"✗ SMTP not configured for invoice {invoice_data['invoice_no']}: smtp_server={bool(smtp_server)}, smtp_port={smtp_port}, smtp_username={bool(smtp_username)}, smtp_password={bool(smtp_password)}")
            return False
        
        print(f"✓ Sending invoice email to {customer_email} using SMTP {smtp_server}:{smtp_port} from {smtp_username}")
        
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
        
        print(f"✓ Invoice email sent successfully to {customer_email}")
        return True
    except Exception as e:
        print(f"Failed to send invoice email: {str(e)}")
        return False
