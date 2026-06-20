import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from typing import Optional
import os

def send_payment_reminder_email(
    to_email: str,
    customer_name: str,
    balance_amount: float,
    payment_link: str = "",
    smtp_host: str = "smtp.hostinger.com",
    smtp_port: int = 465,
    smtp_user: str = "billing@ispbilling.in",
    smtp_password: str = "Login@121212"
):
    """Send payment reminder email to customer"""
    
    if not smtp_user or not smtp_password:
        raise ValueError("SMTP credentials not configured")
    
    msg = MIMEMultipart()
    msg['From'] = f"ISPBILLING <{smtp_user}>"
    msg['To'] = to_email
    msg['Subject'] = 'Payment Reminder'
    

    # _v4730_  Build the full address blocks for both company AND customer.
    def _addr_join(*parts):
        return ", ".join([p for p in parts if (p or "").strip()])
    _v4730_company_addr_full = _addr_join(company_address, company_city,
                                          company_state, company_pincode,
                                          company_country)
    _v4730_customer_addr_full = _addr_join(customer_address, customer_city,
                                           customer_state, customer_pincode,
                                           customer_country)
    body = f"""Dear {customer_name},

Your billing address on record:
{_v4730_customer_addr_full}


Please pay your pending amount of ₹{balance_amount:.2f}.

Choose Online Mode or Pay through Cash to Collection Agents.

{f'Payment Link: {payment_link}' if payment_link else ''}

Regards,
ISPBILLING
"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending payment reminder email: {str(e)}")
        return False

def send_invoice_email(
    to_email: str,
    customer_name: str,
    customer_id: str,
    invoice_number: str,
    pdf_buffer,
    company_name: str = "ISP Billing",
    company_mobile: str = "",
    company_address: str = "",
    company_city: str = "",
    company_state: str = "",
    company_pincode: str = "",
    company_country: str = "India",
    customer_address: str = "",
    customer_city: str = "",
    customer_state: str = "",
    customer_pincode: str = "",
    customer_country: str = "India",
    smtp_host: str = "smtp.gmail.com",
    smtp_port: int = 587,
    smtp_user: Optional[str] = None,
    smtp_password: Optional[str] = None
):
    """Send invoice email with PDF attachment"""
    
    smtp_user = smtp_user or os.getenv('SMTP_USER')
    smtp_password = smtp_password or os.getenv('SMTP_PASSWORD')
    
    if not smtp_user or not smtp_password:
        raise ValueError("SMTP credentials not configured")
    
    msg = MIMEMultipart()
    msg['From'] = f"ISPBILLING <{smtp_user}>"
    msg['To'] = to_email
    msg['Subject'] = 'Invoice'
    
    body = f"""Dear {customer_name},

Thank you for your subscription of Broadband for {customer_id}, please find attached your invoice.

This is an automatically generated message to confirm your subscription. Please do not reply to this e-mail, but you may wish to save it for your records.

Should you have any questions about your subscription, feel free to call {company_name} & {company_mobile}.

Regards,
{company_name}
{_v4730_company_addr_full}
"""
    
    msg.attach(MIMEText(body, 'plain'))
    
    pdf_attachment = MIMEApplication(pdf_buffer.read(), _subtype='pdf')
    pdf_attachment.add_header('Content-Disposition', 'attachment', filename=f'invoice_{invoice_number}.pdf')
    msg.attach(pdf_attachment)
    
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()
        
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False


# __s56t_plan_expiring_email__ — pre-expiry reminder (T-1 / T-2 / T-3)
def send_plan_expiring_email(
    to_email: str,
    customer_name: str,
    plan_name: str,
    expiry_date: str,
    days_left: int,
    outstanding_amount: float = 0.0,
    company_name: str = "AUTO ISP BILLING",
    company_phone: str = "",
    smtp_host: str = "smtp.hostinger.com",
    smtp_port: int = 465,
    smtp_user: str = "billing@ispbilling.in",
    smtp_password: str = "Login@121212",
):
    """Pre-expiry reminder. days_left ∈ {1, 2, 3}."""
    if not to_email:
        return False
    day_word = "tomorrow" if days_left == 1 else f"in {days_left} days"
    subject = f"Subscription expiring {day_word} — {company_name}"
    body_text = f"""Dear {customer_name},

This is a friendly reminder from {company_name}.

Your internet subscription is expiring {day_word} on {expiry_date}.
Plan: {plan_name}
{(f"Outstanding amount: ₹{outstanding_amount:,.2f}" if outstanding_amount and outstanding_amount > 0 else "")}

Please renew on time to avoid service interruption.
{(f"For support, call: {company_phone}" if company_phone else "")}

Regards,
{company_name}
"""
    body_html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;color:#0F172A;line-height:1.55;">
<div style="max-width:560px;margin:0 auto;padding:24px;background:#F8FAFC;border-radius:12px;">
<h2 style="color:#0891B2;margin:0 0 10px;">Your plan expires {day_word}</h2>
<p>Dear <strong>{customer_name}</strong>,</p>
<p>This is a friendly reminder from <strong>{company_name}</strong> — your internet subscription is set to expire <strong>{day_word}</strong> on <strong>{expiry_date}</strong>.</p>
<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #E2E8F0;border-radius:8px;margin:12px 0;">
  <tr><td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#475569;">Plan</td><td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;"><strong>{plan_name}</strong></td></tr>
  <tr><td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;color:#475569;">Expires on</td><td style="padding:10px 14px;border-bottom:1px solid #E2E8F0;"><strong>{expiry_date}</strong></td></tr>
  {(f'<tr><td style="padding:10px 14px;color:#475569;">Outstanding</td><td style="padding:10px 14px;"><strong style="color:#dc2626;">&#8377;{outstanding_amount:,.2f}</strong></td></tr>' if outstanding_amount and outstanding_amount > 0 else '')}
</table>
<p style="color:#475569;">Please renew on time to avoid service interruption.</p>
{(f'<p style="color:#475569;">For support, call <strong>{company_phone}</strong>.</p>' if company_phone else '')}
<p style="margin-top:20px;color:#94A3B8;font-size:12px;">Regards,<br>{company_name}</p>
</div></body></html>"""

    if not (smtp_user and smtp_password):
        print(f"[email_expiry] smtp not configured — would send to {to_email}")
        return False
    msg = MIMEMultipart('alternative')
    msg['From']    = f"{company_name} <{smtp_user}>"
    msg['To']      = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body_text, 'plain'))
    msg.attach(MIMEText(body_html, 'html'))
    try:
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port); server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"[email_expiry] send failed to {to_email}: {e}")
        return False
