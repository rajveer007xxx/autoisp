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
    
    body = f"""Dear {customer_name},

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
{company_address}
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
