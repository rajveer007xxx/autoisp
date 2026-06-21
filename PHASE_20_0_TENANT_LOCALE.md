# Phase 20.0 — Tenant Locale Settings

## Overview
Per-tenant locale configuration for date format, time format, currency symbol,
currency code, week-start day, and country preset. White-label-ready for
international resellers — no more hardcoded India defaults at the framework
layer.

## What changed
1. **DB** — `companies` table gains: `date_format`, `time_format`,
   `currency_symbol`, `currency_code`, `week_start`. All existing rows
   seeded with India defaults (DD/MM/YYYY, 24h, ₹, INR, monday).
2. **ORM (`database.py`)** — `Company` model extended with the 5 new
   columns.
3. **Backend (`main.py`)**:
   - New Jinja globals: `tenant_locale(request)`, `currency_symbol(request)`,
     `fmt_date(request, dt)`, `fmt_time(request, dt)`, `fmt_datetime(request, dt)`.
     Reads from the current session's company_id and falls back to India
     defaults when no session is available.
   - `/api/profile/get` now returns the 5 locale fields.
   - `update_profile` (form POST) saves them.
4. **UI (`templates/admin_profile.html`)**:
   - New **Locale Settings** card placed between **Company Detail** and
     **Bank Details**.
   - 23 country presets (IN, US, GB, EU, AE, SA, BD, NP, LK, PK, ZA, NG,
     KE, PH, ID, MY, TH, VN, BR, MX, CA, AU + Other) auto-fill the
     currency/date/time/week-start fields on select.
   - Pre-fill JS reads current values from `/api/profile/get`.
   - All inputs have `data-testid` attributes (locale-country,
     locale-currency-symbol, locale-currency-code, locale-date-format,
     locale-time-format, locale-week-start).

## How templates use it (going forward)
```jinja2
{{ currency_symbol(request) }}{{ '{:,.0f}'.format(amount) }}
{{ fmt_date(request, invoice.created_at) }}
{{ fmt_datetime(request, payment.received_at) }}
```

## Files touched
- `/opt/ispbilling/admin-portal/database.py` (Company ORM)
- `/opt/ispbilling/admin-portal/main.py` (Jinja globals + GET/POST endpoints)
- `/opt/ispbilling/admin-portal/templates/admin_profile.html`
- DB migration applied live (28 rows seeded)

## Testing
- ✅ DB migration applied, 28 rows seeded with India defaults.
- ✅ `GET /api/profile/get` returns locale fields.
- ✅ `POST /api/profile/update` saves locale fields (verified via DB).
- ✅ UI card renders between Company Detail and Bank Details.
- ✅ Country preset switch: US, GB, AE confirmed via Playwright DOM read.
- ✅ Jinja globals callable; `fmt_date(2026-03-15)` → `15/03/2026` in
  India locale.

