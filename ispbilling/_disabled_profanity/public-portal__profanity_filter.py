"""
_S40zμ_  Profanity-blocking FastAPI middleware (server-side defense)
─────────────────────────────────────────────────────────────────────
Scans every POST/PUT/PATCH body for the words in profanity_words.py.
On match returns 400 with a JSON envelope the JS error handler displays.
GET / static / OPTIONS / multipart-file requests pass through untouched.

_S40zν_  Every block also writes a row into the profanity_violations
SQLite table for the Superadmin "Violations" audit trail.
"""
from __future__ import annotations
import re
import json
import logging
from typing import Optional
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse, HTMLResponse

from profanity_words import ROMAN_WORDS, DEVA_WORDS

log = logging.getLogger(__name__)


# ─── _S40zν_  Audit trail ─────────────────────────────────────────
def _ensure_violation_schema() -> None:
    try:
        from database import engine as _eng
        with _eng.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE IF NOT EXISTS profanity_violations (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    actor_type      TEXT,
                    actor_id        TEXT,
                    actor_name      TEXT,
                    company_id      TEXT,
                    ip_address      TEXT,
                    user_agent      TEXT,
                    method          TEXT,
                    request_path    TEXT,
                    referer         TEXT,
                    offending_word  TEXT NOT NULL,
                    snippet         TEXT
                )
            """)
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pv_occurred_at "
                "ON profanity_violations(occurred_at DESC)")
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_pv_actor "
                "ON profanity_violations(actor_type, actor_id)")
    except Exception as e:
        log.warning("profanity_violations schema init failed: %s", e)


_ensure_violation_schema()


def _decode_session_cookie(request: Request) -> dict:
    """
    _S40zν.b_  Read the Starlette signed session cookie ourselves.
    request.session isn't available because SessionMiddleware sits
    inside our profanity middleware in the ASGI stack.
    Cookie format: <base64(json)>.<timestamp>.<signature> — we only
    care about the data chunk; integrity is already enforced by
    the inner SessionMiddleware before any handler ran.
    """
    try:
        cookie = request.cookies.get("session")
        if not cookie:
            return {}
        import base64, json as _json
        data_part = cookie.split(".", 1)[0]
        # Pad base64 (urlsafe variant)
        pad = "=" * (-len(data_part) % 4)
        raw = base64.urlsafe_b64decode((data_part + pad).encode())
        payload = _json.loads(raw.decode("utf-8", errors="ignore"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _record_violation(request: Request, word: str, snippet: str) -> None:
    """Best-effort write — never propagate errors."""
    try:
        from database import engine as _eng
        # Try the safe scope-side first; fall back to cookie decode.
        sess = (request.scope.get("session")
                if hasattr(request, "scope") else None) or {}
        if not sess:
            sess = _decode_session_cookie(request)
        ip = (request.client.host if request.client else None) or ""
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip() or ip
        with _eng.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO profanity_violations "
                "(actor_type, actor_id, actor_name, company_id, ip_address, "
                " user_agent, method, request_path, referer, "
                " offending_word, snippet) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sess.get("user_type") or "anonymous",
                    sess.get("user_id"),
                    sess.get("user_name"),
                    sess.get("company_id"),
                    ip,
                    (request.headers.get("user-agent") or "")[:300],
                    request.method,
                    request.url.path[:300],
                    (request.headers.get("referer") or "")[:300],
                    (word or "")[:80],
                    (snippet or "")[:500],
                ),
            )
    except Exception as e:
        log.warning("_record_violation failed: %r", e)


# ─── Pattern building ─────────────────────────────────────────────
_RX_ROMAN = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in ROMAN_WORDS) + r")\b",
    re.IGNORECASE,
)
_RX_DEVA = re.compile(
    r"(?<![\u0900-\u097F])(?:"
    + "|".join(re.escape(w) for w in DEVA_WORDS)
    + r")(?![\u0900-\u097F])"
)

PROHIBITED_MESSAGE = (
    "Inappropriate language detected. You cannot use offensive or "
    "abusive words on this portal. Your IP address has been logged "
    "and an FIR may be lodged under IT Act 2000 and IPC Sec 354A/509."
)


def find_profanity(text: str) -> Optional[str]:
    if not text:
        return None
    m = _RX_ROMAN.search(text)
    if m:
        return m.group(0)
    m = _RX_DEVA.search(text)
    if m:
        return m.group(0)
    return None


_SKIP_PATH_PREFIXES = (
    "/static/", "/uploads/", "/apk/", "/api/manual-invoice/",
    "/api/admin/olt/stream",
)


def _should_skip(request: Request) -> bool:
    if request.method.upper() not in ("POST", "PUT", "PATCH"):
        return True
    path = request.url.path or ""
    for p in _SKIP_PATH_PREFIXES:
        if path.startswith(p):
            return True
    ct = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in ct:
        return True
    return False


def _snippet_around(text: str, word: str, ctx: int = 80) -> str:
    """Return ~ctx chars around the offending word for the audit log."""
    if not text or not word:
        return ""
    try:
        idx = text.lower().find(word.lower())
        if idx < 0:
            return text[:ctx*2]
        s = max(0, idx - ctx); e = min(len(text), idx + len(word) + ctx)
        return text[s:e]
    except Exception:
        return text[:ctx*2]


def install_profanity_middleware(app) -> None:

    @app.middleware("http")
    async def _profanity_guard(request: Request, call_next):
        try:
            if _should_skip(request):
                return await call_next(request)
            body = await request.body()
            text: Optional[str] = None
            if body:
                try:
                    text = body.decode("utf-8", errors="ignore")
                except Exception:
                    text = None
            sample = text or ""
            if sample and sample[0:1] in ("{", "["):
                try:
                    parsed = json.loads(sample)
                    sample = _json_strings(parsed)
                except Exception:
                    pass
            hit = find_profanity(sample) if sample else None
            if hit:
                client_ip = (request.client.host
                              if request.client else "unknown")
                log.warning("profanity blocked: ip=%s path=%s word=%r",
                            client_ip, request.url.path, hit)
                _record_violation(
                    request, hit, _snippet_around(sample, hit))
                payload = {
                    "success": False,
                    "blocked": True,
                    "message": PROHIBITED_MESSAGE,
                    "offending_word": hit,
                }
                return JSONResponse(payload, status_code=400)

            async def receive():
                return {"type": "http.request", "body": body,
                        "more_body": False}

            request._receive = receive
        except Exception as e:
            log.debug("profanity middleware error (passing through): %s", e)
        return await call_next(request)


def _json_strings(node) -> str:
    out = []
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            out.append(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return " ".join(out)


# ─── _S40zν_  Read API for the Superadmin Violations page ─────────
def list_violations(limit: int = 200,
                    offset: int = 0,
                    role: Optional[str] = None,
                    q: Optional[str] = None) -> list:
    """Return rows ordered by most-recent first, optional role + q filters."""
    rows: list = []
    try:
        from database import engine as _eng
        sql = ("SELECT id, occurred_at, actor_type, actor_id, actor_name, "
               "       company_id, ip_address, user_agent, method, "
               "       request_path, referer, offending_word, snippet "
               "FROM profanity_violations WHERE 1=1 ")
        params: list = []
        if role:
            sql += " AND actor_type = ? "
            params.append(role)
        if q:
            sql += (" AND (offending_word LIKE ? OR snippet LIKE ? "
                    "      OR ip_address LIKE ? OR actor_id LIKE ?) ")
            like = f"%{q}%"
            params += [like, like, like, like]
        sql += " ORDER BY occurred_at DESC LIMIT ? OFFSET ?"
        params += [int(limit), int(offset)]
        with _eng.begin() as conn:
            for r in conn.exec_driver_sql(sql, tuple(params)).fetchall():
                rows.append({
                    "id": r[0], "occurred_at": r[1],
                    "actor_type": r[2], "actor_id": r[3], "actor_name": r[4],
                    "company_id": r[5], "ip_address": r[6],
                    "user_agent": r[7], "method": r[8],
                    "request_path": r[9], "referer": r[10],
                    "offending_word": r[11], "snippet": r[12],
                })
    except Exception as e:
        log.warning("list_violations failed: %s", e)
    return rows


def violation_summary() -> dict:
    """Counts per role + total + last-24h + last-7d for the dashboard tile."""
    out = {"total": 0, "last24h": 0, "last7d": 0,
           "by_role": {}, "top_words": [], "top_ips": []}
    try:
        from database import engine as _eng
        with _eng.begin() as conn:
            out["total"] = int(conn.exec_driver_sql(
                "SELECT COUNT(*) FROM profanity_violations").fetchone()[0])
            out["last24h"] = int(conn.exec_driver_sql(
                "SELECT COUNT(*) FROM profanity_violations "
                "WHERE occurred_at >= datetime('now','-24 hours')"
            ).fetchone()[0])
            out["last7d"] = int(conn.exec_driver_sql(
                "SELECT COUNT(*) FROM profanity_violations "
                "WHERE occurred_at >= datetime('now','-7 days')"
            ).fetchone()[0])
            for r in conn.exec_driver_sql(
                "SELECT COALESCE(actor_type,'anonymous'), COUNT(*) "
                "FROM profanity_violations GROUP BY actor_type"
            ).fetchall():
                out["by_role"][r[0]] = int(r[1])
            out["top_words"] = [
                {"word": r[0], "count": int(r[1])}
                for r in conn.exec_driver_sql(
                    "SELECT offending_word, COUNT(*) c "
                    "FROM profanity_violations GROUP BY offending_word "
                    "ORDER BY c DESC LIMIT 10"
                ).fetchall()
            ]
            out["top_ips"] = [
                {"ip": r[0], "count": int(r[1])}
                for r in conn.exec_driver_sql(
                    "SELECT ip_address, COUNT(*) c "
                    "FROM profanity_violations "
                    "WHERE ip_address IS NOT NULL AND ip_address != '' "
                    "GROUP BY ip_address ORDER BY c DESC LIMIT 10"
                ).fetchall()
            ]
    except Exception as e:
        log.warning("violation_summary failed: %s", e)
    return out
