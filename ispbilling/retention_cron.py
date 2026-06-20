#!/usr/bin/env python3
"""retention_cron.py — runs daily via isp-retention.timer.
Imports the retention runner from phase27_compliance and executes one pass.
Writes log to /var/log/autoispbilling/retention.log (configured via systemd)."""
import sys
import os

sys.path.insert(0, "/opt/ispbilling/admin-portal")
sys.path.insert(0, "/opt/ispbilling")

if __name__ == "__main__":
    try:
        from phase27_compliance import _run_retention_once
        result = _run_retention_once()
        print(f"[retention_cron] OK cutoff={result['cutoff']}")
        for s in result["summary"]:
            print(f"  {s}")
        sys.exit(0)
    except Exception as e:
        print(f"[retention_cron] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
