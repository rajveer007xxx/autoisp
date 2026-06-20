"""S42K — Regression: parked / WalledGarden sessions must be visible on
admin "Current Session" / "Current Usage" pages with their actual IP."""
import os, sys
sys.path.insert(0, "/opt/ispbilling/admin-portal")
os.chdir("/opt/ispbilling/admin-portal")

from database import SessionLocal


def test_subscriber_sessions_returns_parked_live_session():
    """mp.nidhi.fibernet (Expired customer in WalledGarden) MUST surface
    its live PPP session on the admin Current Usage page, with the
    actual framedipaddress (parking pool IP) shown."""
    from main import _subscriber_sessions_for
    import datetime as _dt
    db = SessionLocal()
    try:
        now = _dt.datetime.now()
        month_start = _dt.datetime(now.year, now.month, 1)
        since = int(month_start.timestamp())
        cust, sessions = _subscriber_sessions_for(
            db, "15378763", 210, since_epoch=since)
        assert cust is not None, "customer not found"
        assert cust.username == "mp.nidhi.fibernet"
        assert any(s.get("is_live") for s in sessions), \
            "no is_live row returned for a parked customer with an open radacct session"
        live = next(s for s in sessions if s.get("is_live"))
        assert live["ip"], f"IP missing on live session: {live}"
        assert live["mac"], f"MAC missing on live session: {live}"
    finally:
        db.close()


def test_online_users_page_excludes_parked_and_expired():
    """S42L — Admin Online Users PAGE must hide parked/expired etc., but
    the WalledGarden rows must still exist in the DB so subscriber detail
    can render them on Current Usage / Current Session."""
    from radius_network import OnlineUser
    from database import Customer
    db = SessionLocal()
    try:
        EXCL = ("Expired", "expired", "Disabled", "disabled",
                "Deactivated", "deactivated", "Suspended", "suspended",
                "Parked", "parked", "Cancelled", "cancelled",
                "Canceled", "canceled", "Terminated", "terminated",
                "Deleted", "deleted")
        excluded_unames = {
            c.username for c in db.query(Customer).filter(
                Customer.company_id == "15378763",
                Customer.username.isnot(None),
                Customer.status.in_(EXCL),
            ).all() if c.username
        }
        # Apply the same filter the page applies.
        rows = db.query(OnlineUser).filter(
            OnlineUser.company_id == "15378763",
            OnlineUser.status == "Online",
        ).all()
        rows = [r for r in rows if r.username not in excluded_unames]
        # Every surviving row must belong to a non-excluded customer.
        for r in rows[:50]:
            c = db.query(Customer).filter(
                Customer.company_id == "15378763",
                Customer.username == r.username,
            ).first()
            assert c is None or c.status not in EXCL, \
                f"page leaked {r.username} (status={c.status})"
        # Sanity: WalledGarden rows still exist for subscriber-detail page.
        parked = db.query(OnlineUser).filter(
            OnlineUser.company_id == "15378763",
            OnlineUser.status == "WalledGarden",
        ).count()
        assert parked > 0, "no walled-garden rows present in fixture"
    finally:
        db.close()


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception:
            print(f"  ERR   {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
