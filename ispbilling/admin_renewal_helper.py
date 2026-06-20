"""
Admin Renewal Helper Functions
Provides reusable functions for admin subscription renewals (manual and automatic)
"""

from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from sqlalchemy import text
from sqlalchemy.orm import Session
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import os


def get_package_price(db: Session, package_name: str) -> float:
    """Get package price from SuperAdminPackage table"""
    from database import SuperAdminPackage
    
    package = db.query(SuperAdminPackage).filter(
        SuperAdminPackage.package_name == package_name,
        SuperAdminPackage.is_active == 1
    ).first()
    
    if not package:
        raise ValueError(f"Package '{package_name}' not found or inactive")
    
    return float(package.package_price)


def generate_admin_invoice_number(db: Session) -> str:
    """Generate unique invoice number for admin subscriptions"""
    invoice_counter = db.execute(
        text("SELECT COUNT(*) FROM invoices WHERE company_id = :company_id"),
        {"company_id": "SUPERADMIN"}
    ).scalar()
    
    return f"SA-INV-{(invoice_counter or 0) + 1:06d}"


def create_admin_invoice_pdf(invoice_data: dict, company_data: dict, customer_data: dict) -> bytes:
    """Generate invoice PDF for admin subscription"""
    from main import generate_invoice_pdf
    return generate_invoice_pdf(invoice_data, company_data, customer_data, [])


def send_admin_invoice_email(db: Session, admin_email: str, admin_name: str, invoice_no: str, 
                             pdf_path: str, company_id: str, admin_id: str, renewal_type: str = "renewal"):
    """Send invoice email to admin"""
    from main import get_global_smtp_settings
    
    smtp_settings = get_global_smtp_settings(db)
    if not smtp_settings or not smtp_settings.get('smtp_server'):
        print(f"Warning: SMTP not configured, skipping email for {admin_email}")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_settings['smtp_username']
        msg['To'] = admin_email
        
        if renewal_type == "new":
            msg['Subject'] = f"Welcome to Auto ISP Billing - Invoice {invoice_no}"
            body = f"""Dear {admin_name},

Welcome to Auto ISP Billing! Your account has been successfully created.

Your Login Credentials:
Company ID: {company_id}
Admin ID: {admin_id}
Login URL: https://www.autoispbilling.com/login

Please find your invoice attached. Payment is due within 7 days.

Thank you for choosing Auto ISP Billing!

Best regards,
Auto ISP Billing Team
Support: support@autoispbilling.com
Phone: +91-8085868114
"""
        elif renewal_type == "auto":
            msg['Subject'] = f"Auto ISP Billing - Subscription Renewal Invoice {invoice_no}"
            body = f"""Dear {admin_name},

Your Auto ISP Billing subscription has been automatically renewed.

Company ID: {company_id}
Invoice Number: {invoice_no}

Please find your renewal invoice attached. Your account status has been set to "Deactivated" until payment is received.

To reactivate your account, please make the payment at your earliest convenience.

Login URL: https://www.autoispbilling.com/login

Thank you for continuing with Auto ISP Billing!

Best regards,
Auto ISP Billing Team
Support: support@autoispbilling.com
Phone: +91-8085868114
"""
        else:  # manual renewal
            msg['Subject'] = f"Auto ISP Billing - Subscription Renewal Invoice {invoice_no}"
            body = f"""Dear {admin_name},

Your Auto ISP Billing subscription has been renewed.

Company ID: {company_id}
Invoice Number: {invoice_no}

Please find your renewal invoice attached. Your account status has been set to "Deactivated" until payment is received.

To reactivate your account, please make the payment at your earliest convenience.

Login URL: https://www.autoispbilling.com/login

Thank you for continuing with Auto ISP Billing!

Best regards,
Auto ISP Billing Team
Support: support@autoispbilling.com
Phone: +91-8085868114
"""
        
        msg.attach(MIMEText(body, 'plain'))
        
        with open(pdf_path, 'rb') as f:
            pdf_attachment = MIMEApplication(f.read(), _subtype='pdf')
            pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'{invoice_no}.pdf')
            msg.attach(pdf_attachment)
        
        smtp_port = int(smtp_settings['smtp_port'])
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_settings['smtp_server'], smtp_port) as server:
                server.login(smtp_settings['smtp_username'], smtp_settings['smtp_password'])
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_settings['smtp_server'], smtp_port) as server:
                server.starttls()
                server.login(smtp_settings['smtp_username'], smtp_settings['smtp_password'])
                server.send_message(msg)
        
        return True
    except Exception as e:
        print(f"Error sending invoice email to {admin_email}: {str(e)}")
        return False


def renew_admin_subscription(db: Session, company_id: str, months: int, method: str = "manual", start_date: datetime = None, gst_invoice_needed: bool = True) -> dict:
    """
    Renew admin subscription with invoice generation and email
    
    Args:
        db: Database session
        company_id: Company ID to renew
        months: Number of months to renew for
        method: 'manual' or 'auto'
        start_date: Optional custom start date for renewal period
        gst_invoice_needed: Whether to include GST in invoice (default: True)
    
    Returns:
        dict with success status, message, and invoice details
    """
    from database import Company, Admin, Invoice as AdminInvoice, RenewalLog
    
    try:
        company = db.query(Company).filter(Company.company_id == company_id).first()
        if not company:
            return {"success": False, "error": "Company not found"}
        
        admin = db.query(Admin).filter(Admin.company_id == company_id).first()
        if not admin:
            return {"success": False, "error": "Admin not found"}
        
        if company.admin_type == "Trial":
            return {"success": False, "error": "Cannot renew trial admins"}
        
        try:
            package_price = get_package_price(db, company.package)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        
        base_amount = package_price * months
        
        if gst_invoice_needed:
            gst_rate = 0.18  # 18% GST (9% CGST + 9% SGST)
            cgst_tax = base_amount * 0.09
            sgst_tax = base_amount * 0.09
            igst_tax = 0.0
            total_amount = base_amount * (1 + gst_rate)
        else:
            cgst_tax = 0.0
            sgst_tax = 0.0
            igst_tax = 0.0
            total_amount = base_amount
        
        if start_date:
            period_start = start_date
        else:
            current_end_date = company.end_date or datetime.now()
            if isinstance(current_end_date, str):
                current_end_date = datetime.strptime(current_end_date, '%Y-%m-%d')
            period_start = current_end_date if current_end_date > datetime.now() else datetime.now()
        
        period_end = period_start + relativedelta(months=months)
        
        existing_renewal = db.query(RenewalLog).filter(
            RenewalLog.company_id == company_id,
            RenewalLog.period_start == period_start,
            RenewalLog.period_end == period_end,
            RenewalLog.method == method
        ).first()
        
        if existing_renewal:
            return {
                "success": False,
                "error": f"Renewal already processed for this period ({method})",
                "duplicate": True
            }
        
        # Generate invoice number
        invoice_no = generate_admin_invoice_number(db)
        
        issue_date = datetime.now()
        due_date = issue_date + timedelta(days=7)
        
        admin_invoice = AdminInvoice(
            company_id="SUPERADMIN",
            customer_id=company_id,
            invoice_no=invoice_no,
            issue_date=issue_date.strftime('%Y-%m-%d'),
            due_date=due_date.strftime('%Y-%m-%d'),
            start_date=period_start.strftime('%Y-%m-%d'),
            end_date=period_end.strftime('%Y-%m-%d'),
            period_months=months,
            plan_id=None,
            plan_name=company.package,
            base_amount=base_amount,
            cgst_tax=cgst_tax,
            sgst_tax=sgst_tax,
            igst_tax=igst_tax,
            total_amount=total_amount,
            status='generated'
        )
        db.add(admin_invoice)
        db.flush()
        
        invoice_data = {
            'invoice_no': invoice_no,
            'issue_date': admin_invoice.issue_date,
            'due_date': admin_invoice.due_date,
            'start_date': admin_invoice.start_date,
            'end_date': admin_invoice.end_date,
            'plan_name': company.package,
            'period_months': months,
            'base_amount': base_amount,
            'cgst_tax': cgst_tax,
            'sgst_tax': sgst_tax,
            'igst_tax': igst_tax,
            'total_amount': total_amount,
            'customer_name': admin.admin_name,
            'prev_due_total': 0.0
        }
        
        from database import SuperAdminSettings
        settings = db.query(SuperAdminSettings).first()
        
        if settings:
            company_data = {
                'company_name': 'AUTO ISP BILLING',
                'company_address': settings.address or 'India',
                'company_phone': settings.contact_number or '+91-8085868114',
                'company_email': settings.contact_email or 'support@autoispbilling.com',
                'state': settings.state or 'Madhya Pradesh',
                'gst_number': settings.gst_number or '',
                'bank_name': settings.bank_name or '',
                'account_number': settings.account_no or '',
                'branch_ifsc': settings.ifsc_code or '',
                'upi_id': settings.upi_id or '',
                'declaration': settings.declaration or 'Thank you for choosing Auto ISP Billing. We look forward to serving you.',
                'terms_conditions': settings.terms_conditions or 'Payment is due within 7 days of invoice date. Late payments may result in service suspension.'
            }
        else:
            company_data = {
                'company_name': 'AUTO ISP BILLING',
                'company_address': 'India',
                'company_phone': '+91-8085868114',
                'company_email': 'support@autoispbilling.com',
                'state': 'Madhya Pradesh',
                'gst_number': '',
                'bank_name': '',
                'account_number': '',
                'branch_ifsc': '',
                'upi_id': '',
                'declaration': 'Thank you for choosing Auto ISP Billing. We look forward to serving you.',
                'terms_conditions': 'Payment is due within 7 days of invoice date. Late payments may result in service suspension.'
            }
        
        customer_data = {
            'customer_name': admin.admin_name,
            'username': admin.admin_id,
            'address': company.company_address or "",
            'customer_gst_no': company.gst_number if gst_invoice_needed else "",
            'state': company.state or "",
            'city': company.city or "",
            'mobile': admin.admin_mobile or "",
            'billing_type': 'PREPAID',
            'category': 'ISP Management Software',
            'gst_invoice_needed': 'Yes' if gst_invoice_needed else 'No'
        }
        
        # Generate PDF
        pdf_data = create_admin_invoice_pdf(invoice_data, company_data, customer_data)
        
        pdf_dir = "/var/lib/autoispbilling/invoices/SUPERADMIN"
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_path = f"{pdf_dir}/{invoice_no}.pdf"
        
        with open(pdf_path, 'wb') as f:
            f.write(pdf_data)
        
        admin_invoice.pdf_path = pdf_path
        
        company.end_date = period_end
        company.balance_amount = (company.balance_amount or 0.0) + total_amount
        company.status = "Deactivated"  # Set to Deactivated until payment
        company.last_renewal_date = datetime.now()
        company.period_months = months  # Update renewal period
        
        renewal_log = RenewalLog(
            company_id=company_id,
            months=months,
            invoice_id=admin_invoice.id,
            amount=total_amount,
            method=method,
            period_start=period_start,
            period_end=period_end
        )
        db.add(renewal_log)
        
        from database import Transaction
        transaction = Transaction(
            company_id="SUPERADMIN",
            customer_id=company_id,
            transaction_type='renewal',
            amount=total_amount,
            invoice_id=admin_invoice.id,
            start_date=period_start.strftime('%Y-%m-%d'),
            end_date=period_end.strftime('%Y-%m-%d'),
            period_months=months,
            remarks=f"Admin subscription renewal - {months} month(s)",
            payment_method=None,
            reference_no=invoice_no,
            note=f"Admin {method} renewal: {invoice_no}",
            transaction_date=datetime.now(),
            created_at=datetime.now()
        )
        db.add(transaction)
        
        db.commit()
        
        email_sent = False
        if admin.admin_email:
            renewal_type = "auto" if method == "auto" else "manual"
            email_sent = send_admin_invoice_email(
                db, admin.admin_email, admin.admin_name, invoice_no,
                pdf_path, company_id, admin.admin_id, renewal_type
            )
        
        return {
            "success": True,
            "message": f"Admin subscription renewed successfully for {months} month(s)",
            "invoice_no": invoice_no,
            "invoice_id": admin_invoice.id,
            "amount": total_amount,
            "new_end_date": period_end.strftime('%Y-%m-%d'),
            "new_balance": company.balance_amount,
            "email_sent": email_sent,
            "pdf_path": pdf_path
        }
        
    except Exception as e:
        db.rollback()
        print(f"Error renewing admin subscription: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}
