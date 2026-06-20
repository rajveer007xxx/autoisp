# _GLOBAL_TZ_FIX_V2_
"""tz_utils.py — single source of truth for IST formatting.

The admin portal runs on a server in Asia/Kolkata (IST, +05:30).  Most
DB columns hold UTC datetimes (`datetime.utcnow`), while some FreeRADIUS
fields hold server-local IST strings.  These helpers normalise both.

All functions are tolerant of `None`/blank and *never raise*.
"""
from datetime import datetime, timedelta, timezone

IST_OFFSET = timedelta(hours=5, minutes=30)


def utc_dt_to_ist(dt):
    """Naive UTC datetime → naive IST datetime."""
    if not dt:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt + IST_OFFSET


def ist_str(dt, with_suffix=False):  # _TZ_FIX_ROUND3_ default off
    """UTC datetime → 'DD-MM-YYYY HH:MM[ IST]'."""
    d = utc_dt_to_ist(dt)
    if not d:
        return ""
    s = d.strftime("%d-%m-%Y %H:%M")
    return f"{s} IST" if with_suffix else s


def ist_str_full(dt):
    """UTC datetime → 'DD-MM-YYYY HH:MM:SS IST'."""
    d = utc_dt_to_ist(dt)
    if not d:
        return ""
    return d.strftime("%d-%m-%Y %H:%M:%S") + " IST"


def epoch_to_ist_str(epoch, with_suffix=False):
    """Unix epoch (UTC) → 'DD-MM-YYYY HH:MM[ IST]'."""
    try:
        e = int(epoch or 0)
    except (TypeError, ValueError):
        return ""
    if not e:
        return ""
    dt = datetime.utcfromtimestamp(e) + IST_OFFSET
    s = dt.strftime("%d-%m-%Y %H:%M")
    return f"{s} IST" if with_suffix else s


def ist_local_str(db_str, with_suffix=False):  # _TZ_FIX_ROUND3_ default off
    """A DB string that is **already** server-local IST → canonical
    'DD-MM-YYYY HH:MM[ IST]'. Tolerates "YYYY-MM-DD HH:MM:SS[.fff]"
    and ISO variants."""
    if not db_str:
        return ""
    s = str(db_str).strip()
    try:
        base = s.split(".")[0].replace("T", " ")
        y, mo, d = base[:10].split("-")
        t = base[11:16]
        out = f"{d}-{mo}-{y} {t}"
        return f"{out} IST" if with_suffix else out
    except Exception:
        return s
