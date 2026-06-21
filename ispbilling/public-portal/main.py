# __SQLITE_GUARD_BOOT__
import sqlite3 as __sq3_g; __sq3_g._orig_connect = __sq3_g.connect
def __sq3_guard(*a, **kw):
    p = a[0] if a else kw.get("database","")
    if isinstance(p, str) and ("/var/lib/autoispbilling/autoispbilling.db" in p or "/var/lib/freeradius/radacct.db" in p):
        import sys as _sys_sg
        if "/opt/ispbilling" not in _sys_sg.path: _sys_sg.path.insert(0, "/opt/ispbilling")
        import db_compat
        return db_compat.get_raw_conn(timeout=kw.get("timeout",10))
    return __sq3_g._orig_connect(*a, **kw)
__sq3_g.connect = __sq3_guard
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
import os
from pathlib import Path

# Create FastAPI app
app = FastAPI(title="Auto ISP Billing - Public Portal")

# Add session middleware
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "your-secret-key-here-change-in-production"))
# _S40zμ_  Profanity middleware (server-side defense)
# Profanity middleware removed (s56x)


# Setup templates
templates = Jinja2Templates(directory="templates")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Database setup (import from parent directory)
import sys
sys.path.append(str(Path(__file__).parent.parent))

try:
    from database import SessionLocal, SuperAdmin
except ImportError:
    # Fallback if database module not found
    SessionLocal = None
    SuperAdmin = None

def get_db():
    """Database dependency"""
    if SessionLocal is None:
        yield None
        return
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_superadmin_contact(db):
    """Get superadmin contact info from database"""
    if db is None or SuperAdmin is None:
        return {}
    try:
        superadmin = db.query(SuperAdmin).first()
        if superadmin:
            return {
                'phone': superadmin.mobile,
                'email': superadmin.email
            }
    except Exception as e:
        print(f"Error getting superadmin contact: {str(e)}")
    return {}

def get_public_context(db):
    """
    Get context for public pages (homepage, contact, faq, etc.)
    Returns dict with support_phone and support_email from superadmin settings.
    """
    contact = get_superadmin_contact(db)
    return {
        'support_phone': contact.get('phone') or '+91-8085868114',
        'support_email': contact.get('email') or 'support@autoispbilling.com'
    }

# Public routes
@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request, db: Session = Depends(get_db)):
    """Homepage"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("index.html", context)

@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request, db: Session = Depends(get_db)):
    """Contact page"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("contact.html", context)

@app.get("/faq", response_class=HTMLResponse)
async def faq_page(request: Request, db: Session = Depends(get_db)):
    """FAQ page"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("faq.html", context)

@app.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy_page(request: Request, db: Session = Depends(get_db)):
    """Privacy policy page"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("privacy_policy.html", context)

@app.get("/terms-of-service", response_class=HTMLResponse)
async def terms_of_service_page(request: Request, db: Session = Depends(get_db)):
    """Terms of service page"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("terms_of_service.html", context)

@app.get("/refund-policy", response_class=HTMLResponse)
async def refund_policy_page(request: Request, db: Session = Depends(get_db)):
    """Refund policy page"""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("refund_policy.html", context)

# __GSC_VERIFICATION__  Google Search Console domain-ownership verification.
# Serves /googlec48eef13f18ea2bc.html with the exact body Google expects.
@app.get("/googlec48eef13f18ea2bc.html", response_class=PlainTextResponse)
async def gsc_verification():
    return "google-site-verification: googlec48eef13f18ea2bc.html"


# __ACCOUNT_DELETION__  Public account-deletion page (required by Google Play
# data deletion policy + Indian DPDPA 2023).
@app.get("/account-deletion", response_class=HTMLResponse)
async def account_deletion_page(request: Request, db: Session = Depends(get_db)):
    """Public form for users to request account + data deletion."""
    context = get_public_context(db)
    context["request"] = request
    return templates.TemplateResponse("account_deletion.html", context)


@app.get("/delete-account", response_class=HTMLResponse)
async def account_deletion_alias(request: Request):
    return RedirectResponse(url="/account-deletion", status_code=301)


@app.post("/api/account-deletion-request")
async def api_account_deletion_request(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    customer_id: str = Form(""),
    isp: str = Form(""),
    scope: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    name, email, phone = name.strip(), email.strip(), phone.strip()
    customer_id, isp, scope = customer_id.strip(), isp.strip(), scope.strip()
    notes = (notes or "").strip()[:2000]
    if not (name and email and phone and isp and scope):
        return JSONResponse({"success": False, "message": "Please fill all required fields."}, status_code=400)
    when = _dt.datetime.now().strftime("%d %b %Y, %H:%M IST")
    # Log request to DB (best-effort)
    try:
        import sys as _sys_rd; _sys_rd.path.insert(0, "/opt/ispbilling")
        from db_compat import get_raw_conn as _compat_conn
        c = _compat_conn(timeout=20)
        c.execute(
            "INSERT INTO account_deletion_requests "
            "(name,email,phone,customer_id,isp,scope,notes,ip,user_agent) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (name, email, phone, customer_id, isp, scope, notes,
             request.client.host if request.client else "",
             request.headers.get("user-agent", "")[:255])
        )
        c.commit(); c.close()
    except Exception as _e:
        print(f"[account_deletion] DB log failed: {_e}")
    SCOPE_LABEL = {
        "account_full": "Delete the entire account + ALL data",
        "app_only":     "Delete app data only (keep broadband subscription)",
        "specific":     "Delete specific data (see notes)",
    }
    scope_lbl = SCOPE_LABEL.get(scope, scope)
    subject = f"[Account Deletion] {name} - {isp}"
    plain = (
        f"New account deletion request via autoispbilling.com/account-deletion\n\n"
        f"Name        : {name}\nEmail       : {email}\nMobile      : {phone}\n"
        f"Customer ID : {customer_id or '-'}\nISP         : {isp}\n"
        f"Scope       : {scope_lbl}\nNotes       : {notes or '-'}\n"
        f"Received    : {when}\n"
    )
    html_body = f"""
<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <h2 style="color:#dc2626;">Account Deletion Request</h2>
  <p style="color:#dc2626;font-weight:600;">Action required: verify the user's identity and process within 7 working days.</p>
  <table cellpadding="8" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;">
    <tr><td style="background:#f1f5f9;font-weight:600;">Name</td><td>{_escape(name)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Email</td><td>{_escape(email)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Mobile</td><td>{_escape(phone)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Customer ID</td><td>{_escape(customer_id) or '-'}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">ISP</td><td>{_escape(isp)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Scope</td><td>{_escape(scope_lbl)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Received</td><td>{when}</td></tr>
  </table>
  <h3 style="margin-top:18px;color:#0f172a;">Notes</h3>
  <div style="white-space:pre-wrap;background:#f8fafc;border-left:4px solid #dc2626;padding:12px 16px;border-radius:6px;">{_escape(notes) or '-'}</div>
  <p style="color:#64748b;font-size:12px;margin-top:18px;">Sent from /account-deletion.</p>
</body></html>
"""
    ok, err = _send_lead_email(db, subject, plain, html_body, reply_to=email)
    if not ok:
        # Even if email fails, the DB row is saved — tell the user we got it.
        return {"success": True,
                "message": "Request saved. If you don't receive a verification email within 24 hours, please email support@autoispbilling.com from the same address."}
    return {"success": True,
            "message": "We've received your request. Check your email within 24 hours for a verification link, after which deletion will be completed in 7 working days."}


# Redirect /login to admin portal login
@app.get("/login")
async def login_redirect():
    """Redirect to admin portal login"""
    return RedirectResponse(url="/admin/login", status_code=302)


# __S38N_PUBLIC_FORMS__
# -- Lead-capture forms (Book-a-demo on /, Send-us-a-message on /contact) --
# Uses SMTP creds stored in the superadmins row (Hostinger 465 SSL).
import smtplib, ssl, html as _html_esc, datetime as _dt
from email.message import EmailMessage

LEAD_RECIPIENT = os.getenv("LEAD_NOTIFICATION_EMAIL", "fibernetserviceindia@gmail.com")


def _get_smtp_creds(db):
    """Pull SMTP host/port/user/pass from the first superadmins row."""
    if db is None or SuperAdmin is None:
        return None
    try:
        sa = db.query(SuperAdmin).first()
        if not sa:
            return None
        if not (sa.smtp_server and sa.smtp_username and sa.smtp_password):
            return None
        return {
            "host": sa.smtp_server,
            "port": int(sa.smtp_port or 465),
            "user": sa.smtp_username,
            "password": sa.smtp_password,
        }
    except Exception as e:
        print(f"[lead-form] SMTP config lookup failed: {e}")
        return None


def _send_lead_email(db, subject: str, plain_body: str, html_body: str, reply_to: str = ""):
    """Send a lead e-mail synchronously. Returns (ok: bool, error: str|None)."""
    creds = _get_smtp_creds(db)
    if not creds:
        return False, "SMTP not configured"
    msg = EmailMessage()
    msg["From"] = creds["user"]
    msg["To"] = LEAD_RECIPIENT
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(plain_body)
    msg.add_alternative(html_body, subtype="html")
    try:
        ctx = ssl.create_default_context()
        port = creds["port"]
        if port == 465:
            with smtplib.SMTP_SSL(creds["host"], port, context=ctx, timeout=20) as srv:
                srv.login(creds["user"], creds["password"])
                srv.send_message(msg)
        else:
            with smtplib.SMTP(creds["host"], port, timeout=20) as srv:
                srv.ehlo()
                srv.starttls(context=ctx)
                srv.login(creds["user"], creds["password"])
                srv.send_message(msg)
        return True, None
    except Exception as e:
        print(f"[lead-form] send failed: {e}")
        return False, str(e)


def _escape(v):
    return _html_esc.escape((v or "").strip())


@app.post("/api/demo-request")
async def api_demo_request(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    company: str = Form(""),
    customers: str = Form(""),
    db: Session = Depends(get_db),
):
    name, email, phone, company = name.strip(), email.strip(), phone.strip(), company.strip()
    if not (name and email and phone and company):
        return JSONResponse({"success": False, "message": "Please fill all required fields."}, status_code=400)
    when = _dt.datetime.now().strftime("%d %b %Y, %H:%M IST")
    subject = f"[Demo Request] {name} - {company}"
    plain = (
        f"New demo request via autoispbilling.com\n\n"
        f"Name      : {name}\nEmail     : {email}\nPhone     : {phone}\n"
        f"Company   : {company}\nCustomers : {customers or 'n/a'}\nReceived  : {when}\n"
    )
    html_body = f"""
<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <h2 style="color:#3d5afe;">New Demo Request</h2>
  <table cellpadding="8" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;">
    <tr><td style="background:#f1f5f9;font-weight:600;">Name</td><td>{_escape(name)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Email</td><td>{_escape(email)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Phone</td><td>{_escape(phone)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Company</td><td>{_escape(company)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Customers</td><td>{_escape(customers) or '-'}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Received</td><td>{when}</td></tr>
  </table>
  <p style="color:#64748b;font-size:12px;margin-top:18px;">Sent from the autoispbilling.com landing page.</p>
</body></html>
"""
    ok, err = _send_lead_email(db, subject, plain, html_body, reply_to=email)
    if not ok:
        return JSONResponse({"success": False, "message": f"Could not send request right now. Please try again or email us directly. ({err})"}, status_code=500)
    return {"success": True, "message": "Thanks! Our team will reach out within 1 business day."}


@app.post("/api/contact-form")
async def api_contact_form(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    company: str = Form(""),
    subject: str = Form(""),
    message: str = Form(""),
    db: Session = Depends(get_db),
):
    name, email, phone, message = name.strip(), email.strip(), phone.strip(), message.strip()
    if not (name and email and phone and subject and message):
        return JSONResponse({"success": False, "message": "Please fill all required fields."}, status_code=400)
    when = _dt.datetime.now().strftime("%d %b %Y, %H:%M IST")
    subj_line = f"[Contact] {subject} - {name}"
    plain = (
        f"New contact form submission\n\n"
        f"Name      : {name}\nEmail     : {email}\nPhone     : {phone}\n"
        f"Company   : {company or 'n/a'}\nSubject   : {subject}\nReceived  : {when}\n\n"
        f"Message:\n{message}\n"
    )
    html_body = f"""
<html><body style="font-family:Arial,sans-serif;color:#0f172a;">
  <h2 style="color:#00d4b4;">New Contact Message</h2>
  <table cellpadding="8" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e7eb;">
    <tr><td style="background:#f1f5f9;font-weight:600;">Name</td><td>{_escape(name)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Email</td><td>{_escape(email)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Phone</td><td>{_escape(phone)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Company</td><td>{_escape(company) or '-'}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Subject</td><td>{_escape(subject)}</td></tr>
    <tr><td style="background:#f1f5f9;font-weight:600;">Received</td><td>{when}</td></tr>
  </table>
  <h3 style="margin-top:18px;color:#0f172a;">Message</h3>
  <div style="white-space:pre-wrap;background:#f8fafc;border-left:4px solid #3d5afe;padding:12px 16px;border-radius:6px;">{_escape(message)}</div>
  <p style="color:#64748b;font-size:12px;margin-top:18px;">Sent from the autoispbilling.com contact page.</p>
</body></html>
"""
    ok, err = _send_lead_email(db, subj_line, plain, html_body, reply_to=email)
    if not ok:
        return JSONResponse({"success": False, "message": f"Could not send your message right now. Please try again later. ({err})"}, status_code=500)
    return {"success": True, "message": "Thanks for reaching out — we'll reply shortly."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8004)


# _S39R5E_PRICING — Pricing page that fetches active packages from superadmin
@app.get("/pricing", response_class=HTMLResponse)
async def pricing_page(request: Request):
    import sys as _sys_rd; _sys_rd.path.insert(0, "/opt/ispbilling")
    from db_compat import get_raw_conn as _compat_conn
    db_path = None
    pkgs = []
    try:
        c = _compat_conn(timeout=10)
        rows = c.execute(
            "SELECT id, package_name, user_count, package_type, package_price, "
            "       description, cgst_rate, sgst_rate, igst_rate "
            "FROM superadmin_packages "
            "WHERE is_active=1 ORDER BY package_price ASC"
        ).fetchall()
        c.close()
        for r in rows:
            d = dict(r)
            base = float(d.get("package_price") or 0)
            igst = float(d.get("igst_rate") or 18)
            d["price_with_gst"] = round(base * (1 + igst/100.0), 2)
            d["icon"] = ("rocket-takeoff" if base == 0
                         else "stars" if base < 1500
                         else "trophy" if base < 3000
                         else "gem")
            d["features"] = []
            if d.get("user_count"):
                d["features"].append(f"Up to {d['user_count']} customers")
            # _S39R5G_PRICING_FEATURES
            d["features"].append("WhatsApp notifications & reminders")
            d["features"] += [
                "Mikrotik auto-provisioning",
                "OLT live monitoring (Optilink / VSOL / ZTE / Nokia / Fiberhome / Syrotech / Netlink)",
                "Connected-ONU mapping, reboot & re-provision",
                "Pinpoint fiber-cut detection (LOS / LOF telemetry)",
                "Fiber / Network Planning (GIS map, splice trays, power budget)",
                "Online payment & invoicing",
                "Sub-LCO + Employee + Users Portals",
                "Family Safe Features",
                "Mobile app (Android)",
                "24/7 support",
            ]
            pkgs.append(d)
    except Exception as e:
        print(f"[pricing] failed: {e}")
    return templates.TemplateResponse(
        "pricing.html",
        {"request": request, "packages": pkgs}
    )


# _S39R5I_SECURITY — security headers
@app.middleware("http")
async def _r5i_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
    return response


# __PUBLIC_SITEMAP_WIRED__
import datetime as _sm_dt
from fastapi.responses import Response as _SM_Resp

_SITEMAP_PAGES = [
    # __SEO_SITEMAP_V2__  path, label, group, freq, prio
    ("/",                 "Home",                "Product",  "weekly",  "1.0"),
    ("/pricing",          "Pricing",             "Product",  "monthly", "0.9"),
    ("/faq",              "FAQ / Help",          "Support",  "weekly",  "0.8"),
    ("/contact",          "Contact Us",          "Support",  "monthly", "0.7"),
    ("/sitemap",          "Site Map",            "Support",  "monthly", "0.5"),
    ("/login",            "Customer Sign In",    "Account",  "monthly", "0.9"),
    ("/admin/login",      "Admin / Sub-LCO / Employee Sign In",
                                                 "Account",  "monthly", "0.7"),
    ("/pay",              "Pay My Bill",         "Account",  "weekly",  "1.0"),
    ("/account-deletion", "Account Deletion",    "Legal",    "yearly",  "0.4"),
    ("/delete-account",   "Delete My Account",   "Legal",    "yearly",  "0.4"),
    ("/privacy-policy",   "Privacy Policy",      "Legal",    "yearly",  "0.4"),
    ("/terms-of-service", "Terms of Service",    "Legal",    "yearly",  "0.4"),
    ("/refund-policy",    "Refund Policy",       "Legal",    "yearly",  "0.4"),
]

# Images known to be on the public site (used by /sitemap-images.xml).
# (image_url, caption, page_path_where_it_appears)
_SITEMAP_IMAGES = [
    ("/static/favicon.png",
     "Auto ISP Billing — Logo",  "/"),
    ("/static/img/hero.png",
     "Auto ISP Billing Dashboard Preview", "/"),
    ("/static/img/og-image.png",
     "Auto ISP Billing — All-in-One ISP Management Platform", "/"),
    ("/static/img/feature-billing.png",
     "Automated Billing & Invoicing for ISPs", "/pricing"),
    ("/static/img/feature-radius.png",
     "FreeRADIUS Integration for ISP Authentication", "/"),
    ("/static/img/feature-whatsapp.png",
     "WhatsApp & SMS Notifications to Customers", "/"),
]

# Languages we declare in hreflang. Single-domain so all map to the same URL.
_SITEMAP_HREFLANGS = [
    ("en",     ""),   # default
    ("en-IN",  ""),
    ("hi-IN",  ""),
    ("x-default", ""),
]


def _sm_base(request):
    host = request.headers.get("host") or "www.autoispbilling.com"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{proto}://{host}"


@app.get("/sitemap.xml")
async def public_sitemap_xml(request: Request):
    # __SEO_SITEMAP_V2__  pages sitemap with hreflang annotations
    base = _sm_base(request)
    today = _sm_dt.date.today().isoformat()
    parts = []
    for p, _lbl, _grp, freq, prio in _SITEMAP_PAGES:
        loc = f"{base}{p}"
        xhtml = ""
        for lang, _ in _SITEMAP_HREFLANGS:
            xhtml += (
                f'    <xhtml:link rel="alternate" hreflang="{lang}" '
                f'href="{loc}"/>\n'
            )
        parts.append(
            f"  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{prio}</priority>\n"
            f"{xhtml}"
            f"  </url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(parts) + "\n</urlset>\n"
    )
    return _SM_Resp(content=body, media_type="application/xml")


@app.get("/sitemap-index.xml")
async def public_sitemap_index(request: Request):
    """Sitemap index pointing to per-section sitemaps (pages + images)."""
    base = _sm_base(request)
    today = _sm_dt.date.today().isoformat()
    children = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap-images.xml",
    ]
    items = "\n".join(
        f"  <sitemap>\n"
        f"    <loc>{c}</loc>\n"
        f"    <lastmod>{today}</lastmod>\n"
        f"  </sitemap>"
        for c in children
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + items + "\n</sitemapindex>\n"
    )
    return _SM_Resp(content=body, media_type="application/xml")


@app.get("/sitemap-images.xml")
async def public_sitemap_images(request: Request):
    """Image sitemap with captions for each public-site image."""
    base = _sm_base(request)
    today = _sm_dt.date.today().isoformat()
    # Group images by page they appear on so we emit one <url> per page.
    by_page = {}
    for img, caption, page in _SITEMAP_IMAGES:
        by_page.setdefault(page, []).append((img, caption))
    items = []
    for page, imgs in by_page.items():
        img_xml = "\n".join(
            f"    <image:image>\n"
            f"      <image:loc>{base}{i}</image:loc>\n"
            f"      <image:caption>{c}</image:caption>\n"
            f"      <image:title>{c}</image:title>\n"
            f"    </image:image>"
            for i, c in imgs
        )
        items.append(
            f"  <url>\n"
            f"    <loc>{base}{page}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"{img_xml}\n"
            f"  </url>"
        )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">\n'
        + "\n".join(items) + "\n</urlset>\n"
    )
    return _SM_Resp(content=body, media_type="application/xml")


@app.get("/robots.txt")
async def public_robots(request: Request):
    # __SEO_SITEMAP_V2__  references sitemap-index for better crawler discovery
    base = _sm_base(request)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /sub-lco/\n"
        "Disallow: /employee/\n"
        "Disallow: /customer/\n"
        "Disallow: /superadmin/\n"
        "Disallow: /api/\n"
        "Disallow: /static/uploads/\n"
        "Disallow: /static/customer-docs/\n"
        "Crawl-delay: 1\n"
        "\n"
        f"Sitemap: {base}/sitemap-index.xml\n"
        f"Sitemap: {base}/sitemap.xml\n"
        f"Sitemap: {base}/sitemap-images.xml\n"
    )
    return _SM_Resp(content=body, media_type="text/plain")


@app.get("/sitemap", response_class=HTMLResponse)
async def public_sitemap_html(request: Request):
    # Group pages for the human view.
    groups = {}
    for path, label, grp, _f, _p in _SITEMAP_PAGES:
        groups.setdefault(grp, []).append((label, path))
    return templates.TemplateResponse("sitemap.html", {
        "request": request, "groups": groups,
        "support_phone": "+918085868114",
    })
