#!/usr/bin/env python3
"""Standalone DB backup cron — no FastAPI import needed."""
import os, gzip, hashlib, shutil, sqlite3
from datetime import datetime
DB = "/var/lib/autoispbilling/autoispbilling.db"
BD = "/var/lib/autoispbilling/backups"
os.makedirs(BD, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d-%H%M%S")
raw = os.path.join(BD, f"db-{ts}.sqlite")
gz = raw + ".gz"
src = sqlite3.connect(DB); dst = sqlite3.connect(raw)
with dst: src.backup(dst)
src.close(); dst.close()
with open(raw,"rb") as f_in, gzip.open(gz,"wb",compresslevel=6) as f_out:
    shutil.copyfileobj(f_in, f_out)
os.remove(raw)
size = os.path.getsize(gz)
sha = hashlib.sha256(open(gz,"rb").read()).hexdigest()
eng = sqlite3.connect(DB)
eng.execute("""INSERT INTO db_backups(filename, size_bytes, sha256, created_by, trigger)
               VALUES (?, ?, ?, ?, ?)""",
            (os.path.basename(gz), size, sha, "cron", "auto"))
eng.commit(); eng.close()
files = sorted([f for f in os.listdir(BD) if f.endswith(".gz")])
for old in files[:-30]:
    try: os.remove(os.path.join(BD, old))
    except Exception: pass
print(f"[backup_cron] OK file={os.path.basename(gz)} size={size}")
