#!/usr/bin/env python3
"""Migrate SQLite → Postgres without pgloader's Lisp-double bug.

Strategy:
  1. Read schema from each SQLite table via PRAGMA.
  2. Create the equivalent Postgres table (drop if exists) with safe types:
       INTEGER PRIMARY KEY AUTOINCREMENT -> BIGSERIAL PRIMARY KEY
       INTEGER                            -> BIGINT
       REAL                               -> DOUBLE PRECISION
       NUMERIC                            -> NUMERIC
       BOOLEAN / TINYINT                  -> SMALLINT
       TEXT/VARCHAR/CHAR/CLOB             -> TEXT
       BLOB                               -> BYTEA
       DATETIME/TIMESTAMP                 -> TIMESTAMPTZ
       DATE                               -> DATE
  3. Stream rows with executemany() in 5000-row batches. Floats are
     passed natively as Python float, not Lisp strings, so the d0 bug
     is gone.
  4. Replay indexes from sqlite_master (skipping ones with NIL exprs).
  5. Reset sequences to MAX(id)+1.
  6. Report exact row count diffs.

Idempotent: safe to re-run. No FK constraints created (matches SQLite's
default behaviour and side-steps orphan-row issues we saw in pgloader).
"""

import os, sqlite3, sys, re, time
import psycopg2
import psycopg2.extras
from collections import defaultdict

SQLITE = "/var/lib/autoispbilling/autoispbilling.db"
PG_DSN = os.environ["PG_DSN"]   # postgresql://user:pw@host:5432/db

BATCH = 5000

# ---- type mapping (SQLite affinity → Postgres) ---------------------------
def map_type(sqlite_type, pk_autoinc):
    t = (sqlite_type or "").upper().strip()
    if pk_autoinc:
        return "BIGSERIAL PRIMARY KEY"
    if t in ("INTEGER", "INT", "BIGINT", "TINYINT", "SMALLINT", "MEDIUMINT", "UNSIGNED BIG INT", "INT2", "INT8"):
        # smallint / tinyint kept narrow when explicit
        if t in ("TINYINT","SMALLINT","INT2"): return "SMALLINT"
        return "BIGINT"
    if t in ("REAL","DOUBLE","DOUBLE PRECISION","FLOAT"):
        return "DOUBLE PRECISION"
    if t.startswith("NUMERIC") or t.startswith("DECIMAL"):
        return "NUMERIC"
    if t in ("BOOLEAN","BOOL"):
        return "SMALLINT"
    if t in ("BLOB",):
        return "BYTEA"
    if t in ("DATETIME","TIMESTAMP","TIMESTAMPTZ"):
        return "TIMESTAMPTZ"
    if t == "DATE":
        return "DATE"
    # default TEXT for VARCHAR/CHAR/CLOB/etc. (SQLite stores them all as TEXT)
    return "TEXT"


def pg_ident(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    src = sqlite3.connect(SQLITE)
    src.row_factory = sqlite3.Row
    dst = psycopg2.connect(PG_DSN)
    dst.autocommit = False

    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]

    # Wipe destination schema to start clean.
    with dst.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
        cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    dst.commit()
    print(f"[clean] reset PG schema")

    # ---- pass 1: create all tables ---------------------------------------
    table_pk_col = {}
    for t in tables:
        cols = [dict(r) for r in src.execute(f"PRAGMA table_info({pg_ident(t)})").fetchall()]
        if not cols:
            print(f"  SKIP empty schema for {t}")
            continue
        # detect autoinc: integer pk with rowid alias
        pk_cols = [c for c in cols if c["pk"]]
        pk_col = pk_cols[0]["name"] if pk_cols else None
        col_defs = []
        for c in cols:
            is_pk_auto = (
                pk_col == c["name"] and
                (c["type"] or "").upper() in ("INTEGER","INT") and
                len(pk_cols) == 1
            )
            pg_type = map_type(c["type"], is_pk_auto)
            null_clause = "" if pg_type.endswith("PRIMARY KEY") else (" NOT NULL" if c["notnull"] else "")
            # Carry SQLite defaults only if simple literal (skip CURRENT_TIMESTAMP / function defaults).
            default_clause = ""
            dv = c["dflt_value"]
            if dv is not None and not pg_type.endswith("PRIMARY KEY"):
                if re.match(r"^-?\d+(\.\d+)?$", str(dv)):
                    default_clause = f" DEFAULT {dv}"
                elif str(dv).startswith("'") and str(dv).endswith("'"):
                    default_clause = f" DEFAULT {dv}"
            col_defs.append(f"  {pg_ident(c['name'])} {pg_type}{null_clause}{default_clause}")
        ddl = f"CREATE TABLE {pg_ident(t)} (\n" + ",\n".join(col_defs) + "\n);"
        try:
            with dst.cursor() as cur:
                cur.execute(ddl)
            dst.commit()
            table_pk_col[t] = pk_col
        except Exception as e:
            dst.rollback()
            print(f"  CREATE FAIL {t}: {e}")
            print(ddl)
            raise

    print(f"[schema] created {len(tables)} tables")

    # ---- pass 2: copy data -----------------------------------------------
    counts = {}
    t_start = time.time()
    for t in tables:
        cols = [dict(r) for r in src.execute(f"PRAGMA table_info({pg_ident(t)})").fetchall()]
        if not cols:
            counts[t] = (0, 0); continue
        col_names = [c["name"] for c in cols]
        col_idents = ", ".join(pg_ident(c) for c in col_names)
        placeholders = ", ".join(["%s"] * len(col_names))
        insert_sql = f"INSERT INTO {pg_ident(t)} ({col_idents}) VALUES ({placeholders})"
        rows_iter = src.execute(f"SELECT {', '.join(pg_ident(c) for c in col_names)} FROM {pg_ident(t)}")
        copied = 0
        batch = []
        cur = dst.cursor()
        try:
            for row in rows_iter:
                batch.append(tuple(row))
                if len(batch) >= BATCH:
                    psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=BATCH)
                    copied += len(batch); batch = []
            if batch:
                psycopg2.extras.execute_batch(cur, insert_sql, batch, page_size=BATCH)
                copied += len(batch)
            dst.commit()
        except Exception as e:
            dst.rollback()
            print(f"  COPY FAIL {t} (after {copied} rows): {e}")
            cur.close()
            counts[t] = (copied, 0)
            continue
        cur.close()
        src_n = src.execute(f"SELECT COUNT(*) FROM {pg_ident(t)}").fetchone()[0]
        counts[t] = (src_n, copied)
        if src_n != copied:
            print(f"  ! {t}: sqlite={src_n} pg={copied}")

    print(f"[data] copy done in {time.time()-t_start:.1f}s")

    # ---- pass 3: indexes (skip ones referencing 'NIL' expressions) -------
    idx_rows = src.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' "
        "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_autoindex_%' "
        "ORDER BY name").fetchall()
    idx_created = 0; idx_skipped = 0
    with dst.cursor() as cur:
        for name, ddl in idx_rows:
            if " NIL" in (ddl or "") or "(NIL)" in (ddl or ""):
                idx_skipped += 1; continue
            # rewrite IF NOT EXISTS for idempotence and lower-case keywords
            ddl_fixed = ddl.strip()
            if not ddl_fixed.upper().startswith("CREATE"):
                continue
            # Replace AUTOINCREMENT etc. nothing — these are CREATE INDEX
            try:
                cur.execute(ddl_fixed)
                idx_created += 1
            except Exception as e:
                # Some unique indexes might have orphan dup values in legacy data
                dst.rollback()
                msg = str(e).split('\n')[0]
                print(f"  IDX SKIP {name}: {msg}")
                idx_skipped += 1
                cur = dst.cursor()
    dst.commit()
    print(f"[indexes] created={idx_created} skipped={idx_skipped}")

    # ---- pass 4: reset sequences -----------------------------------------
    with dst.cursor() as cur:
        cur.execute("""
            SELECT sequence_name FROM information_schema.sequences
            WHERE sequence_schema='public'
        """)
        seqs = [r[0] for r in cur.fetchall()]
        for s in seqs:
            # bigserial sequences are named TABLE_COL_seq
            m = re.match(r"^(?P<t>.+)_(?P<c>[a-zA-Z0-9_]+)_seq$", s)
            if not m: continue
            t, c = m.group("t"), m.group("c")
            try:
                cur.execute(
                    f'SELECT setval(%s, COALESCE((SELECT MAX({pg_ident(c)}) FROM {pg_ident(t)}), 0) + 1, false)',
                    (s,))
            except Exception:
                dst.rollback(); cur = dst.cursor()
                continue
    dst.commit()
    print(f"[seq] reset {len(seqs)} sequences")

    # ---- summary ---------------------------------------------------------
    mismatches = [(t, s, p) for t, (s, p) in counts.items() if s != p]
    total_src = sum(s for s, _ in counts.values())
    total_dst = sum(p for _, p in counts.values())
    print(f"\n=== Summary: sqlite={total_src:,}  pg={total_dst:,}  mismatch_tables={len(mismatches)}")
    for t, s, p in mismatches[:20]:
        print(f"  - {t}: sqlite={s} pg={p}")

    src.close(); dst.close()


if __name__ == "__main__":
    main()
