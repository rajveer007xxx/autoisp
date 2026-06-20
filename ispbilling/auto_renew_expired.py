#!/usr/bin/env python3
"""
Auto-renewal script for expired customers.
Runs daily at 01:15 AM IST via cron to automatically renew customers whose subscription expired yesterday.

Requirements:
- Customers with end_date = yesterday (IST)
- Exclude customers with status = 'Suspended'
- Exclude customers with auto_renew = 'No' or NULL
- Generate invoice and send email
- Reset received_since_reset to 0
- Set status to 'Deactive' after renewal
"""

import sys
import os
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# v10 — admin-portal must be EARLIER in sys.path than /opt/ispbilling
# so that `services.billing` (which does `from main import ...`) resolves
# to /opt/ispbilling/admin-portal/main.py, not /opt/ispbilling/main.py.
sys.path.insert(0, '/opt/ispbilling/admin-portal')
sys.path.insert(1, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, Customer, Invoice
from services.billing import renew_customer_core, send_invoice_email_sync


def get_yesterday_ist() -> str:
    """Get yesterday's date in IST timezone as YYYY-MM-DD string"""
    ist = ZoneInfo("Asia/Kolkata")
    today_ist = datetime.now(ist).date()
    yesterday_ist = today_ist - timedelta(days=1)
    return yesterday_ist.strftime('%Y-%m-%d')


def get_today_ist() -> datetime:
    """Get today's datetime in IST timezone"""
    ist = ZoneInfo("Asia/Kolkata")
    return datetime.now(ist)


def check_already_renewed(customer_id: str, company_id: str, intended_start_date: str, db) -> bool:
    """
    Check if customer was already renewed for this period (idempotency check).
    Returns True if an invoice already exists for the intended period.
    """
    existing_invoice = db.query(Invoice).filter(
        Invoice.company_id == company_id,
        Invoice.customer_id == customer_id,
        Invoice.start_date == intended_start_date
    ).first()
    
    return existing_invoice is not None


def auto_renew_expired_customers(as_of: str = None):
    """Main function to auto-renew all eligible expired customers.

    Args:
        as_of: Optional 'YYYY-MM-DD' string overriding "today IST".
               Defaults to real today in IST. When provided, the cutoff
               (end_date <= as_of - 1 day) shifts accordingly. Use this
               to process a backlog catch-up or renew users whose
               end_date == today IST (without waiting for tomorrow's cron).
    """

    print(f"\n{'='*80}")
    print(f"Auto-Renewal Job Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}\n")

    # s37f: cutoff = (as_of or today-IST) - 1 day. Customers with end_date
    # <= cutoff are eligible. This replaces the prior strict `== yesterday`
    # filter, so missed cron runs / backlog customers now auto-catch-up.
    if as_of:
        try:
            ref_date = datetime.strptime(as_of, "%Y-%m-%d").date()
        except ValueError:
            print(f"✗ invalid --as-of value {as_of!r}; expected YYYY-MM-DD")
            return
        # __s56s_strict_past__ — strict cutoff: customer keeps service
        # THROUGH their entire expiry day. The cron runs the NEXT day.
        from datetime import timedelta as _td
        cutoff_date = ref_date - _td(days=1)
        today_ist = datetime.combine(ref_date, datetime.min.time(),
                                     tzinfo=ZoneInfo("Asia/Kolkata"))
    else:
        today_ist = get_today_ist()
        # __s56s_strict_past__ — strict cutoff: customer keeps service
        # THROUGH their entire expiry day. The cron runs the NEXT day.
        from datetime import timedelta as _td
        cutoff_date = today_ist.date() - _td(days=1)

    cutoff = cutoff_date.strftime('%Y-%m-%d')
    intended_start_date = today_ist.strftime('%Y-%m-%d')

    print(f"IST Timezone: Asia/Kolkata")
    print(f"Reference 'today' (IST): {intended_start_date}")
    print(f"Target end_date (strictly < IST today): {cutoff}")
    print(f"Intended renewal period: 1 month\n")

    db = SessionLocal()

    try:
        # s37k: normalise end_date on the Python side so mixed-format rows
        # (DD-MM-YYYY vs YYYY-MM-DD) are all compared correctly against
        # the cutoff. Pull candidates with minimal filters then filter
        # in Python.
        def _norm_date(s):
            if not s: return None
            s = str(s).strip()
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d'):
                try:
                    return datetime.strptime(s, fmt).date()
                except ValueError:
                    continue
            return None

        cutoff_date_obj = _norm_date(cutoff)
        _candidates = db.query(Customer).filter(
            Customer.end_date.isnot(None),
            Customer.end_date != '',
            Customer.status.notin_(['Suspended','Terminated','Disabled','Deactive']),  # _S39R5FIX13_
            Customer.auto_renew == 'Yes'
        ).all()
        # __PHASE0_STRICT_YESTERDAY__ — strict equality per operator's
        # explicit Jun-2-2026 instruction. The previous '<= cutoff'
        # catch-up behaviour caused the Jun 1 2026 incident: when
        # auto_renew was enabled on previously-disabled customers,
        # months of accumulated backlog (184 from FIBERNET) all
        # became eligible on the same morning -> SQLite lock storm
        # + captive-portal flood + systemd SIGKILL loop.
        # Now the cron processes EXACTLY one day of expiries:
        #   today IST = July 1  →  picks customers with end_date = June 30
        #   today IST = July 26 →  picks customers with end_date = July 25
        # If a cron run is ever missed, use:
        #   python /opt/ispbilling/auto_renew_expired.py --as-of YYYY-MM-DD
        # to manually catch up for that specific day.
        eligible_customers = [
            c for c in _candidates
            if (_d := _norm_date(c.end_date)) and _d == cutoff_date_obj
        ]
        
        print(f"Found {len(eligible_customers)} eligible customers for auto-renewal\n")
        
        if len(eligible_customers) == 0:
            print("No customers to renew. Exiting.\n")
            return
        
        renewed_count = 0
        skipped_count = 0
        failed_count = 0
        
        # __PHASE0_THROTTLE__ pace renewals: 0.3 s between customers
        # spreads the 03:10 AM thundering herd. Add a top-of-file
        # import once.
        import time as _phase0_time
        for customer in eligible_customers:
            _phase0_time.sleep(0.3)
            try:
                customer_id = customer.customer_id
                company_id = customer.company_id
                customer_name = customer.customer_name
                customer_email = customer.customer_email
            
                print(f"Processing: {customer_id} - {customer_name}")
            
                if check_already_renewed(customer_id, company_id, intended_start_date, db):
                    print(f"  ⊘ SKIPPED: Already renewed for period starting {intended_start_date}")
                    skipped_count += 1
                    continue
            
                # s37i + __s56s_plan_fallback__: use customer.period first,
                # but fall back to plan.validity/30 when missing/zero.
                try:
                    _period_months = int(customer.period or 0)
                except (TypeError, ValueError):
                    _period_months = 0
                if _period_months < 1:
                    # Look up plan.validity (stored as days) → months
                    try:
                        from sqlalchemy import text as _sa_text
                        _plan_row = db.execute(_sa_text(
                            "SELECT validity FROM plans "
                            "WHERE id=:pid AND company_id=:cid LIMIT 1"
                        ), {"pid": customer.plan_id, "cid": company_id}).fetchone()
                        if _plan_row and _plan_row[0]:
                            _period_months = max(1, int(_plan_row[0]) // 30)
                    except Exception:
                        pass
                if _period_months < 1:
                    _period_months = 1
                result = renew_customer_core(
                    company_id=company_id,
                    customer_id=customer_id,
                    start_date=today_ist,
                    period_months=_period_months,
                    with_invoice=True,
                    db=db,
                    source="auto"
                )
            
                if not result.get('success'):
                    print(f"  ✗ FAILED: {result.get('message', 'Unknown error')}")
                    failed_count += 1
                    continue
            
                # Session 21 / s37j — auto-renew leaves balance > 0 until payment,
                # so mark the user Expired so FreeRADIUS rejects. We use an
                # explicit UPDATE (not ORM attribute set) because the outer-loop
                # `customer` object may hold stale column values that SQLAlchemy
                # would re-persist if we let it emit a whole-row UPDATE.
                from sqlalchemy import update as _sa_update
                db.execute(
                    _sa_update(Customer)
                      .where(Customer.customer_id == customer_id,
                             Customer.company_id == company_id)
                      .values(status='Expired')
                )
                db.commit()

                # s41c: kick-on-expire — DO NOT disable the PPP secret
                # (that blocks the user from authenticating at all). Instead
                # just drop the LIVE session via PPP-Active remove so they
                # re-auth and pick up the parking-pool Framed-Pool from the
                # RADIUS reply on the next dial. RADIUS resync at the end of
                # this batch will have already rewritten isp-users.
                try:
                    import sys as _sys
                    if '/opt/ispbilling/admin-portal' not in _sys.path:
                        _sys.path.insert(0, '/opt/ispbilling/admin-portal')
                    from main import _router_kick_active_session as _kick_session
                    ok, info = _kick_session(db, customer.company_id, customer)
                    print(f"  ↳ session kicked → re-auth into parking: ok={ok} info={info}")
                except Exception as _ke:
                    print(f"  ⚠ kick-session failed (non-fatal): {_ke}")

                invoice_no = result.get('invoice_no')
                print(f"  ✓ RENEWED: Invoice {invoice_no} generated")
                print(f"  ✓ STATUS: Changed to 'Expired' (balance due — kicked to parking pool)")
            
                if customer_email and result.get('pdf_data'):
                    email_sent = send_invoice_email_sync(
                        invoice_data=result.get('invoice_data'),
                        customer_email=customer_email,
                        company_data=result.get('company_data'),
                        pdf_data=result.get('pdf_data'),
                        billing_type=result.get('customer_type', 'PREPAID')
                    )
                
                    if email_sent:
                        print(f"  ✓ EMAIL: Sent to {customer_email}")
                    else:
                        print(f"  ⚠ EMAIL: Failed to send to {customer_email}")
                else:
                    if not customer_email:
                        print(f"  ⊘ EMAIL: No email address on file")
                    else:
                        print(f"  ⊘ EMAIL: No PDF data generated")
            
                renewed_count += 1

            except Exception as _per_err:
                # s37j: isolate failures so one bad customer doesn't kill
                # the whole job.
                print(f"  ✗ EXCEPTION: {_per_err}")
                failed_count += 1
                try:
                    db.rollback()
                except Exception:
                    pass
                continue

        # Session 21 — batch resync FreeRADIUS so status changes reflect
        # immediately on the router (Access-Reject for all non-Active).
        try:
            from radius_network import _sync_freeradius_all_tenants
            _sync_freeradius_all_tenants(db, restart=True)
        except Exception as _e:
            print(f"  [warn] post-renew radius resync failed: {_e}")
            print()
        
        print(f"\n{'='*80}")
        print(f"Auto-Renewal Job Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}")
        print(f"Total Eligible: {len(eligible_customers)}")
        print(f"Successfully Renewed: {renewed_count}")
        print(f"Skipped (Already Renewed): {skipped_count}")
        print(f"Failed: {failed_count}")
        print(f"{'='*80}\n")
        
    except Exception as e:
        print(f"\n✗ CRITICAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


def expire_non_renew_customers(as_of: str = None):
    """v10 — customers with auto_renew != Yes whose end_date has passed
    are flipped from Active to Expired and disconnected on the router.
    Without this they stay Active forever past expiry."""
    from datetime import datetime as _dt, timedelta as _td
    from zoneinfo import ZoneInfo as _ZI
    if as_of:
        try:
            ref = _dt.strptime(as_of, '%Y-%m-%d').date()
        except ValueError:
            return
        today = ref
    else:
        today = _dt.now(_ZI('Asia/Kolkata')).date()
    cutoff = today  # _S39R5FIX5_INCLUSIVE_TODAY_

    def _norm(s):
        if not s: return None
        s = str(s).strip()
        for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y/%m/%d'):
            try: return _dt.strptime(s, fmt).date()
            except ValueError: continue
        return None

    db = SessionLocal()
    flipped = 0
    try:
        cands = db.query(Customer).filter(
            Customer.end_date.isnot(None),
            Customer.end_date != '',
            Customer.status == 'Active',
        ).all()
        for c in cands:
            ar = (getattr(c, 'auto_renew', '') or '').strip().lower()
            if ar == 'yes':
                continue
            d = _norm(c.end_date)
            if not d or d > cutoff:
                continue
            try:
                c.status = 'Expired'
                db.commit()
                flipped += 1
                print(f'  ⏰ {c.customer_id} {c.customer_name!r} → Expired (end_date={c.end_date}, auto_renew={ar or "NULL"})')
                try:
                    import sys as _s
                    if '/opt/ispbilling/admin-portal' not in _s.path:
                        _s.path.insert(0, '/opt/ispbilling/admin-portal')
                    # s41c: kick LIVE session only (do NOT disable secret).
                    from main import _router_kick_active_session as _kick_session
                    ok, info = _kick_session(db, c.company_id, c)
                    print(f'    ↳ session kicked → re-auth into parking: ok={ok} info={str(info)[:80]}')
                except Exception as _ke:
                    print(f'    ⚠ kick-session failed (non-fatal): {_ke}')
            except Exception as _e:
                db.rollback()
                print(f'  ✗ {c.customer_id}: {_e}')
        print(f'Non-renew expiry sweep: {flipped} flipped to Expired')
        # s41c: re-render isp-users so newly-Expired users get parking
        # attributes (Framed-Pool=parking-pool, Mikrotik-Group=parking).
        if flipped:
            try:
                import sys as _sx
                if '/opt/ispbilling/admin-portal' not in _sx.path:
                    _sx.path.insert(0, '/opt/ispbilling/admin-portal')
                from radius_network import _sync_freeradius_all_tenants
                rr = _sync_freeradius_all_tenants(db, restart=True)
                print(f'    ↳ radius resync: users_written={rr.get("users_written")}, restarted={rr.get("restarted")}')
            except Exception as _re:
                print(f'    ⚠ radius resync failed (non-fatal): {_re}')
    finally:
        db.close()


def _parse_args():
    import argparse
    ap = argparse.ArgumentParser(description="Auto-renew expired customers")
    ap.add_argument("--as-of", metavar="YYYY-MM-DD",
        help="Override 'today' for this run (defaults to today in IST). "
             "Use for one-off catch-ups or to include customers whose "
             "end_date == today IST.")
    return ap.parse_args()


if __name__ == "__main__":
    _args = _parse_args()
    auto_renew_expired_customers(as_of=_args.as_of)
    print("\n--- non-renew expiry sweep ---")
    expire_non_renew_customers(as_of=_args.as_of)
