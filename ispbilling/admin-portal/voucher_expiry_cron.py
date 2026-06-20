#!/opt/ispbilling/venv/bin/python
"""s36b8: Voucher auto-expiry cron.

Runs every 15 minutes via systemd timer. Flips any voucher whose
`expires_at < now` and status == 'unused' to 'expired'. Idempotent — safe to
rerun and never touches already-used vouchers.
"""
from __future__ import annotations
import os, sys, logging
from datetime import datetime

sys.path.insert(0, "/opt/ispbilling/admin-portal")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("voucher-expiry")


def main() -> int:
    try:
        from database import SessionLocal, HotspotVoucher
    except Exception as e:
        log.error("import failed: %s", e)
        return 1

    db = SessionLocal()
    try:
        now = datetime.utcnow()
        q = db.query(HotspotVoucher).filter(
            HotspotVoucher.status == "unused",
            HotspotVoucher.expires_at.isnot(None),
            HotspotVoucher.expires_at < now,
        )
        stale = q.all()
        if not stale:
            log.info("no stale vouchers")
            return 0
        for v in stale:
            v.status = "expired"
        db.commit()
        log.info("expired %d vouchers", len(stale))
        return 0
    except Exception as e:
        db.rollback()
        log.exception("voucher-expiry failed: %s", e)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
