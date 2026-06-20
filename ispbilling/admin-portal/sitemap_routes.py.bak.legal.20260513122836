"""
sitemap_routes.py — Session 2026.02
Public SEO sitemap + role-aware HTML site map.

Endpoints:
  GET /sitemap.xml          (public; XML for search engines)
  GET /robots.txt           (public; points crawlers at the sitemap)
  GET /admin/sitemap        (role-aware HTML; auth required)
  GET /sub-lco/sitemap      (role-aware HTML; auth required)
  GET /employee/sitemap     (role-aware HTML; auth required)
  GET /customer/sitemap     (role-aware HTML; auth required)

The XML lists ONLY public, crawlable URLs (login, pay, password-reset
landing, hotspot template). It deliberately excludes every per-role
page since those are behind authentication.

The HTML site map per role mirrors that role's sidebar so users can
jump to any page in one click — useful for big sidebars with nested
sections.
"""
from __future__ import annotations

import datetime
from typing import List, Dict

from fastapi import FastAPI, Request
from fastapi.responses import Response, RedirectResponse


# ─── 1. Public SEO sitemap.xml ────────────────────────────────────
def _today_iso() -> str:
    return datetime.date.today().isoformat()


PUBLIC_URLS: List[Dict[str, str]] = [
    # path,                  changefreq, priority
    {"path": "/login",                 "freq": "monthly", "prio": "0.9"},
    {"path": "/pay",                   "freq": "weekly",  "prio": "1.0"},
    {"path": "/pay/captive",           "freq": "monthly", "prio": "0.6"},
    {"path": "/admin/login",           "freq": "monthly", "prio": "0.8"},
    {"path": "/sub-lco/login",         "freq": "monthly", "prio": "0.7"},
    {"path": "/employee/login",        "freq": "monthly", "prio": "0.6"},
    {"path": "/customer/login",        "freq": "monthly", "prio": "0.8"},
]


def _base_url(request: Request) -> str:
    host = request.headers.get("host") or "www.autoispbilling.com"
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    return f"{scheme}://{host}"


def register(app: FastAPI) -> None:

    @app.get("/sitemap.xml")
    async def sitemap_xml(request: Request):
        base = _base_url(request)
        today = _today_iso()
        urls_xml = "\n".join(
            f"  <url>\n"
            f"    <loc>{base}{u['path']}</loc>\n"
            f"    <lastmod>{today}</lastmod>\n"
            f"    <changefreq>{u['freq']}</changefreq>\n"
            f"    <priority>{u['prio']}</priority>\n"
            f"  </url>"
            for u in PUBLIC_URLS
        )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls_xml}\n"
            '</urlset>\n'
        )
        return Response(content=body, media_type="application/xml")

    @app.get("/robots.txt")
    async def robots_txt(request: Request):
        base = _base_url(request)
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /admin/\n"
            "Disallow: /sub-lco/\n"
            "Disallow: /employee/\n"
            "Disallow: /customer/\n"
            "Disallow: /superadmin/\n"
            "Disallow: /api/\n"
            f"Sitemap: {base}/sitemap.xml\n"
        )
        return Response(content=body, media_type="text/plain")

    # ─── 2. Role-aware HTML site map ─────────────────────────────
    ADMIN_GROUPS: List[Dict] = [
        {"title": "Dashboard", "icon": "bi-speedometer2", "items": [
            {"label": "Overview", "href": "/admin/dashboard"},
            {"label": "Notifications", "href": "/admin/notifications"},
        ]},
        {"title": "Customers", "icon": "bi-people", "items": [
            {"label": "Subscribers", "href": "/admin/subscribers"},
            {"label": "Add Customer", "href": "/admin/subscribers/new"},
            {"label": "Plans", "href": "/admin/plans"},
            {"label": "Locations", "href": "/admin/locations"},
        ]},
        {"title": "Billing", "icon": "bi-receipt", "items": [
            {"label": "Invoices", "href": "/admin/send-invoices"},
            {"label": "Payments", "href": "/admin/payments"},
            {"label": "Manual Invoice", "href": "/admin/manual-invoice"},
            {"label": "Expenses", "href": "/admin/expenses"},
        ]},
        {"title": "Network", "icon": "bi-broadcast", "items": [
            {"label": "Network Map", "href": "/admin/network-map"},
            {"label": "OLT Management", "href": "/admin/olt"},
            {"label": "Track Employee", "href": "/admin/track-employee"},
        ]},
        {"title": "Communication", "icon": "bi-chat-dots", "items": [
            {"label": "WhatsApp Campaigns", "href": "/admin/whatsapp-campaign"},
            {"label": "Complaints", "href": "/admin/complaints"},
        ]},
        {"title": "Team", "icon": "bi-person-badge", "items": [
            {"label": "Sub-LCOs", "href": "/admin/sub-lcos"},
            {"label": "Employees", "href": "/admin/employees"},
        ]},
        {"title": "Account", "icon": "bi-gear", "items": [
            {"label": "Settings", "href": "/admin/settings"},
            {"label": "FAQ / Help", "href": "/admin/faq"},
            {"label": "Profile", "href": "/admin/profile"},
        ]},
    ]

    SUB_LCO_GROUPS: List[Dict] = [
        {"title": "Dashboard", "icon": "bi-speedometer2", "items": [
            {"label": "Overview", "href": "/sub-lco/dashboard"},
        ]},
        {"title": "Customers", "icon": "bi-people", "items": [
            {"label": "My Subscribers", "href": "/sub-lco/subscribers"},
            {"label": "Add Customer", "href": "/sub-lco/subscribers/new"},
        ]},
        {"title": "Billing", "icon": "bi-receipt", "items": [
            {"label": "Invoices", "href": "/sub-lco/send-invoices"},
            {"label": "Payments", "href": "/sub-lco/payments"},
            {"label": "Commission", "href": "/sub-lco/commissions"},
        ]},
        {"title": "Field", "icon": "bi-geo", "items": [
            {"label": "Network Map", "href": "/sub-lco/network-map"},
            {"label": "Track Employee", "href": "/sub-lco/track-employee"},
            {"label": "Complaints", "href": "/sub-lco/complaints"},
        ]},
        {"title": "Account", "icon": "bi-gear", "items": [
            {"label": "FAQ / Help", "href": "/sub-lco/faq"},
            {"label": "Profile", "href": "/sub-lco/profile"},
        ]},
    ]

    EMPLOYEE_GROUPS: List[Dict] = [
        {"title": "Dashboard", "icon": "bi-speedometer2", "items": [
            {"label": "Overview", "href": "/employee/dashboard"},
            {"label": "My Tasks", "href": "/employee/tasks"},
        ]},
        {"title": "Field", "icon": "bi-geo", "items": [
            {"label": "Network Map", "href": "/employee/network-map"},
            {"label": "Complaints", "href": "/employee/complaints"},
        ]},
        {"title": "Account", "icon": "bi-gear", "items": [
            {"label": "FAQ / Help", "href": "/employee/faq"},
            {"label": "Profile", "href": "/employee/profile"},
        ]},
    ]

    CUSTOMER_GROUPS: List[Dict] = [
        {"title": "Account", "icon": "bi-person", "items": [
            {"label": "Dashboard", "href": "/customer/dashboard"},
            {"label": "Profile", "href": "/customer/profile"},
        ]},
        {"title": "Billing", "icon": "bi-receipt", "items": [
            {"label": "Payments", "href": "/customer/payments"},
            {"label": "Invoices", "href": "/customer/invoices"},
            {"label": "Pay Now", "href": "/customer/pay"},
        ]},
        {"title": "Support", "icon": "bi-life-preserver", "items": [
            {"label": "Complaints", "href": "/customer/complaints"},
            {"label": "FAQ / Help", "href": "/customer/faq"},
        ]},
    ]

    def _render_sitemap(role: str, groups: List[Dict], company_name: str = "") -> str:
        title = f"Site Map — {role.replace('-', ' ').title()}"
        body = []
        body.append(f"""<!doctype html><html><head>
<meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
          background: #f8fafc; color: #0f172a; margin: 0; padding: 32px 24px; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; color: #1e293b; }}
  .sub {{ color: #64748b; margin-bottom: 28px; font-size: 14px; }}
  .grid {{ display: grid; gap: 18px;
           grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }}
  .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
           padding: 18px 20px; }}
  .card h3 {{ margin: 0 0 12px; font-size: 15px; display:flex;
              align-items: center; gap: 8px; color: #0f172a; }}
  .card h3 i {{ color: #6366f1; font-size: 18px; }}
  .card ul {{ list-style: none; padding: 0; margin: 0; }}
  .card li {{ padding: 6px 0; }}
  .card a {{ color: #1e40af; text-decoration: none; font-size: 14px; }}
  .card a:hover {{ text-decoration: underline; }}
  .back {{ display:inline-block; margin-top:24px; color:#475569;
           text-decoration:none; font-size:13px; }}
</style></head><body><div class="wrap">""")
        body.append(f'<h1><i class="bi bi-diagram-3"></i> {title}</h1>')
        body.append('<div class="sub">All available sections at a glance. '
                     'Click any link to jump there.</div>')
        body.append('<div class="grid">')
        for g in groups:
            body.append(f'<div class="card" data-testid="sitemap-group-{g["title"].lower().replace(" ", "-")}">')
            body.append(f'  <h3><i class="bi {g["icon"]}"></i> {g["title"]}</h3>')
            body.append('  <ul>')
            for it in g["items"]:
                body.append(
                    f'    <li><a href="{it["href"]}" '
                    f'data-testid="sitemap-link-{it["href"].rsplit("/",1)[-1] or "root"}">'
                    f'{it["label"]}</a></li>'
                )
            body.append('  </ul>')
            body.append('</div>')
        body.append('</div>')
        body.append(f'<a class="back" href="javascript:history.back()">← Back</a>')
        body.append('</div></body></html>')
        return "\n".join(body)

    def _guard(request: Request) -> bool:
        return bool(request.session.get("user_id"))

    @app.get("/admin/sitemap")
    async def admin_sitemap(request: Request):
        if not _guard(request):
            return RedirectResponse(url="/admin/login", status_code=302)
        return Response(_render_sitemap("Admin", ADMIN_GROUPS),
                        media_type="text/html")

    @app.get("/sub-lco/sitemap")
    async def sub_lco_sitemap(request: Request):
        if not _guard(request):
            return RedirectResponse(url="/admin/login", status_code=302)
        return Response(_render_sitemap("Sub-LCO", SUB_LCO_GROUPS),
                        media_type="text/html")

    @app.get("/employee/sitemap")
    async def employee_sitemap(request: Request):
        if not _guard(request):
            return RedirectResponse(url="/admin/login", status_code=302)
        return Response(_render_sitemap("Employee", EMPLOYEE_GROUPS),
                        media_type="text/html")

    @app.get("/customer/sitemap")
    async def customer_sitemap(request: Request):
        if not _guard(request):
            return RedirectResponse(url="/login", status_code=302)
        return Response(_render_sitemap("Customer", CUSTOMER_GROUPS),
                        media_type="text/html")
