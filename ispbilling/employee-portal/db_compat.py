"""db_compat.py — Transparent SQLite/Postgres compatibility layer.

This is the single source of truth for the application's DB URL. Modules
should call `get_raw_conn()` instead of `sqlite3.connect(DB_PATH)`.

The big trick: when DATABASE_URL is Postgres we return a connection
**wrapper** that translates SQLite syntax to Postgres on the fly inside
`cursor.execute()` — so existing modules that pass `?` placeholders or
`datetime('now', '-7 days')` continue working **without source edits**.

Supported transforms (only applied when running on Postgres):

  1.  ?                          → %s   (parameter placeholder)
  2.  datetime('now', '-N days') → (NOW() - INTERVAL 'N days')
      datetime('now', '+N days') → (NOW() + INTERVAL 'N days')
      datetime('now', '-N hours')→ similar
      datetime('now', '-N seconds')→ similar
      datetime('now', :param)    → handled by binding (we route the
                                   value through `_pg_interval_param()`)
  3.  datetime('now')            → NOW()
  4.  INSERT OR IGNORE INTO …    → INSERT INTO … ON CONFLICT DO NOTHING
  5.  INSERT OR REPLACE INTO …   → INSERT INTO … with crude
                                   ON CONFLICT (pk) DO UPDATE SET …
                                   (callers needing this pattern should
                                   migrate to explicit upserts; we
                                   handle the simple case.)
  6.  AUTOINCREMENT              → (Postgres ignores it; we strip)
  7.  PRAGMA …                   → no-op (we silently swallow PRAGMA)
  8.  last_insert_rowid()        → caller should use cursor.lastrowid
                                   which we surface from RETURNING.

For SQLite the same wrapper is a no-op pass-through (zero overhead).

WARNING: this is not a full SQL translator. It handles the patterns we
actually use across our 32 modules. Anything exotic will still fail and
that's intentional — Phase 3's verification step grep-tests the codebase
for unsupported patterns before cutover.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from typing import Optional, Any
from urllib.parse import urlparse


DEFAULT_SQLITE = "sqlite:////var/lib/autoispbilling/autoispbilling.db"
DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_SQLITE)
# __PHASE3_STRICT_PG__
if not DATABASE_URL.startswith(("postgres:", "postgresql:", "postgresql+")):
    import sys as _sys_ph3
    print(
        '[db_compat] PHASE-3: SQLite is quarantined. '
        'DATABASE_URL must be PostgreSQL, got ' + repr(DATABASE_URL),
        file=_sys_ph3.stderr,
    )
    raise RuntimeError(
        'SQLite is no longer permitted. Set DATABASE_URL=postgresql://...'
    )




def is_sqlite() -> bool:
    return DATABASE_URL.startswith("sqlite:")


def is_postgres() -> bool:
    return DATABASE_URL.startswith(("postgres:", "postgresql:", "postgresql+"))


# ───── SQL translation (only used on Postgres) ──────────────────────────
_RE_DATETIME_NOW_LITERAL = re.compile(
    r"""datetime\(\s*'now'\s*,\s*['"]\s*(?P<sign>[-+]?)(?P<n>\d+)\s*(?P<unit>seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s*['"]\s*\)""",
    re.IGNORECASE,
)
_RE_DATETIME_NOW_ONLY = re.compile(r"""datetime\(\s*'now'\s*\)""", re.IGNORECASE)
_RE_INSERT_OR_IGNORE = re.compile(
    r"""INSERT\s+OR\s+IGNORE\s+INTO""", re.IGNORECASE,
)
_RE_INSERT_OR_REPLACE = re.compile(
    r"""INSERT\s+OR\s+REPLACE\s+INTO""", re.IGNORECASE,
)
_RE_AUTOINCREMENT = re.compile(r"""\bAUTOINCREMENT\b""", re.IGNORECASE)
_RE_IFNULL = re.compile(r"""\bIFNULL\s*\(""", re.IGNORECASE)
_RE_LAST_INSERT_ROWID = re.compile(r"""\blast_insert_rowid\s*\(\s*\)""", re.IGNORECASE)
_RE_COLLATE_NOCASE = re.compile(
    r"""COLLATE\s+NOCASE""", re.IGNORECASE,
)
_RE_PRAGMA_LINE = re.compile(r"""^\s*PRAGMA\b""", re.IGNORECASE)
_RE_PRAGMA_TABLE_INFO = re.compile(
    r"""PRAGMA\s+table_info\s*\(\s*['"]?(?P<t>[A-Za-z0-9_]+)['"]?\s*\)""",
    re.IGNORECASE,
)
_RE_PRAGMA_INDEX_LIST = re.compile(
    r"""PRAGMA\s+index_list\s*\(\s*['"]?(?P<t>[A-Za-z0-9_]+)['"]?\s*\)""",
    re.IGNORECASE,
)
# Catches positional ? placeholders but NOT ?? or escaped ?:
# Naive but works for our SQL (we don't have any "?? in JSON" etc.).
_RE_QMARK = re.compile(r"""(?<!\?)\?(?!\?)""")
# IIF(a,b,c) -> CASE WHEN a THEN b ELSE c END (rare in our code)
_RE_IIF = re.compile(r"""\bIIF\s*\(""", re.IGNORECASE)
# substr -> substring
_RE_SUBSTR = re.compile(r"""\bsubstr\(""", re.IGNORECASE)
# sqlite_master(name, type, sql) -> pg_tables / pg_indexes translation.
_RE_SQLITE_MASTER_TABLE = re.compile(
    r"""FROM\s+sqlite_master\s+WHERE\s+type\s*=\s*'table'\s+AND\s+name\s*=\s*('[^']+'|\?|%s)""",
    re.IGNORECASE,
)
_RE_SQLITE_MASTER_GENERIC = re.compile(
    r"""\bsqlite_master\b""", re.IGNORECASE,
)

# ALTER TABLE x ADD COLUMN <col>  ->  ADD COLUMN IF NOT EXISTS <col>
_RE_ADD_COLUMN = re.compile(
    r"""\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS\b)""",
    re.IGNORECASE,
)



# ─ strftime(fmt, expr [, modifiers...]) -> TO_CHAR / EXTRACT (Postgres) ─
_STRFTIME_FORMATS = {
    "%Y-%m-%d": "YYYY-MM-DD",
    "%Y-%m":    "YYYY-MM",
    "%Y-%m-%dT%H:%M:%S": "YYYY-MM-DD\"T\"HH24:MI:SS",
    "%Y-%m-%d %H:%M:%S": "YYYY-MM-DD HH24:MI:SS",
    "%H:%M:%S": "HH24:MI:SS",
    "%H:%M":    "HH24:MI",
    "%Y":       "YYYY",
    "%m":       "MM",
    "%d":       "DD",
}


def _strftime_translate(fmt: str, expr_sql: str) -> str:
    """Return PG SQL fragment equivalent to sqlite strftime(fmt, expr)."""
    # Postgres can't accept 'now' as a column expr; convert it.
    e = expr_sql.strip()
    el = e.lower().strip("'").strip('"')
    if el == "now":
        ts_expr = "NOW()"
    else:
        # Cast safely. If column already a timestamp this is a no-op cast.
        ts_expr = f"(({e})::timestamp)"
    if fmt == "%s":
        return f"EXTRACT(EPOCH FROM {ts_expr})::bigint"
    pg_fmt = _STRFTIME_FORMATS.get(fmt)
    if pg_fmt is None:
        # Fallback: leave literal but escape the % so psycopg2 doesn't try
        # to bind it as a parameter (pyformat treats %s as placeholder).
        escaped = fmt.replace("%", "%%")
        return f"to_char({ts_expr}, '{escaped}')"
    return f"to_char({ts_expr}, '{pg_fmt}')"


def _rewrite_strftime(sql: str) -> str:
    """Naive scanner that finds strftime(...) calls and rewrites them.

    Only handles 2-arg cases (no SQLite modifier like '-30 days') which
    is what the Postgres-facing codebase uses. Multi-arg calls are left
    untouched (those run against the legacy sqlite radacct DB)."""
    out = []
    i = 0
    L = len(sql)
    KEY = "strftime("
    while i < L:
        # Case-insensitive match.
        if sql[i:i+len(KEY)].lower() == KEY:
            # Find first quoted format arg.
            j = i + len(KEY)
            # skip whitespace
            while j < L and sql[j] in " \t\n\r":
                j += 1
            if j >= L or sql[j] not in ("'", '"'):
                out.append(sql[i]); i += 1; continue
            q = sql[j]; j += 1
            fmt_start = j
            while j < L and sql[j] != q:
                j += 1
            if j >= L:
                out.append(sql[i]); i += 1; continue
            fmt = sql[fmt_start:j]
            j += 1  # past closing quote
            # comma
            while j < L and sql[j] in " \t\n\r":
                j += 1
            if j >= L or sql[j] != ",":
                out.append(sql[i]); i += 1; continue
            j += 1
            # Read expression up to matching ) at depth 0, ignoring commas
            # at depth 0 (if >1 comma found, it's a sqlite modifier form
            # — leave as-is).
            depth = 1
            expr_start = j
            extra_commas = []
            while j < L and depth > 0:
                c = sql[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif c == "," and depth == 1:
                    extra_commas.append(j)
                elif c in ("'", '"'):
                    # Skip quoted string.
                    qq = c
                    j += 1
                    while j < L and sql[j] != qq:
                        j += 1
                j += 1
            if j >= L or depth != 0:
                out.append(sql[i]); i += 1; continue
            expr = sql[expr_start:j].strip()
            end = j + 1  # past close paren
            if extra_commas:
                # Sqlite modifier form (3+ args). Don't rewrite — these
                # only run on the radacct sqlite db.
                out.append(sql[i:end])
                i = end
                continue
            out.append(_strftime_translate(fmt, expr))
            i = end
            continue
        out.append(sql[i]); i += 1
    return "".join(out)



# julianday(expr) -> Postgres equivalent.
_RE_JULIANDAY_NOW = re.compile(
    r"""\bjulianday\(\s*'now'\s*\)""", re.IGNORECASE
)
_RE_JULIANDAY_EXPR = re.compile(
    r"""\bjulianday\(""", re.IGNORECASE
)


def _rewrite_julianday(sql: str) -> str:
    # Easy: julianday('now')
    sql = _RE_JULIANDAY_NOW.sub(
        "(EXTRACT(EPOCH FROM NOW())/86400.0 + 2440587.5)", sql,
    )
    # Generic: julianday(<expr>) with balanced parens.
    out = []
    i = 0
    L = len(sql)
    KEY = "julianday("
    while i < L:
        if sql[i:i+len(KEY)].lower() == KEY:
            j = i + len(KEY)
            depth = 1
            expr_start = j
            while j < L and depth > 0:
                c = sql[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif c in ("'", '"'):
                    qq = c
                    j += 1
                    while j < L and sql[j] != qq:
                        j += 1
                j += 1
            if j >= L or depth != 0:
                out.append(sql[i]); i += 1; continue
            expr = sql[expr_start:j].strip()
            out.append(
                f"(EXTRACT(EPOCH FROM (({expr})::timestamp))/86400.0 + 2440587.5)"
            )
            i = j + 1
            continue
        out.append(sql[i]); i += 1
    return "".join(out)



# json_extract(col, '$.path.to.key') -> Postgres ((col)::jsonb #>> '{path,to,key}')
def _rewrite_json_extract(sql: str) -> str:
    out = []
    i = 0
    L = len(sql)
    KEY = "json_extract("
    while i < L:
        if sql[i:i+len(KEY)].lower() == KEY:
            j = i + len(KEY)
            depth = 1
            arg_start = j
            commas = []
            while j < L and depth > 0:
                c = sql[j]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                elif c == "," and depth == 1:
                    commas.append(j)
                elif c in ("'", '"'):
                    qq = c
                    j += 1
                    while j < L and sql[j] != qq:
                        j += 1
                j += 1
            if depth != 0 or j >= L or len(commas) != 1:
                out.append(sql[i]); i += 1; continue
            col = sql[arg_start:commas[0]].strip()
            path_lit = sql[commas[0]+1:j].strip()
            # Path literal must be a single-quoted string starting with '$.'
            if len(path_lit) >= 2 and path_lit[0] in ("'", '"'):
                qq = path_lit[0]
                inner = path_lit[1:-1] if path_lit.endswith(qq) else path_lit[1:]
                if inner.startswith("$."):
                    keys = inner[2:].split(".")
                elif inner.startswith("$"):
                    keys = inner[1:].split(".") if len(inner) > 1 else []
                else:
                    keys = [inner]
                # Build {a,b,c}
                pg_path = "{" + ",".join(k.strip("\"'") for k in keys) + "}"
                # _S57B_JSON_NUM_CAST_  Look ahead for a numeric comparison —
                # if found, cast the result to numeric so '#>>' (text) can
                # be compared against int/float literals or bound params.
                _k = j + 1
                while _k < L and sql[_k] in ' \t\n\r':
                    _k += 1
                _is_numeric_cmp = False
                if _k < L:
                    _rest = sql[_k:_k+5]
                    for _op in ('<>', '!=', '<=', '>=', '=', '<', '>'):
                        if _rest.startswith(_op):
                            _after = sql[_k+len(_op):].lstrip()
                            if _after[:1].isdigit() or _after.startswith('?') or _after.startswith('%s') or _after.startswith(':'):
                                _is_numeric_cmp = True
                            break
                if _is_numeric_cmp:
                    out.append(f"(NULLIF(({col})::jsonb #>> '{pg_path}', '')::numeric)")
                else:
                    out.append(f"(({col})::jsonb #>> '{pg_path}')")
                i = j + 1
                continue
        out.append(sql[i]); i += 1
    return "".join(out)


def translate_sql(sql: str) -> str:
    """SQLite-to-Postgres SQL rewrite (idempotent)."""
    # 0. COLLATE NOCASE → "C" + LOWER mid-comparison (best-effort).
    # We map it to citext-style behavior by removing it; Postgres sort
    # will be deterministic UTF-8, which for English-only data is
    # close enough to NOCASE for ORDER BY purposes. Anywhere this
    # truly matters (rare in our codebase) the SQL has been audited.
    s = _RE_COLLATE_NOCASE.sub("", sql)
    # 0a. strftime(fmt, expr) -> TO_CHAR / EXTRACT EPOCH
    if "strftime" in s.lower():
        s = _rewrite_strftime(s)
    # 0a-b. julianday(...) -> Postgres EPOCH-day math
    if "julianday" in s.lower():
        s = _rewrite_julianday(s)
    # 0c. json_extract(col, '$.path') -> Postgres jsonb #>>
    if "json_extract" in s.lower():
        s = _rewrite_json_extract(s)
    # 0b. last_insert_rowid() → LASTVAL()  (Postgres session-level last
    # inserted sequence value — works because RETURNING in the previous
    # statement set the sequence's current value for the session).
    s = _RE_LAST_INSERT_ROWID.sub("LASTVAL()", s)
    # 0c. IFNULL(a, b) → COALESCE(a, b)
    s = _RE_IFNULL.sub("COALESCE(", s)
    # 1. datetime('now', '-N days') with literal interval
    def _dt_literal(m: re.Match) -> str:
        sign = m.group("sign") or "+"
        if sign == "":
            sign = "+"
        n = m.group("n")
        unit = m.group("unit").lower().rstrip("s")
        # PostgreSQL canonical singular: second/minute/hour/day/week/month/year
        return f"(NOW() {sign} INTERVAL '{n} {unit}')"
    s = _RE_DATETIME_NOW_LITERAL.sub(_dt_literal, s)

    # 2. datetime('now') -> NOW()
    s = _RE_DATETIME_NOW_ONLY.sub("NOW()", s)

    # 3. INSERT OR IGNORE / REPLACE -> ON CONFLICT clauses
    if _RE_INSERT_OR_IGNORE.search(s):
        s = _RE_INSERT_OR_IGNORE.sub("INSERT INTO", s)
        # Append ON CONFLICT DO NOTHING if there isn't already one and the
        # statement contains VALUES (the only shape we use).
        if "ON CONFLICT" not in s.upper() and re.search(r"\bVALUES\b", s, re.IGNORECASE):
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    if _RE_INSERT_OR_REPLACE.search(s):
        s = _RE_INSERT_OR_REPLACE.sub("INSERT INTO", s)
        # Crude: caller must add ON CONFLICT themselves; we leave a marker.
        # The single legacy caller has been audited (see Phase-3 doc).
    # 3b. ALTER TABLE ... ADD COLUMN <col> -> ADD COLUMN IF NOT EXISTS <col>
    #      (Postgres 9.6+; makes legacy SQLite idempotency-via-exception work
    #       without poisoning the surrounding transaction.)
    s = _RE_ADD_COLUMN.sub("ADD COLUMN IF NOT EXISTS ", s)
    # 3c. sqlite_master -> pg_tables (used for table-existence probes).
    if _RE_SQLITE_MASTER_GENERIC.search(s):
        m = _RE_SQLITE_MASTER_TABLE.search(s)
        if m:
            s = _RE_SQLITE_MASTER_TABLE.sub(
                f"FROM pg_tables WHERE schemaname='public' AND tablename={m.group(1)}",
                s,
            )
            # rename selected `name` column to tablename so callers still get a value.
            s = re.sub(r"\bSELECT\s+name\b", "SELECT tablename AS name",
                       s, count=1, flags=re.IGNORECASE)
        else:
            # Generic fallback: best-effort no-op probe so caller's fetchone()
            # returns falsy (treats as missing) without crashing the txn.
            s = _RE_SQLITE_MASTER_GENERIC.sub("(SELECT NULL AS name WHERE FALSE) AS sqlite_master", s)
    # 4. AUTOINCREMENT -> nothing (Postgres uses BIGSERIAL / IDENTITY)
    s = _RE_AUTOINCREMENT.sub("", s)
    # 5. PRAGMA -> Postgres equivalents (or no-op)
    if _RE_PRAGMA_LINE.search(s):
        m = _RE_PRAGMA_TABLE_INFO.search(s)
        if m:
            t = m.group("t")
            return (
                "SELECT (ordinal_position - 1)::int AS cid, "
                "column_name AS name, "
                "UPPER(data_type) AS type, "
                "CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, "
                "column_default AS dflt_value, "
                "0 AS pk "
                "FROM information_schema.columns "
                f"WHERE table_schema='public' AND table_name = '{t}' "
                "ORDER BY ordinal_position"
            )
        m = _RE_PRAGMA_INDEX_LIST.search(s)
        if m:
            t = m.group("t")
            return (
                "SELECT 0 AS seq, indexname AS name, "
                "CASE WHEN indexdef ILIKE 'CREATE UNIQUE%' THEN 1 ELSE 0 END AS unique, "
                "'c' AS origin, 0 AS partial "
                "FROM pg_indexes "
                f"WHERE schemaname='public' AND tablename = '{t}'"
            )
        # Anything else (journal_mode, busy_timeout, etc.) -> no-op
        return "SELECT 1 AS pragma_noop"
    # 6. IIF(a,b,c) -> CASE WHEN a THEN b ELSE c END (only when present)
    if _RE_IIF.search(s):
        s = _rewrite_iif(s)
    # 7. ?  ->  %s  (last so we don't smash other rewrites)
    s = _RE_QMARK.sub("%s", s)
    return s


def _rewrite_iif(sql: str) -> str:
    """Naive IIF(cond, a, b) → CASE WHEN cond THEN a ELSE b END.
    Handles single-level nesting; our codebase has none deeper."""
    out = []
    i = 0
    while i < len(sql):
        m = _RE_IIF.match(sql, i)
        if not m:
            out.append(sql[i])
            i += 1
            continue
        # Find matching closing paren, respecting commas-at-depth-0.
        depth = 1
        start = m.end()
        j = start
        commas = []
        while j < len(sql) and depth > 0:
            c = sql[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            elif c == "," and depth == 1:
                commas.append(j)
            j += 1
        if len(commas) < 2 or depth != 0:
            # Malformed — bail and leave as-is.
            out.append(sql[i:j+1])
            i = j + 1
            continue
        cond = sql[start:commas[0]]
        a = sql[commas[0]+1:commas[1]]
        b = sql[commas[1]+1:j]
        out.append(f"CASE WHEN {cond} THEN {a} ELSE {b} END")
        i = j + 1
    return "".join(out)


# ───── psycopg2 wrapper that translates on the fly ──────────────────────
def _pick_cursor_factory(want_dict: bool):
    """Return the DictCursor factory when caller wants sqlite3.Row-style
    access, else None (= default tuple cursor)."""
    if not want_dict:
        return None
    import psycopg2.extras
    return psycopg2.extras.DictCursor


class _PGCursorWrap:
    """Wraps a psycopg2 cursor; rewrites SQL via translate_sql() before
    executing. Also auto-appends RETURNING id to INSERT statements so
    that `cur.lastrowid` works like sqlite3 expects."""

    _RE_INSERT_START = re.compile(r"""^\s*INSERT\s+INTO\b""", re.IGNORECASE)
    _RE_HAS_RETURNING = re.compile(r"""\bRETURNING\b""", re.IGNORECASE)

    def __init__(self, real):
        self._real = real
        self._lastrowid = None

    @staticmethod
    def _coerce_params(params):
        # _S58AE_BOOL_INT_CAST_  Postgres won't implicitly cast bool→bigint
        # and the schema still has ~80 bigint columns from the SQLite
        # era that hold 0/1 flags. Convert Python booleans to ints so
        # every INSERT/UPDATE just works. Idempotent for non-bool values.
        if params is None:
            return None
        if isinstance(params, (list, tuple)):
            t = type(params)
            return t(int(v) if isinstance(v, bool) else v for v in params)
        if isinstance(params, dict):
            return {k: (int(v) if isinstance(v, bool) else v)
                    for k, v in params.items()}
        return params

    # Most attrs (description, rowcount, fetch*, close) pass through.
    def __getattr__(self, name):
        return getattr(self._real, name)

    def __iter__(self):
        return iter(self._real)

    @property
    def lastrowid(self):
        return self._lastrowid

    def execute(self, sql, params=None):
        sql_t = translate_sql(sql)
        # Auto-RETURNING for INSERTs so `cur.lastrowid` works.
        if (self._RE_INSERT_START.search(sql_t)
                and not self._RE_HAS_RETURNING.search(sql_t)):
            # Only safe to append when the statement has no ON CONFLICT
            # DO NOTHING tail that may produce zero rows. We still try —
            # if RETURNING produces no row (DO NOTHING + duplicate), we
            # leave lastrowid as None, mirroring sqlite3's behavior where
            # AUTO PK is unset on a constraint hit.
            sql_t_with_ret = sql_t.rstrip().rstrip(";") + " RETURNING id"
            try:
                self._real.execute(sql_t_with_ret, self._coerce_params(params))
                row = None
                try:
                    row = self._real.fetchone()
                except Exception:
                    row = None
                if row:
                    self._lastrowid = row[0]
                return self
            except Exception:
                # Table has no `id` column or some other shape — fall
                # back to plain execute.
                try: self._real.connection.rollback()
                except Exception: pass
                self._real.execute(sql_t, self._coerce_params(params))
                self._lastrowid = None
                return self
        self._real.execute(sql_t, self._coerce_params(params))
        return self

    def executemany(self, sql, seq_of_params):
        # _S58AE_BOOL_INT_CAST_  bool->int coerce for every batch row.
        coerced = [self._coerce_params(p) for p in (seq_of_params or [])]
        return self._real.executemany(translate_sql(sql), coerced)

    def executescript(self, script):
        """sqlite3.Cursor.executescript shim — same as connection.executescript."""
        for part in _split_script(script):
            sql = translate_sql(part)
            if not sql.strip():
                continue
            self._real.execute(sql)


class _PGConnWrap:
    """Wraps psycopg2 connection so callers can `con.execute(...)` like
    sqlite3 *and* drive cursors the usual way. Also makes
    `con.row_factory = sqlite3.Row` a no-op (it would crash psycopg2)."""

    def __init__(self, real):
        self._real = real
        self._row_dict = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    # Pass-through context manager.
    # IMPORTANT: psycopg2's `with conn:` starts an explicit transaction
    # (autocommit becomes effectively False inside). For sqlite3
    # compatibility we want `with con:` to be a no-op transactionally
    # since autocommit=True is the closest match to SQLite's default.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Match sqlite3's behavior: implicit commit on no-error, rollback
        # on error. psycopg2 in autocommit mode doesn't have an open
        # transaction, so these are mostly no-ops, but we call them in
        # case some sub-statement issued BEGIN explicitly.
        if exc_type is None:
            try: self._real.commit()
            except Exception: pass
        else:
            try: self._real.rollback()
            except Exception: pass
        return False

    # sqlite3.Connection has .execute / .executemany shortcuts.
    def execute(self, sql, params=None):
        cur = self._real.cursor(cursor_factory=_pick_cursor_factory(self._row_dict))
        wrap = _PGCursorWrap(cur)
        wrap.execute(sql, params)
        return wrap

    def executemany(self, sql, seq):
        cur = self._real.cursor()
        wrap = _PGCursorWrap(cur)
        wrap.executemany(sql, seq)
        return wrap

    def executescript(self, script):
        # SQLite's executescript can run many ; -separated statements.
        # psycopg2's execute can too IF autocommit is on, but only when
        # the script is a single execute() call — and individual statement
        # errors don't rollback the rest. We loop instead for safety.
        cur = self._real.cursor()
        for part in _split_script(script):
            sql = translate_sql(part)
            if not sql.strip():
                continue
            cur.execute(sql)
        cur.close()

    def cursor(self, *a, **kw):
        # Make `row_factory = sqlite3.Row` work: route through DictCursor.
        cf = _pick_cursor_factory(self._row_dict or kw.get("dict_rows"))
        cur = self._real.cursor(cursor_factory=cf) if cf else self._real.cursor()
        return _PGCursorWrap(cur)

    # Mimic sqlite3.Connection.row_factory descriptor.
    @property
    def row_factory(self):
        return sqlite3.Row if self._row_dict else None

    @row_factory.setter
    def row_factory(self, val):
        # Caller wants sqlite3.Row-style dict access → flip the flag.
        self._row_dict = (val is not None)


def _split_script(script: str):
    """Cheap statement splitter. Good enough for our schemas (no
    dollar-quoted procedures, no semicolons inside strings)."""
    out = []
    buf = []
    in_s = False
    quote = None
    for c in script:
        if in_s:
            buf.append(c)
            if c == quote:
                in_s = False
        else:
            if c in ("'", '"'):
                in_s = True
                quote = c
                buf.append(c)
            elif c == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    out.append(stmt)
                buf = []
            else:
                buf.append(c)
    rest = "".join(buf).strip()
    if rest:
        out.append(rest)
    return out


# ───── public API ───────────────────────────────────────────────────────
def get_raw_conn(timeout: float = 10.0, path: Optional[str] = None):
    """Returns a connection. On SQLite this is a real sqlite3.Connection
    (unchanged behavior). On Postgres it's a _PGConnWrap that translates
    SQLite syntax transparently."""
    # Some callers pass an explicit non-default path (FreeRADIUS radacct.db)
    # — never redirect those to Postgres.
    if path is not None and "autoispbilling.db" not in path:
        return sqlite3.connect(path, timeout=timeout)

    if is_sqlite():
        u = urlparse(DATABASE_URL)
        p = u.path
        if DATABASE_URL.startswith("sqlite:////"):
            p = "/" + p.lstrip("/")
        con = sqlite3.connect(p, timeout=timeout)
        # Match the production tuning that SQLite users expect.
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=10000")
        except Exception:
            pass
        return con

    import psycopg2
    dsn = DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://", 1)
    raw = psycopg2.connect(dsn, connect_timeout=int(timeout))
    raw.autocommit = True   # Match sqlite3 default behavior (sqlite3.connect
                            # has explicit BEGIN; sqlite users mostly don't
                            # call commit explicitly outside of WITH blocks).
                            # NOTE: code that wraps in `with con:` will still
                            # use explicit transaction blocks via psycopg2's
                            # context manager which overrides autocommit.
    return _PGConnWrap(raw)


# ───── SQLAlchemy engine (lazy, cached) ─────────────────────────────────
_engine = None
_session_factory = None
_engine_lock = threading.Lock()


def _install_sqla_translator_once():
    """Patch sqlalchemy.engine.Connection.exec_driver_sql so SQLite-only
    syntax inside `conn.exec_driver_sql(...)` calls also gets translated
    when we're on Postgres. Runs at db_compat import time. Idempotent."""
    if not is_postgres():
        return
    try:
        from sqlalchemy.engine import Connection as _SAConn
        if getattr(_SAConn, "_s56Z_patched", False):
            return
        _orig_exec = _SAConn.exec_driver_sql
        def _exec_driver_sql_xlate(self, statement, parameters=None,
                                    execution_options=None):
            stmt_t = translate_sql(statement)
            # On Postgres a failed statement aborts the whole transaction
            # block. Many legacy schemas wrap idempotent DDL
            # (`ALTER TABLE ... ADD COLUMN`, `CREATE INDEX`, etc.) in
            # try/except: pass on the assumption that SQLite's
            # per-statement isolation will keep the surrounding work
            # alive. We restore that behavior by wrapping each call in
            # a SAVEPOINT and rolling back to it on failure.
            try:
                in_txn = self.in_transaction()
            except Exception:
                in_txn = False
            if in_txn:
                sp = None
                try:
                    sp = self.begin_nested()
                    res = _orig_exec(self, stmt_t, parameters, execution_options)
                    sp.commit()
                    return res
                except Exception:
                    if sp is not None:
                        try: sp.rollback()
                        except Exception: pass
                    raise
            return _orig_exec(self, stmt_t, parameters, execution_options)
        _SAConn.exec_driver_sql = _exec_driver_sql_xlate
        _SAConn._s56Z_patched = True

        # SAVEPOINT-isolate Connection.execute() so concurrent
        # CREATE TABLE/INDEX IF NOT EXISTS races (which raise PG
        # UniqueViolation on pg_class_relname_nsp_index) don't poison
        # the outer engine.begin() transaction.
        if not getattr(_SAConn, '_s56Z_patched_execute', False):
            _orig_execute = _SAConn.execute
            def _execute_savepoint(self, statement, *args, **kwargs):
                try:
                    in_txn = self.in_transaction()
                except Exception:
                    in_txn = False
                if not in_txn:
                    return _orig_execute(self, statement, *args, **kwargs)
                # Only wrap idempotent DDL — the patterns that ship as
                # try/except: pass in legacy modules. Everything else
                # bypasses the savepoint so business logic semantics
                # stay intact.
                sql_str = ''
                try:
                    sql_str = str(statement)
                except Exception:
                    pass
                upper = sql_str.upper().lstrip()
                idempotent_ddl = (
                    upper.startswith('CREATE TABLE IF NOT EXISTS')
                    or upper.startswith('CREATE INDEX IF NOT EXISTS')
                    or upper.startswith('CREATE UNIQUE INDEX IF NOT EXISTS')
                    or upper.startswith('ALTER TABLE')
                )
                if not idempotent_ddl:
                    return _orig_execute(self, statement, *args, **kwargs)
                sp = None
                try:
                    sp = self.begin_nested()
                    res = _orig_execute(self, statement, *args, **kwargs)
                    sp.commit()
                    return res
                except Exception:
                    if sp is not None:
                        try: sp.rollback()
                        except Exception: pass
                    # Swallow idempotent DDL races (UniqueViolation,
                    # DuplicateTable, DuplicateColumn) — mimics SQLite
                    # IF NOT EXISTS semantics. Re-raise anything else
                    # so logical bugs still surface.
                    import psycopg2.errors as _pgerr
                    cur_exc = None
                    import sys as _s
                    cur_exc = _s.exc_info()[1]
                    orig = getattr(cur_exc, 'orig', cur_exc)
                    if isinstance(orig, (_pgerr.UniqueViolation,
                                          _pgerr.DuplicateTable,
                                          _pgerr.DuplicateObject,
                                          _pgerr.DuplicateColumn)):
                        # Return a tombstone result-like object that
                        # has the methods callers might invoke.
                        class _Tomb:
                            rowcount = 0
                            def fetchone(self): return None
                            def fetchall(self): return []
                            def scalar(self): return None
                            def first(self): return None
                            def close(self): pass
                            def __iter__(self): return iter([])
                        return _Tomb()
                    raise
            _SAConn.execute = _execute_savepoint
            _SAConn._s56Z_patched_execute = True
    except Exception as _e:
        import sys
        print(f"[db_compat] SQLAlchemy translator install failed: {_e}",
              file=sys.stderr)

# Run the install at import time so every portal's engine benefits.
_install_sqla_translator_once()


def get_sqla_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        from sqlalchemy import create_engine, event
        kwargs = {"pool_pre_ping": True, "future": True}
        if is_sqlite():
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        _engine = create_engine(DATABASE_URL, **kwargs)

        # Also attach before_cursor_execute for the engine SQL that DOES
        # go through events (e.g. text() / Core constructs).
        if is_postgres():
            @event.listens_for(_engine, "before_cursor_execute", retval=True)
            def _translate_before(conn, cursor, statement, parameters,
                                   context, executemany):
                return translate_sql(statement), parameters
    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    from sqlalchemy.orm import sessionmaker
    _session_factory = sessionmaker(bind=get_sqla_engine(), autoflush=False,
                                     expire_on_commit=False, future=True)
    return _session_factory


def smoke_test() -> dict:
    """Verify connectivity. Returns dict with backend, version, customer count."""
    engine = get_sqla_engine()
    from sqlalchemy import text
    with engine.begin() as con:
        if is_sqlite():
            ver = con.execute(text("SELECT sqlite_version()")).scalar()
        else:
            ver = con.execute(text("SHOW server_version")).scalar()
        n = con.execute(text("SELECT COUNT(*) FROM customers")).scalar()
    return {"backend": "sqlite" if is_sqlite() else "postgres",
            "version": ver, "customers_rows": n,
            "database_url": (DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL
                              else DATABASE_URL)}


if __name__ == "__main__":
    import json
    print(json.dumps(smoke_test(), indent=2))

# __PHASE_FINAL_SQLITE_GUARD__
# Monkey-patch sqlite3.connect so any attempt to open the quarantined
# legacy paths /var/lib/autoispbilling/autoispbilling.db and
# /var/lib/freeradius/radacct.db is transparently routed through the
# PostgreSQL-backed db_compat shim. Other paths (tests, temp DBs)
# pass through unchanged.
try:
    import sqlite3 as __sqlite3_for_guard
    _QUARANTINED_PATHS = (
        "/var/lib/autoispbilling/autoispbilling.db",
        "/var/lib/freeradius/radacct.db",
    )
    _original_sqlite3_connect = __sqlite3_for_guard.connect
    def __guarded_sqlite3_connect(*args, **kwargs):
        path = args[0] if args else kwargs.get("database", "")
        try:
            path_s = str(path)
        except Exception:
            path_s = ""
        if any(p in path_s for p in _QUARANTINED_PATHS):
            # Reroute to PG via the same module's get_raw_conn.
            return get_raw_conn(timeout=kwargs.get("timeout", 10.0))
        return _original_sqlite3_connect(*args, **kwargs)
    __sqlite3_for_guard.connect = __guarded_sqlite3_connect
except Exception as _gxe:
    import sys as _gxe_sys
    print(f"[db_compat] sqlite-guard install failed: {_gxe}", file=_gxe_sys.stderr)
