"""
Fibernet Invoice Template - Complete Implementation
This module contains the refactored invoice PDF generation function that matches the Fibernet template exactly.
"""

def generate_invoice_pdf_fibernet(invoice_data: dict, company_data: dict, customer_data: dict, previous_invoices: list = None) -> bytes:
    """Generate invoice PDF matching Fibernet template exactly (100%)"""
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.lib.units import inch, mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from io import BytesIO
    from datetime import datetime, timedelta
    from dateutil.relativedelta import relativedelta
    import os
    
    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        FONT_REGULAR = 'DejaVu'
        FONT_BOLD = 'DejaVu-Bold'
    except:
        FONT_REGULAR = 'Helvetica'
        FONT_BOLD = 'Helvetica-Bold'
    
    prev_due_total = sum(inv.get('total_amount', 0) for inv in (previous_invoices or []))
    grand_total = invoice_data['total_amount'] + prev_due_total
    
    show_gst = (customer_data.get('gst_invoice_needed', '').lower() == 'yes')
    customer_type = customer_data.get('customer_type', 'Postpaid')
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=15*mm, bottomMargin=15*mm, 
                           leftMargin=15*mm, rightMargin=15*mm)
    
    story = []
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('InvoiceTitle', parent=styles['Heading1'], 
                                 fontSize=16, fontName=FONT_BOLD, alignment=TA_CENTER,
                                 spaceAfter=3*mm, textColor=colors.black)
    
    brand_style = ParagraphStyle('BrandName', parent=styles['Heading1'],
                                fontSize=24, fontName=FONT_BOLD, alignment=TA_CENTER,
                                textColor=colors.HexColor('#0099FF'), spaceAfter=3*mm)
    
    normal_style = ParagraphStyle('Normal', parent=styles['Normal'], 
                                  fontSize=9, fontName=FONT_REGULAR, leading=11)
    
    bold_style = ParagraphStyle('Bold', parent=styles['Normal'], 
                                fontSize=9, fontName=FONT_BOLD, leading=11)
    
    small_style = ParagraphStyle('Small', parent=styles['Normal'], 
                                 fontSize=8, fontName=FONT_REGULAR, leading=10)
    
    def format_date_dd_mm_yyyy(date_val):
        if isinstance(date_val, str):
            try:
                if '-' in date_val:
                    parts = date_val.split('-')
                    if len(parts[0]) == 4:  # YYYY-MM-DD
                        dt = datetime.strptime(date_val, '%Y-%m-%d')
                        return dt.strftime('%d/%m/%Y')
                    else:  # DD-MM-YYYY
                        dt = datetime.strptime(date_val, '%d-%m-%Y')
                        return dt.strftime('%d/%m/%Y')
            except:
                pass
        return date_val
    
    def number_to_words_indian(num):
        if num == 0:
            return "Zero"
        
        ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
        tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
        teens = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", 
                "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
        
        def convert_below_hundred(n):
            if n < 10:
                return ones[n]
            elif n < 20:
                return teens[n - 10]
            else:
                return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")
        
        def convert_below_thousand(n):
            if n < 100:
                return convert_below_hundred(n)
            else:
                return ones[n // 100] + " Hundred" + (" " + convert_below_hundred(n % 100) if n % 100 != 0 else "")
        
        num = int(num)
        if num < 1000:
            return convert_below_thousand(num) + " ONLY"
        
        crore = num // 10000000
        num %= 10000000
        lakh = num // 100000
        num %= 100000
        thousand = num // 1000
        num %= 1000
        
        result = []
        if crore > 0:
            result.append(convert_below_thousand(crore) + " Crore")
        if lakh > 0:
            result.append(convert_below_thousand(lakh) + " Lakh")
        if thousand > 0:
            result.append(convert_below_thousand(thousand) + " Thousand")
        if num > 0:
            result.append(convert_below_thousand(num))
        
        return " ".join(result) + " ONLY"
    
    story.append(Paragraph("INVOICE", title_style))
    
    logo_path = company_data.get('logo_path')
    if logo_path and os.path.exists(logo_path):
        try:
            from reportlab.platypus import Image as RLImage
            logo = RLImage(logo_path, width=40*mm, height=15*mm)
            story.append(logo)
            story.append(Spacer(1, 2*mm))
        except:
            brand_name = company_data.get('company_name', 'AUTO ISP BILLING').upper()
            story.append(Paragraph(f"<font color='#0099FF'><b>{brand_name}</b></font>", brand_style))
            story.append(Spacer(1, 3*mm))
    else:
        brand_name = company_data.get('company_name', 'AUTO ISP BILLING').upper()
        story.append(Paragraph(f"<font color='#0099FF'><b>{brand_name}</b></font>", brand_style))
        story.append(Spacer(1, 3*mm))
    
    issue_date_formatted = format_date_dd_mm_yyyy(invoice_data['issue_date'])
    due_date_formatted = format_date_dd_mm_yyyy(invoice_data.get('due_date', invoice_data['issue_date']))
    
    company_name = company_data.get('company_name', 'AUTO ISP BILLING')
    company_state = company_data.get('state', '')
    # _S39R5FIX6b_ — show GSTIN line only when GST invoice + company has GSTIN
    _company_gstin_line = (f"GSTIN - {company_data.get('gst_number')}<br/>"
                           if (show_gst and company_data.get('gst_number')) else "")
    company_info_text = (
        f"<b>{company_name}</b><br/>"
        f"{company_data.get('company_address', '')}<br/>"
        f"Mobile No: {company_data.get('company_phone', '')}<br/>"
        + _company_gstin_line +
        f"E-Mail: {company_data.get('company_email', '')}<br/>"
        f"Website: {company_data.get('website', 'www.autoispbilling.com')}<br/>"
        f"State: {company_state}"
    )
    
    invoice_details_text = f"<b>Invoice No</b><br/>{invoice_data['invoice_no']}<br/><br/>" \
                          f"<b>Dated</b><br/>{issue_date_formatted}<br/><br/>" \
                          f"<b>Total Due Amount</b><br/>₹ {grand_total:.0f}<br/><br/>" \
                          f"<b>Due Date</b><br/>{due_date_formatted if due_date_formatted != issue_date_formatted else 'Immediately'}"
    
    header_data = [[
        Paragraph(company_info_text, small_style),
        Paragraph(invoice_details_text, small_style)
    ]]
    
    header_table = Table(header_data, colWidths=[100*mm, 80*mm])
    header_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 3*mm),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 2*mm))
    
    # _S39R5FIX6b_ — customer GSTIN line only when GST invoice + customer has GSTIN
    _cust_gstin_line = (f"<b>GSTIN -</b> {customer_data.get('customer_gst_no')}<br/>"
                        if (show_gst and customer_data.get('customer_gst_no')) else "")
    # Use billing_state if set, else fall back to installation state
    _disp_state = customer_data.get('billing_state') or customer_data.get('state') or 'Madhya Pradesh'
    buyer_info_text = (
        f"<b>Buyer</b><br/>"
        f"{customer_data.get('customer_name', '')} ({customer_data.get('customer_phone', '')})<br/>"
        f"Username: {customer_data.get('username', '')}<br/>"
        f"Locality: {customer_data.get('locality', '')}<br/>"
        f"Category: {customer_data.get('customer_type', 'Broadband')}<br/>"
        f"<b>BILLING ADDRESS:</b> {customer_data.get('billing_address') or customer_data.get('address', '')}<br/>"
        f"<b>Company Name:</b> {customer_data.get('company_name', 'N/A')}<br/>"
        + _cust_gstin_line +
        f"<b>State:</b> {_disp_state}<br/>"
        f"<b>Code:</b> {customer_data.get('state_code', '23')}"
    )
    
    buyer_data = [[Paragraph(buyer_info_text, small_style)]]
    buyer_table = Table(buyer_data, colWidths=[180*mm])
    buyer_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 3*mm),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(buyer_table)
    story.append(Spacer(1, 2*mm))
    
    try:
        # _S39R5FIX10c_ — DB stores inclusive end day; show as-is
        start_date_dt = datetime.strptime(invoice_data['start_date'], '%Y-%m-%d')
        end_date_dt   = datetime.strptime(invoice_data['end_date'],   '%Y-%m-%d')
        period_str = f"PERIOD {start_date_dt.strftime('%d/%m/%Y')} TO {end_date_dt.strftime('%d/%m/%Y')}"
    except Exception:
        period_str = "PERIOD N/A"
    
    items_data = [[
        Paragraph("<b>S.NO</b>", bold_style),
        Paragraph("<b>Description of Goods</b>", bold_style),
        Paragraph("<b>HSN/SAC</b>", bold_style),
        Paragraph("<b>Quantity</b>", bold_style),
        Paragraph("<b>Rate Per</b>", bold_style),
        Paragraph("<b>Disc. %</b>", bold_style),
        Paragraph("<b>Amount</b>", bold_style)
    ]]
    
    hsn_sac = "998422"
    quantity = "1 nos"
    rate_per = invoice_data['total_amount']
    disc_percent = "0"
    amount = invoice_data['total_amount']
    
    plan_name = invoice_data.get('plan_name', 'ISP Service')
    items_data.append([
        Paragraph("1", normal_style),
        Paragraph(f"{plan_name}<br/>{period_str}", small_style),
        Paragraph(hsn_sac, normal_style),
        Paragraph(quantity, normal_style),
        Paragraph(f"{rate_per:.0f}", normal_style),
        Paragraph(disc_percent, normal_style),
        Paragraph(f"{amount:.0f}", normal_style)
    ])
    
    if previous_invoices and prev_due_total > 0:
        prev_invoice_nos = ", ".join([inv.get('invoice_no','') for inv in previous_invoices[:2] if inv.get('invoice_no')])
        if len(previous_invoices) > 2:
            prev_invoice_nos += f" +{len(previous_invoices)-2} more"
        # _S39R5FIX6_ — "Previous Due (Inv X, Y)" or just "Previous Due" if no inv-no
        prev_label = f"Previous Due<br/>({prev_invoice_nos})" if prev_invoice_nos else "Previous Due"
        items_data.append([
            Paragraph("2", normal_style),
            Paragraph(prev_label, small_style),
            Paragraph(hsn_sac, normal_style),
            Paragraph("1 nos", normal_style),
            Paragraph(f"{prev_due_total:.0f}", normal_style),
            Paragraph("0", normal_style),
            Paragraph(f"{prev_due_total:.0f}", normal_style)
        ])
    
    items_data.append([
        Paragraph("", normal_style),
        Paragraph("", normal_style),
        Paragraph("", normal_style),
        Paragraph("", normal_style),
        Paragraph("", normal_style),
        Paragraph("<b>Total</b>", bold_style),
        Paragraph(f"<b>{grand_total:.0f}</b>", bold_style)
    ])
    
    items_table = Table(items_data, colWidths=[12*mm, 70*mm, 20*mm, 20*mm, 20*mm, 15*mm, 23*mm])
    items_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
        ('LINEABOVE', (-2, -1), (-1, -1), 1, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E0E0E0')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 1), (1, -2), 'LEFT'),
        ('ALIGN', (-2, -1), (-1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('PADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 2*mm))
    
    amount_words = number_to_words_indian(grand_total)
    amount_words_data = [[Paragraph(f"<b>Amount Chargeable (in words) E. & O.E</b><br/>{amount_words}", small_style)]]
    amount_words_table = Table(amount_words_data, colWidths=[180*mm])
    amount_words_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('PADDING', (0, 0), (-1, -1), 2*mm),
    ]))
    story.append(amount_words_table)
    story.append(Spacer(1, 2*mm))
    
    # _S39R5FIX6_ — render tax block for ALL customers, not just GST-invoice ones.
    if True:
        cgst_tax = invoice_data.get('cgst_tax', 0)
        sgst_tax = invoice_data.get('sgst_tax', 0)
        igst_tax = invoice_data.get('igst_tax', 0)
        base_amount = invoice_data.get('base_amount', grand_total)
        
        cgst_rate = (cgst_tax / base_amount * 100) if base_amount > 0 else 0
        sgst_rate = (sgst_tax / base_amount * 100) if base_amount > 0 else 0
        igst_rate = (igst_tax / base_amount * 100) if base_amount > 0 else 0
        total_tax = cgst_tax + sgst_tax + igst_tax
        
        tax_data = [[
            Paragraph("<b>HSN/SAC</b>", bold_style),
            Paragraph("<b>Taxable Value</b>", bold_style),
            Paragraph("<b>SGST Tax</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph("<b>CGST Tax</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph("<b>IGST Tax</b>", bold_style),
            Paragraph("", normal_style),
            Paragraph("<b>Total Tax</b>", bold_style)
        ]]
        
        tax_data.append([
            Paragraph("", normal_style),
            Paragraph("", normal_style),
            Paragraph("<b>Rate</b>", bold_style),
            Paragraph("<b>Amount</b>", bold_style),
            Paragraph("<b>Rate</b>", bold_style),
            Paragraph("<b>Amount</b>", bold_style),
            Paragraph("<b>Rate</b>", bold_style),
            Paragraph("<b>Amount</b>", bold_style),
            Paragraph("", normal_style)
        ])
        
        tax_data.append([
            Paragraph(hsn_sac, normal_style),
            Paragraph(f"{base_amount:.0f}", normal_style),
            Paragraph(f"{sgst_rate:.1f}%", normal_style),
            Paragraph(f"{sgst_tax:.0f}", normal_style),
            Paragraph(f"{cgst_rate:.1f}%", normal_style),
            Paragraph(f"{cgst_tax:.0f}", normal_style),
            Paragraph(f"{igst_rate:.1f}%", normal_style),
            Paragraph(f"{igst_tax:.0f}", normal_style),
            Paragraph(f"{total_tax:.0f}", normal_style)
        ])
        
        tax_table = Table(tax_data, colWidths=[20*mm, 25*mm, 15*mm, 20*mm, 15*mm, 20*mm, 15*mm, 20*mm, 30*mm])
        tax_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E0E0E0')),
            ('SPAN', (0, 0), (0, 1)),
            ('SPAN', (1, 0), (1, 1)),
            ('SPAN', (2, 0), (3, 0)),
            ('SPAN', (4, 0), (5, 0)),
            ('SPAN', (6, 0), (7, 0)),
            ('SPAN', (8, 0), (8, 1)),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('PADDING', (0, 0), (-1, -1), 1.5*mm),
        ]))
        story.append(tax_table)
        story.append(Spacer(1, 2*mm))
        
        tax_words = number_to_words_indian(total_tax)
        tax_words_data = [[Paragraph(f"<b>Tax Amount (in words):</b> {tax_words}", small_style)]]
        tax_words_table = Table(tax_words_data, colWidths=[180*mm])
        tax_words_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('PADDING', (0, 0), (-1, -1), 2*mm),
        ]))
        story.append(tax_words_table)
        story.append(Spacer(1, 2*mm))
    
    declaration_text = company_data.get('declaration', 'Thanks for your business. Hope you are enjoying our services.')
    
    default_terms = "<b>Terms & Conditions</b><br/>" \
                   "1. Kindly Pay Your Dues Amount Before/Till Due Date to Avoid Late Payment<br/>" \
                   "2. Broadband Charges/Monthly will be 500 Rupees Per Charge. It Will<br/>" \
                   "Be Included on the Same if User Paid Annual subscription<br/>" \
                   "3. Renewal Charges will be Calculated as Per Actual Days<br/>" \
                   "<b>Paid Annual subscription</b>"
    terms_text = company_data.get('terms_conditions', default_terms)
    
    company_name_for_signature = company_data.get('company_name', 'AUTO ISP BILLING')
    bank_details_text = "<b>Company Bank Details</b><br/>" \
                       f"Bank Name: {company_data.get('bank_name', 'HDFC BANK')}<br/>" \
                       f"Branch & IFS Code: {company_data.get('branch_ifsc', 'HDFC0001781')}<br/>" \
                       f"Bank A/C No: {company_data.get('account_number', 'N/A')}<br/>" \
                       f"UPI ID: {company_data.get('upi_id', 'N/A')}<br/><br/>" \
                       f"<b>for {company_name_for_signature}</b><br/><br/><br/>" \
                       "<b>Authorised Signatory</b>"
    
    footer_data = [[
        Paragraph(f"{declaration_text}<br/><br/>{terms_text}", small_style),
        Paragraph(bank_details_text, small_style)
    ]]
    
    footer_table = Table(footer_data, colWidths=[90*mm, 90*mm])
    footer_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('PADDING', (0, 0), (-1, -1), 3*mm),
    ]))
    story.append(footer_table)
    
    doc.build(story)
    buffer.seek(0)
    return buffer.read()
