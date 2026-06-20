def generate_caf_pdf(customer_data: dict, company_data: dict) -> bytes:
    """Generate CAF (Customer Application Form) PDF matching the exact format with company branding"""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm, mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, KeepTogether
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    import os
    
    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        FONT_REGULAR = 'DejaVu'
        FONT_BOLD = 'DejaVu-Bold'
    except:
        FONT_REGULAR = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm,
                           leftMargin=2*cm, rightMargin=2*cm)
    
    story = []
    styles = getSampleStyleSheet()
    
    company_name_style = ParagraphStyle('CompanyName', parent=styles['Normal'], fontSize=14,
                                       fontName=FONT_BOLD, alignment=TA_CENTER, spaceAfter=2)
    company_address_style = ParagraphStyle('CompanyAddress', parent=styles['Normal'], fontSize=9,
                                          fontName=FONT_REGULAR, alignment=TA_CENTER, spaceAfter=8)
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=12,
                                fontName=FONT_BOLD, alignment=TA_CENTER, spaceAfter=10)
    section_header_style = ParagraphStyle('SectionHeader', parent=styles['Normal'], fontSize=10,
                                         fontName=FONT_BOLD, spaceAfter=4)
    label_style = ParagraphStyle('Label', parent=styles['Normal'], fontSize=9, fontName=FONT_REGULAR)
    value_style = ParagraphStyle('Value', parent=styles['Normal'], fontSize=9, fontName=FONT_REGULAR)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8, fontName=FONT_REGULAR)
    tiny_style = ParagraphStyle('Tiny', parent=styles['Normal'], fontSize=7, fontName=FONT_REGULAR, alignment=TA_CENTER)
    
    company_name = company_data.get('company_name', 'AUTO ISP BILLING').upper()
    company_address = company_data.get('company_address', '')
    company_city = company_data.get('city', '')
    company_state = company_data.get('state', '')
    
    logo_img = None
    logo_path = company_data.get('logo_path', '')
    if logo_path:
        if logo_path.startswith('/static/'):
            logo_path = os.path.join('/home/ubuntu/autoispbilling-payfast-repo', logo_path.lstrip('/'))
        if os.path.exists(logo_path):
            try:
                logo_img = Image(logo_path, width=5*cm, height=1.8*cm, kind='proportional')
            except:
                pass
    
    photo_box_data = [[Paragraph("Paste<br/>Passport Size<br/>Photograph", tiny_style)]]
    photo_box = Table(photo_box_data, colWidths=[3.5*cm], rowHeights=[4.5*cm])
    photo_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    
    center_elements = []
    if logo_img:
        center_elements.append(logo_img)
    center_elements.append(Paragraph(company_name, company_name_style))
    address_text = f"{company_address}"
    if company_city or company_state:
        address_text += f"<br/>{company_city}, {company_state}"
    center_elements.append(Paragraph(address_text, company_address_style))
    
    center_table = Table([[elem] for elem in center_elements], colWidths=[8*cm])
    center_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    caf_data = [[Paragraph(f"<b>CAF No.</b>", label_style)], 
                [Paragraph(customer_data.get('caf_no', ''), value_style)]]
    caf_box = Table(caf_data, colWidths=[4*cm])
    caf_box.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 3),
    ]))
    
    header_row = Table([[photo_box, center_table, caf_box]], colWidths=[4.5*cm, 8*cm, 4.5*cm])
    header_row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
    ]))
    story.append(header_row)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Customer Application Form</b>", title_style))
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Personal / Company Details</b>", section_header_style))
    personal_data = [
        [Paragraph("<b>Customer ID :</b>", label_style), Paragraph(customer_data.get('customer_id', ''), value_style),
         Paragraph("<b>User Name :</b>", label_style), Paragraph(customer_data.get('username', ''), value_style)],
        [Paragraph("<b>Name of the Customer :</b>", label_style), Paragraph(customer_data.get('customer_name', ''), value_style),
         Paragraph("<b>S/o, D/o, W/o :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Date of Birth :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>Gender :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Nationality :</b>", label_style), Paragraph('', value_style), '', ''],
    ]
    personal_table = Table(personal_data, colWidths=[4*cm, 4.5*cm, 3.5*cm, 5*cm])
    personal_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(personal_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Customer Address</b>", section_header_style))
    address_data = [
        [Paragraph("<b>Billing Address :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>City :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Pin Code :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>State :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Country :</b>", label_style), Paragraph('India', value_style), '', ''],
        [Paragraph("<b>Installation Address :</b>", label_style), Paragraph(customer_data.get('address', ''), value_style),
         Paragraph("<b>City :</b>", label_style), Paragraph(customer_data.get('city', ''), value_style)],
        [Paragraph("<b>Pin Code :</b>", label_style), Paragraph(customer_data.get('pincode', ''), value_style),
         Paragraph("<b>State :</b>", label_style), Paragraph(customer_data.get('state', ''), value_style)],
        [Paragraph("<b>Country :</b>", label_style), Paragraph('India', value_style),
         Paragraph("<b>Areacode :</b>", label_style), Paragraph(customer_data.get('locality', ''), value_style)],
    ]
    address_table = Table(address_data, colWidths=[4*cm, 4.5*cm, 3.5*cm, 5*cm])
    address_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(address_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Document Proof (Attach photocopies of address proof and Photo ID)</b>", section_header_style))
    doc_data = [
        [Paragraph("<b>Address Proof :</b>", label_style), Paragraph(customer_data.get('id_proof', ''), value_style),
         Paragraph("<b>Address Proof ID No. :</b>", label_style), Paragraph(customer_data.get('id_proof_no', ''), value_style)],
        [Paragraph("<b>Photo ID Proof :</b>", label_style), Paragraph(customer_data.get('id_proof', ''), value_style),
         Paragraph("<b>Photo ID No : :</b>", label_style), Paragraph(customer_data.get('id_proof_no', ''), value_style)],
    ]
    doc_table = Table(doc_data, colWidths=[4*cm, 4.5*cm, 4.5*cm, 4*cm])
    doc_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(doc_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Connection Details</b>", section_header_style))
    
    monthly_amount = float(customer_data.get('monthly_amount', 0))
    period = int(customer_data.get('period', 1))
    total_amount = monthly_amount * period
    security_deposit = float(customer_data.get('security_deposit', 0))
    installation_charges = float(customer_data.get('installation_charges', 0))
    customer_type = customer_data.get('customer_type', 'Prepaid').strip()
    
    if customer_type.lower() == 'postpaid':
        display_security = f"{monthly_amount:.2f}"
        display_plan_charges = ''
    else:
        display_security = f"{security_deposit:.2f}"
        display_plan_charges = f"{monthly_amount:.2f}"
    
    connection_data = [
        [Paragraph("<b>Customer Type :</b>", label_style), Paragraph(customer_type, value_style), '', ''],
        [Paragraph("<b>Installation Amount :</b>", label_style), Paragraph(f"{installation_charges:.0f}", value_style),
         Paragraph("<b>Security Deposite :</b>", label_style), Paragraph(display_security, value_style)],
        [Paragraph("<b>Plan Details :</b>", label_style), Paragraph(customer_data.get('plan_name', ''), value_style),
         Paragraph("<b>Plan Charges :</b>", label_style), Paragraph(display_plan_charges, value_style)],
        [Paragraph("<b>Bill Amount :</b>", label_style), Paragraph(f"{total_amount:.2f}", value_style),
         Paragraph("<b>Connection Type :</b>", label_style), Paragraph(customer_data.get('service_type', 'Broadband'), value_style)],
        ['', Paragraph("<b>(GST inclusive)</b>", small_style), '', Paragraph("<b>(GST exclusive)</b>", small_style)],
        [Paragraph("<b>Set Top Box No. :</b>", label_style), Paragraph('', value_style),
         Paragraph("<b>VC No. :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Modem No. :</b>", label_style), Paragraph(customer_data.get('modem_no', ''), value_style),
         Paragraph("<b>Modem No. Detail :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>MAC Address :</b>", label_style), Paragraph(customer_data.get('mac_address', ''), value_style),
         Paragraph("<b>MAC Address Detail :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>IP Addess :</b>", label_style), Paragraph(customer_data.get('ip_address', ''), value_style),
         Paragraph("<b>Vendor :</b>", label_style), Paragraph(customer_data.get('vendor', ''), value_style)],
        [Paragraph("<b>Under Scheme :</b>", label_style), Paragraph('', value_style), '', ''],
    ]
    connection_table = Table(connection_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    connection_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(connection_table)
    story.append(Spacer(1, 5*mm))
    
    story.append(Paragraph("<b>Contact Details</b>", section_header_style))
    contact_data = [
        [Paragraph("<b>Email :</b>", label_style), Paragraph(customer_data.get('customer_email', ''), value_style),
         Paragraph("<b>Alternate Email :</b>", label_style), Paragraph('', value_style)],
        [Paragraph("<b>Mobile :</b>", label_style), Paragraph(customer_data.get('customer_phone', ''), value_style),
         Paragraph("<b>Alternate Mobile :</b>", label_style), Paragraph(customer_data.get('alt_mobile', ''), value_style)],
        [Paragraph("<b>Registration Date :</b>", label_style), Paragraph(customer_data.get('installation_date', ''), value_style),
         Paragraph("<b>Landline No. :</b>", label_style), Paragraph('', value_style)],
    ]
    contact_table = Table(contact_data, colWidths=[4*cm, 4.5*cm, 4*cm, 4.5*cm])
    contact_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(contact_table)
    story.append(Spacer(1, 8*mm))
    
    story.append(Paragraph("<b>DECLARATION</b>", section_header_style))
    declaration_text = f"""• I, hereby declare that I have applied for a new Broadband Internet connection with M/s {company_name}.<br/>
• I submit that my installation address is the same as mentioned above and the documentary proof issued by Govt. of India evidencing the proof of my permanent residence is duly submitted herewith.<br/>
• I hereby submit that I reside in the Installation Address mentioned in the Customer Application Form (CAF) and the Broadband Internet Services to be subscribed by me shall be used for my own personal use. I undertake to indemnify {company_name} against any claims or legal actions that may arise in case of any misuse or any act contrary to the terms & conditions mentioned under the CAF."""
    story.append(Paragraph(declaration_text, label_style))
    story.append(Spacer(1, 8*mm))
    
    signature_data = [
        [Paragraph("<b>Customer Signature</b><br/>Mobile ({0})<br/>OTP Verified: YES".format(customer_data.get('customer_phone', '')), label_style),
         Paragraph("<b>Date:</b> {0}<br/>{1}".format(customer_data.get('installation_date', ''), ''), label_style),
         Paragraph("<b>Authorized Signatory</b><br/>{0}".format(company_name), label_style)],
        [Paragraph("<b>Place:</b> {0}".format(company_city), label_style), '', ''],
    ]
    signature_table = Table(signature_data, colWidths=[5.5*cm, 5.5*cm, 6*cm])
    signature_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(signature_table)
    story.append(Spacer(1, 8*mm))
    
    story.append(Paragraph("<b>Terms & Conditions</b>", section_header_style))
    terms_text = f"""<b>1. About</b><br/>
This Agreement for subscription of Broadband Internet and other value added services (Hereinafter referred to as 'Services') is entered between {company_name}, a Company incorporated under Companies act, 1956 having its registered office at {company_address}. {company_name} is licensed Internet Service Provider holding valid license issued by the Department of Telecommunications (DOT), Govt. of India. Any individual/ entity/legal person subscribing to the services offered by {company_name} are hereunder referred to as the 'subscriber'.<br/><br/>
<b>2. Service</b><br/>
{company_name} provides its services via Fiber optic cables, which requires us to install and power CX (Customer Switch) at the installation Address provided by me herein above. I accept this requirement and hereby accord the permission for installing this CX and give power for the same if required by {company_name}, so that {company_name} internet services may be installed and commissioned. The subscriber is also responsible to provide all access to equipment necessary to access the services. All the subsequent services manuals/packages/booklets etc. issued by {company_name} from time to time shall be binding on Subscriber. {company_name} reserves the right to modify and amend these terms and conditions in part or full and the amended one, as notified by {company_name} in its website, shall be binding on the subscriber. The Subscriber shall provide valid proof of address and proof of identity as per the direction issued by DOT from time to time to subscribe the {company_name} services and as and when required by {company_name}."""
    story.append(Paragraph(terms_text, small_style))
    
    doc.build(story)
    pdf_data = buffer.getvalue()
    buffer.close()
    
    return pdf_data
