#!/usr/bin/env python3
"""[PHASE-3] Standalone DB backup cron — now PG (pg_dump) instead of SQLite.

Previously dumped /var/lib/autoispbilling/autoispbilling.db via sqlite3.backup().
Now produces a pg_dump custom-format file. Retention = last 30 dumps.
"""
import os
import gzip
import hashlib
import shutil
import subprocess
import sys
from datetime import datetime

if "/opt/ispbilling" not in sys.path:
    sys.path.insert(0, "/opt/ispbilling")
import db_compat  # noqa: E402

BD = "/var/lib/autoispbilling/backups"
os.makedirs(BD, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d-%H%M%S")
raw = os.path.join(BD, f"pg-{ts}.dump")
gz = raw + ".gz"

# pg_dump --format=custom: still a single file but compressible
subprocess.check_call(
    ["sudo", "-u", "postgres", "pg_dump", "--format=custom",
     "--file=" + raw, "autoispbilling"]
)

with open(raw, "rb") as f_in, gzip.open(gz, "wb", compresslevel=6) as f_out:
    shutil.copyfileobj(f_in, f_out)
os.remove(raw)

size = os.path.getsize(gz)
sha = hashlib.sha256(open(gz, "rb").read()).hexdigest()

# Audit row in db_backups (table is PG now via db_compat)
try:
    con = db_compat.get_raw_conn(timeout=10.0)
    con.cursor().execute(
        "INSERT INTO db_backups(filename, size_bytes, sha256, created_by, trigger) "
        "VALUES (?, ?, ?, ?, ?)",
        (os.path.basename(gz), size, sha, "cron", "auto"),
    )
    try:
        con.commit()
    except Exception:
        pass
    con.close()
except Exception as e:
    print(f"[backup_cron] audit-row insert skipped: {e}")

# Retention — keep last 30 PG dumps
files = sorted([f for f in os.listdir(BD) if f.startswith("pg-") and f.endswith(".gz")])
for old in files[:-30]:
    try:
        os.remove(os.path.join(BD, old))
    except Exception:
        pass

print(f"[backup_cron] OK file={os.path.basename(gz)} size={size}")
