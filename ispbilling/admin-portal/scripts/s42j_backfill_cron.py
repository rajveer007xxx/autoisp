#!/usr/bin/env python3
"""S42J — periodic tenant-id backfill for shared FreeRADIUS tables.

Resolves `company_id` on any radpostauth / radacct rows inserted since
the previous run. Run by systemd timer every minute.
"""
import os
import sys

sys.path.insert(0, "/opt/ispbilling/admin-portal")

from database import SessionLocal  # noqa: E402
from radpostauth_tenant import backfill_all  # noqa: E402


def main() -> int:
    db = SessionLocal()
    try:
        stats = backfill_all(db, limit_each=int(os.getenv("S42J_LIMIT", "20000")))
        print(f"[s42j-backfill] {stats}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
