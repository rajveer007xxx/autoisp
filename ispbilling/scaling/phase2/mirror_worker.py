"""
Phase 2 mirror worker — drains pg_mirror_outbox to the Postgres shadow.

Run via:  systemd unit /etc/systemd/system/isp-pg-mirror.service
"""
import logging
import os
import sqlite3
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

DB_SQLITE = "/var/lib/autoispbilling/autoispbilling.db"
DB_PG = os.environ["DUAL_WRITE_PG_URL"]
BATCH = 200
POLL_SEC = 5

log = logging.getLogger("isp.mirror")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
pg = create_engine(DB_PG, pool_pre_ping=True)


def ensure_outbox():
    with sqlite3.connect(DB_SQLITE, timeout=30) as c:
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("""CREATE TABLE IF NOT EXISTS pg_mirror_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            op TEXT NOT NULL,     -- 'upsert' | 'delete'
            queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            mirrored_at TIMESTAMP,
            attempts INTEGER DEFAULT 0,
            last_error TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_outbox_pending "
                  "ON pg_mirror_outbox(mirrored_at) WHERE mirrored_at IS NULL")
        c.commit()


def drain_one_batch():
    with sqlite3.connect(DB_SQLITE, timeout=30) as c:
        c.execute("PRAGMA busy_timeout=5000")
        rows = c.execute(
            "SELECT id, table_name, row_id, op FROM pg_mirror_outbox "
            "WHERE mirrored_at IS NULL ORDER BY id LIMIT ?", (BATCH,)
        ).fetchall()
    if not rows:
        return 0
    ok = 0
    for outbox_id, table, row_id, op in rows:
        try:
            if op == "delete":
                with pg.begin() as p:
                    p.execute(text(f"DELETE FROM {table} WHERE id=:i"),
                              {"i": row_id})
            else:
                with sqlite3.connect(DB_SQLITE, timeout=30) as c:
                    c.row_factory = sqlite3.Row
                    src = c.execute(f"SELECT * FROM {table} WHERE id=?",
                                    (row_id,)).fetchone()
                if not src:
                    raise RuntimeError(f"row not found in source: {table}.{row_id}")
                cols = list(src.keys())
                vals = {k: src[k] for k in cols}
                with pg.begin() as p:
                    sets = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != "id")
                    p.execute(text(
                        f"INSERT INTO {table} ({','.join(cols)}) "
                        f"VALUES ({','.join(':'+c for c in cols)}) "
                        f"ON CONFLICT (id) DO UPDATE SET {sets}"
                    ), vals)
            with sqlite3.connect(DB_SQLITE, timeout=30) as c:
                c.execute("PRAGMA busy_timeout=5000")
                c.execute("UPDATE pg_mirror_outbox SET mirrored_at=datetime('now'), "
                          "last_error=NULL WHERE id=?", (outbox_id,))
                c.commit()
            ok += 1
        except Exception as e:
            log.warning("mirror failed for %s.%s: %s", table, row_id, e)
            with sqlite3.connect(DB_SQLITE, timeout=30) as c:
                c.execute("PRAGMA busy_timeout=5000")
                c.execute("UPDATE pg_mirror_outbox SET attempts=attempts+1, "
                          "last_error=? WHERE id=?", (str(e)[:500], outbox_id))
                c.commit()
    log.info("drained %d/%d", ok, len(rows))
    return ok


def main():
    ensure_outbox()
    log.info("Phase 2 mirror worker started")
    while True:
        try:
            n = drain_one_batch()
        except Exception as e:
            log.error("batch failed: %s", e)
            n = 0
        if n == 0:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
