"""
Modern manual-invoice PDF generator.

Generates a clean, stylish invoice using ReportLab for "send manual invoice"
scenarios:
  • To existing customers
  • To one-off recipients (no customer record)
  • With or without GST

Header / footer / T&C / bank / declaration are all sourced from the
companies row that owns the session, with optional sender_label override
so a sub-LCO or employee shows their own name as the sender.
"""
from io import BytesIO
from datetime import datetime


def generate_manual_invoice_pdf(invoice: dict, recipient: dict, sender: dict,
                                 company: dict, line_items: list) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle, Paragraph,
                                     Spacer, Image)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os

    try:
        pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        F, FB = 'DejaVu', 'DejaVu-Bold'
    except Exception:
        F, FB = 'Helvetica', 'Helvetica-Bold'

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14*mm, rightMargin=14*mm,
        topMargin=14*mm, bottomMargin=14*mm,
        title=f"Invoice {invoice.get('invoice_no','')}",
        author=company.get('company_name', 'Auto ISP Billing'),
    )

    NAVY      = colors.HexColor('#1e3a8a')
    INDIGO    = colors.HexColor('#312e81')
    SKY       = colors.HexColor('#0ea5e9')
    SLATE_50  = colors.HexColor('#f8fafc')
    SLATE_200 = colors.HexColor('#e2e8f0')
    SLATE_500 = colors.HexColor('#64748b')
    SLATE_900 = colors.HexColor('#0f172a')
    GREEN     = colors.HexColor('#10b981')

    styles = getSampleStyleSheet()
    # __S38U9__ — slimmer header; invoice-no matches date size; due-amount line.
    p_brand   = ParagraphStyle('brand',   parent=styles['Normal'], fontName=FB,
                                fontSize=14, textColor=colors.white, leading=16, spaceAfter=1)
    p_brand_s = ParagraphStyle('brand_s', parent=styles['Normal'], fontName=F,
                                fontSize=7.8, textColor=colors.HexColor('#cbd5e1'),
                                leading=10)
    p_inv_lbl = ParagraphStyle('inv_lbl', parent=styles['Normal'], fontName=F,
                                fontSize=8, textColor=colors.HexColor('#cbd5e1'),
                                alignment=TA_RIGHT, leading=10)
    p_inv_no  = ParagraphStyle('inv_no',  parent=styles['Normal'], fontName=FB,
                                fontSize=8.5, textColor=colors.white,
                                alignment=TA_RIGHT, leading=11, spaceAfter=1)
    p_inv_sub = ParagraphStyle('inv_sub', parent=styles['Normal'], fontName=F,
                                fontSize=8, textColor=colors.HexColor('#e2e8f0'),
                                alignment=TA_RIGHT, leading=11)
    p_inv_due = ParagraphStyle('inv_due', parent=styles['Normal'], fontName=FB,
                                fontSize=9.5, textColor=colors.HexColor('#fde68a'),
                                alignment=TA_RIGHT, leading=12, spaceBefore=1)

    p_h     = ParagraphStyle('h', parent=styles['Normal'], fontName=FB, fontSize=10,
                              textColor=NAVY, spaceAfter=4)
    p_label = ParagraphStyle('label', parent=styles['Normal'], fontName=F,
                              fontSize=8, textColor=SLATE_500, leading=10)
    p_val   = ParagraphStyle('val', parent=styles['Normal'], fontName=F,
                              fontSize=10, textColor=SLATE_900, leading=14)
    p_val_b = ParagraphStyle('val_b', parent=styles['Normal'], fontName=FB,
                              fontSize=10.5, textColor=SLATE_900, leading=13)
    p_cell  = ParagraphStyle('cell', parent=styles['Normal'], fontName=F,
                              fontSize=9, textColor=SLATE_900, leading=11)
    p_cell_r= ParagraphStyle('cell_r', parent=styles['Normal'], fontName=F,
                              fontSize=9, textColor=SLATE_900, leading=11,
                              alignment=TA_RIGHT)
    p_th    = ParagraphStyle('th', parent=styles['Normal'], fontName=FB,
                              fontSize=9, textColor=colors.white, leading=11)
    p_th_r  = ParagraphStyle('th_r', parent=styles['Normal'], fontName=FB,
                              fontSize=9, textColor=colors.white, leading=11,
                              alignment=TA_RIGHT)
    p_total = ParagraphStyle('total', parent=styles['Normal'], fontName=FB,
                              fontSize=12, textColor=colors.white,
                              alignment=TA_RIGHT, leading=14)
    p_foot  = ParagraphStyle('foot', parent=styles['Normal'], fontName=F,
                              fontSize=8, textColor=SLATE_500, leading=11)
    p_foot_b= ParagraphStyle('foot_b', parent=styles['Normal'], fontName=FB,
                              fontSize=9, textColor=NAVY, leading=12, spaceAfter=2)

    story = []

    co_name = company.get('company_name') or 'Auto ISP Billing'
    co_addr = company.get('company_address') or ''
    co_phone = company.get('company_phone') or ''
    co_email = company.get('company_email') or ''
    co_gst = company.get('gst_number') or ''
    sender_label = sender.get('label') or co_name
    sender_role = sender.get('role') or ''

    # -- Header band --
    brand_block = [Paragraph(co_name, p_brand)]
    addr = co_addr.replace('\n', '<br/>')
    if addr:
        brand_block.append(Paragraph(addr, p_brand_s))
    if co_phone or co_email:
        brand_block.append(Paragraph(f"{co_phone}  &nbsp;&nbsp;{co_email}", p_brand_s))
    # __S38U10__ — only show sender GSTIN when this is a GST invoice.
    if invoice.get('is_gst') and co_gst:
        brand_block.append(Paragraph(f"GSTIN: <b>{co_gst}</b>", p_brand_s))
    inv_block = [
        Paragraph("TAX INVOICE" if invoice.get('is_gst') else "INVOICE", p_inv_lbl),
        Paragraph(f"#{invoice.get('invoice_no','')}", p_inv_no),
        Paragraph(f"Issued: {invoice.get('issue_date','')}", p_inv_sub),
        Paragraph(f"Due: {invoice.get('due_date','')}", p_inv_sub),
    ]
    if invoice.get('total_due') is not None:
        inv_block.append(Paragraph(
            f"Total Due: ₹ {float(invoice.get('total_due') or 0):,.2f}", p_inv_due))
    head = Table([[brand_block, inv_block]],
                 colWidths=[110*mm, 65*mm])
    head.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), NAVY),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING',(0,0), (-1,-1), 10),
        ('TOPPADDING',  (0,0), (-1,-1), 8),
        ('BOTTOMPADDING',(0,0),(-1,-1), 8),
        ('VALIGN',      (0,0), (-1,-1), 'TOP'),
        ('LINEBELOW',   (0,0), (-1,-1), 3, SKY),
    ]))
    story.append(head)
    story.append(Spacer(1, 4*mm))

    # -- Sender / Bill-To panels --
    sender_lines = [Paragraph("SENT BY", p_label),
                    Paragraph(sender_label, p_val_b)]
    if sender_role:
        sender_lines.append(Paragraph(sender_role, p_label))
    snd_addr = (sender.get('address') or '').replace('\n', '<br/>')
    if snd_addr:
        sender_lines.append(Paragraph(snd_addr, p_val))
    contact_bits = []
    if sender.get('phone'): contact_bits.append(sender['phone'])
    if sender.get('email'): contact_bits.append(sender['email'])
    if contact_bits:
        sender_lines.append(Paragraph(" · ".join(contact_bits), p_val))
    if invoice.get('is_gst') and sender.get('gst_no'):
        sender_lines.append(Paragraph(f"GSTIN: <b>{sender['gst_no']}</b>", p_val))

    bill_lines = [Paragraph("BILL TO", p_label),
                  Paragraph(recipient.get('name') or '—', p_val_b)]
    rcp_addr = (recipient.get('address') or '').replace('\n', '<br/>')
    if rcp_addr:
        bill_lines.append(Paragraph(rcp_addr, p_val))
    rcp_loc = ', '.join([x for x in [recipient.get('city'), recipient.get('state'),
                                       recipient.get('pincode')] if x])
    if rcp_loc:
        bill_lines.append(Paragraph(rcp_loc, p_val))
    if recipient.get('email'):
        bill_lines.append(Paragraph(recipient['email'], p_val))
    if recipient.get('phone'):
        bill_lines.append(Paragraph(recipient['phone'], p_val))
    if invoice.get('is_gst') and recipient.get('gst_no'):
        bill_lines.append(Paragraph(f"GSTIN: <b>{recipient['gst_no']}</b>", p_val))

    party = Table([[sender_lines, bill_lines]], colWidths=[85*mm, 90*mm])
    party.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BACKGROUND', (0,0), (0,0), SLATE_50),
        ('BACKGROUND', (1,0), (1,0), SLATE_50),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('RIGHTPADDING',(0,0), (-1,-1), 12),
        ('TOPPADDING',  (0,0), (-1,-1), 10),
        ('BOTTOMPADDING',(0,0),(-1,-1), 10),
        ('BOX', (0,0), (0,0), 0.4, SLATE_200),
        ('BOX', (1,0), (1,0), 0.4, SLATE_200),
        ('LINEABOVE', (0,0), (0,0), 2, NAVY),
        ('LINEABOVE', (1,0), (1,0), 2, SKY),
    ]))
    story.append(party)
    story.append(Spacer(1, 5*mm))

    # -- Line items --
    is_gst = bool(invoice.get('is_gst'))
    if is_gst:
        head_row = [
            Paragraph("#", p_th),
            Paragraph("Description", p_th),
            Paragraph("HSN/SAC", p_th),
            Paragraph("Qty", p_th_r),
            Paragraph("Rate", p_th_r),
            Paragraph("CGST", p_th_r),
            Paragraph("SGST", p_th_r),
            Paragraph("Amount", p_th_r),
        ]
        col_widths = [9*mm, 64*mm, 18*mm, 13*mm, 22*mm, 16*mm, 16*mm, 24*mm]
    else:
        head_row = [
            Paragraph("#", p_th),
            Paragraph("Description", p_th),
            Paragraph("Qty", p_th_r),
            Paragraph("Rate", p_th_r),
            Paragraph("Amount", p_th_r),
        ]
        col_widths = [9*mm, 100*mm, 18*mm, 24*mm, 31*mm]

    rows = [head_row]
    sub_total = 0.0
    cgst_total = 0.0
    sgst_total = 0.0
    for i, li in enumerate(line_items, 1):
        qty  = float(li.get('qty', 1) or 1)
        rate = float(li.get('rate', 0) or 0)
        amt  = qty * rate
        sub_total += amt
        if is_gst:
            cgst_pct = float(li.get('cgst_pct', 0) or 0)
            sgst_pct = float(li.get('sgst_pct', 0) or 0)
            cg = amt * cgst_pct / 100.0
            sg = amt * sgst_pct / 100.0
            cgst_total += cg
            sgst_total += sg
            rows.append([
                Paragraph(str(i), p_cell),
                Paragraph(li.get('description','—'), p_cell),
                Paragraph(li.get('hsn','') or '—', p_cell),
                Paragraph(f"{qty:g}", p_cell_r),
                Paragraph(f"₹ {rate:,.2f}", p_cell_r),
                Paragraph(f"{cgst_pct:g}%<br/><font size=7 color='#64748b'>₹ {cg:,.2f}</font>", p_cell_r),
                Paragraph(f"{sgst_pct:g}%<br/><font size=7 color='#64748b'>₹ {sg:,.2f}</font>", p_cell_r),
                Paragraph(f"₹ {amt:,.2f}", p_cell_r),
            ])
        else:
            rows.append([
                Paragraph(str(i), p_cell),
                Paragraph(li.get('description','—'), p_cell),
                Paragraph(f"{qty:g}", p_cell_r),
                Paragraph(f"₹ {rate:,.2f}", p_cell_r),
                Paragraph(f"₹ {amt:,.2f}", p_cell_r),
            ])

    items = Table(rows, colWidths=col_widths, repeatRows=1)
    items.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), NAVY),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING',(0,0), (-1,-1), 6),
        ('TOPPADDING',  (0,0), (-1,-1), 7),
        ('BOTTOMPADDING',(0,0),(-1,-1), 7),
        ('VALIGN',      (0,0), (-1,-1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, SLATE_50]),
        ('LINEBELOW',   (0,0), (-1,-1), 0.3, SLATE_200),
    ]))
    story.append(items)
    story.append(Spacer(1, 4*mm))

    grand_total = sub_total + cgst_total + sgst_total

    # -- Totals panel --
    tot_rows = [
        [Paragraph("Sub Total", p_val), Paragraph(f"₹ {sub_total:,.2f}", p_cell_r)],
    ]
    if is_gst:
        tot_rows += [
            [Paragraph("CGST", p_val), Paragraph(f"₹ {cgst_total:,.2f}", p_cell_r)],
            [Paragraph("SGST", p_val), Paragraph(f"₹ {sgst_total:,.2f}", p_cell_r)],
        ]
    tot_rows.append([
        Paragraph("Grand Total", p_total),
        Paragraph(f"₹ {grand_total:,.2f}", p_total),
    ])
    totals = Table(tot_rows, colWidths=[40*mm, 40*mm])
    totals.setStyle(TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING',(0,0), (-1,-1), 8),
        ('TOPPADDING',  (0,0), (-1,-1), 5),
        ('BOTTOMPADDING',(0,0),(-1,-1), 5),
        ('BACKGROUND',  (0,-1), (-1,-1), NAVY),
        ('LINEABOVE',   (0,0), (-1,0),  0.3, SLATE_200),
        ('LINEABOVE',   (0,-1),(-1,-1), 1, INDIGO),
    ]))
    pad_total = Table([['', totals]], colWidths=[95*mm, 80*mm])
    pad_total.setStyle(TableStyle([
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING',(0,0), (-1,-1), 0),
        ('TOPPADDING',  (0,0), (-1,-1), 0),
        ('BOTTOMPADDING',(0,0),(-1,-1), 0),
    ]))
    story.append(pad_total)
    story.append(Spacer(1, 6*mm))

    # -- Notes / T&C / declaration / bank --
    notes = (invoice.get('notes') or '').strip()
    if notes:
        story.append(Paragraph("Notes", p_foot_b))
        story.append(Paragraph(notes.replace('\n', '<br/>'), p_foot))
        story.append(Spacer(1, 3*mm))

    bottom_left = []
    if company.get('terms_conditions'):
        bottom_left.append(Paragraph("Terms &amp; Conditions", p_foot_b))
        bottom_left.append(Paragraph(
            company['terms_conditions'].replace('\n', '<br/>'), p_foot))
        bottom_left.append(Spacer(1, 2*mm))
    if company.get('declaration'):
        bottom_left.append(Paragraph("Declaration", p_foot_b))
        bottom_left.append(Paragraph(
            company['declaration'].replace('\n', '<br/>'), p_foot))

    bank_block = []
    if any(company.get(k) for k in ('bank_name','account_number','branch_ifsc','upi_id')):
        bank_block.append(Paragraph("Bank Details", p_foot_b))
        bank_lines = []
        for label, key in [('Bank', 'bank_name'),
                           ('A/C No.', 'account_number'),
                           ('IFSC', 'branch_ifsc'),
                           ('Branch', 'branch_location'),
                           ('UPI', 'upi_id')]:
            v = company.get(key)
            if v:
                bank_lines.append(f"<b>{label}:</b> {v}")
        bank_block.append(Paragraph("<br/>".join(bank_lines), p_foot))

    # __S38U9__ — T&C + Declaration first (full width), bank details below.
    if bottom_left:
        tc_block = Table([[bottom_left]], colWidths=[180*mm])
        tc_block.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING',(0,0), (-1,-1), 10),
            ('TOPPADDING',  (0,0), (-1,-1), 6),
            ('BOTTOMPADDING',(0,0),(-1,-1), 6),
            ('BACKGROUND', (0,0), (-1,-1), SLATE_50),
            ('LINEABOVE',  (0,0), (-1,-1), 2, INDIGO),
            ('BOX', (0,0), (-1,-1), 0.4, SLATE_200),
        ]))
        story.append(tc_block)
        story.append(Spacer(1, 4*mm))
    if bank_block:
        bk_block = Table([[bank_block]], colWidths=[180*mm])
        bk_block.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING',(0,0), (-1,-1), 10),
            ('TOPPADDING',  (0,0), (-1,-1), 6),
            ('BOTTOMPADDING',(0,0),(-1,-1), 6),
            ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#ecfdf5')),
            ('LINEABOVE',  (0,0), (-1,-1), 2, GREEN),
            ('BOX', (0,0), (-1,-1), 0.4, SLATE_200),
        ]))
        story.append(bk_block)
        story.append(Spacer(1, 6*mm))

    # -- Signature line --
    sig_line = Table([[Paragraph(f"Authorised signatory<br/>For {co_name}", p_foot)]],
                     colWidths=[60*mm])
    sig_line.setStyle(TableStyle([
        ('LINEABOVE', (0,0), (-1,0), 0.5, SLATE_500),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
        ('RIGHTPADDING',(0,0), (-1,-1), 0),
    ]))
    story.append(Table([['', sig_line]], colWidths=[115*mm, 60*mm]))

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(SLATE_500)
        canvas.setFont(F, 7.5)
        canvas.drawString(14*mm, 8*mm,
            f"Generated on {datetime.now().strftime('%d-%m-%Y %I:%M %p IST')} • Auto ISP Billing")
        canvas.drawRightString(196*mm, 8*mm,
            f"Page {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
