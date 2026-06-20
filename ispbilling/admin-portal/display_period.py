"""display_period.py — Session s41q (Feb 2026)

Single source of truth for the **display period** rendered on invoices
and receipts.

Background
==========
- The customer's `start_date` and `end_date` columns store the
  NEXT cycle dates (the period being billed forward for).
- For PREPAID this is what we want to render — the customer is paying
  ahead, so the displayed period equals the DB period.
- For POSTPAID the customer is paying for the JUST-COMPLETED cycle.
  The DB columns still hold the next cycle (so renewal scheduling
  works the same as prepaid), so the renderer must shift the
  displayed period BACK by `period_months`:
      display_start = start_date − period_months
      display_end   = start_date − 1 day

Centralizing this here means the invoice PDF generator and the
receipt period helper produce IDENTICAL strings — no risk of one
drifting from the other and creating an "invoice vs receipt
disagree" bug.
"""
from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Optional, Tuple, Union

try:
    from dateutil.relativedelta import relativedelta
except ImportError:  # pragma: no cover — dateutil is a hard dep on this VPS
    relativedelta = None


_DateLike = Union[str, datetime, date, None]


def _parse_ymd(value: _DateLike) -> Optional[datetime]:
    """Accept str / datetime / date; return datetime or None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        return None
    # Accept 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' (truncate the time).
    s = s[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def is_postpaid(billing_type: Optional[str]) -> bool:
    """Robust postpaid check: case-insensitive, tolerant of None."""
    return str(billing_type or "").strip().upper() == "POSTPAID"


def get_display_period(
    start_date: _DateLike,
    end_date: _DateLike,
    period_months: Optional[int],
    billing_type: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return `(display_start_ymd, display_end_ymd)` for invoice/receipt
    rendering.

    - PREPAID / unknown billing type: returns the inputs verbatim (after
      normalising to 'YYYY-MM-DD').
    - POSTPAID with a valid `period_months ≥ 1` and parsable `start_date`:
      shifts back by `period_months` months and (start_date − 1 day).
    - If anything is missing or unparseable, falls through to the
      verbatim inputs — never raises.
    """
    sd_dt = _parse_ymd(start_date)
    ed_dt = _parse_ymd(end_date)
    pm = 0
    try:
        pm = int(period_months) if period_months else 0
    except (TypeError, ValueError):
        pm = 0

    if is_postpaid(billing_type) and sd_dt is not None and pm >= 1 and relativedelta is not None:
        try:
            disp_start = sd_dt - relativedelta(months=pm)
            disp_end = sd_dt - timedelta(days=1)
            return (disp_start.strftime("%Y-%m-%d"),
                    disp_end.strftime("%Y-%m-%d"))
        except Exception:
            # Fall through to verbatim on any unexpected error.
            pass

    return (
        sd_dt.strftime("%Y-%m-%d") if sd_dt else None,
        ed_dt.strftime("%Y-%m-%d") if ed_dt else None,
    )


def get_display_period_ddmmyyyy(
    start_date: _DateLike,
    end_date: _DateLike,
    period_months: Optional[int],
    billing_type: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Same as `get_display_period` but returns DD-MM-YYYY strings."""
    sd_ymd, ed_ymd = get_display_period(start_date, end_date,
                                         period_months, billing_type)

    def _fmt(s):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d-%m-%Y")
        except Exception:
            return s

    return (_fmt(sd_ymd), _fmt(ed_ymd))
