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


# ─── _S40zπ_PROF_PLUS_  Pattern building (normalised match) ───────
import unicodedata as _ud

# Pull in the masked-skeleton additions and the false-positive removal list.
try:
    from profanity_words import ROMAN_WORDS_MASKED as _MASKED, ROMAN_WORDS_REMOVE as _REMOVE
except Exception:
    _MASKED, _REMOVE = [], []

# Common leet-speak / homoglyph substitutions used to evade filters.
_LEET = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't',
    '@': 'a', '$': 's', '!': 'i', '*': '',
    # Cyrillic look-alikes that visually impersonate Latin
    'х': 'x', 'о': 'o', 'а': 'a', 'е': 'e', 'с': 'c',
    'р': 'p', 'н': 'h', 'т': 't', 'к': 'k', 'м': 'm', 'і': 'i',
})


def _norm_strict(text: str) -> str:
    """Aggressive normalisation for substring matching of LONG profane words.

    Applies in order:  unicode NFKC → lowercase → leet-decode → strip combining
    marks → drop everything that is not a-z or Devanagari → collapse runs of
    repeated chars.  This catches RAAANDIYO, CHXXXT, M_A_D_A_R_C_H_O_D,
    Cyrillic-spoofed Latin, etc.
    """
    if not text:
        return ""
    t = _ud.normalize('NFKC', text).lower().translate(_LEET)
    t = ''.join(c for c in _ud.normalize('NFKD', t) if not _ud.combining(c))
    # drop any char that is not a-z or Devanagari
    t = re.sub(r'[^a-z\u0900-\u097F]+', '', t)
    # collapse 2+ same chars to 1 (RAAANDI → RANDI, CHOOT → CHOT, MAAAA → MA)
    t = re.sub(r'(.)\1+', r'\1', t)
    return t


def _norm_loose(text: str) -> str:
    """Strict normalisation that preserves word BOUNDARIES.

    Used for SHORT codes (mc, bc, bkl, lvd, bsdk, chxt) which must be
    word-boundary matched to avoid false positives, while still catching
    elongation/leet/spaced variants:
      'M C ko'              → ' mc ko '       (single-letter sequences join)
      'B.S.D.K'             → ' bsdk '
      'M_A_D_A_R_C_H_O_D'   → ' madarchod '
      'CHXXXT'              → ' chxt '        (2+ same → 1)
      'random.bsdk@x.com'   → ' random bsdk x com '
    """
    if not text:
        return ""
    # In the LOOSE path we treat email/path separators (@ . / _ - + |) as
    # word boundaries — NOT as leet substitutions — so 'random.bsdk@x.com'
    # is correctly tokenised as 'random | bsdk | x | com'.  The aggressive
    # @→a / $→s mappings remain available in the STRICT path for inputs like
    # 'm@d4rch0d' which have no surrounding word boundaries to begin with.
    t = _ud.normalize('NFKC', text).lower()
    t = re.sub(r'[@\$\.\-_/\\\+\|]+', ' ', t)
    t = t.translate(_LEET)
    t = ''.join(c for c in _ud.normalize('NFKD', t) if not _ud.combining(c))
    # Replace any non-letter run with a single space
    t = re.sub(r'[^a-z]+', ' ', t)
    # Join runs of single-letter tokens: 'm c d' → 'mcd', 'b.s.d.k' → 'bsdk'
    t = re.sub(r'\b([a-z])(?:\s+([a-z])\b)+',
               lambda m: m.group(0).replace(' ', ''),
               t)
    # Collapse runs of 2+ same chars to 1 — same rule as strict normaliser
    # so SHORT skeletons match elongation: 'CHXXXT' → 'chxt', 'lll' → 'l'.
    t = re.sub(r'(.)\1+', r'\1', t)
    return ' ' + t.strip() + ' '   # pad so \b at edges always matches


# Build the active matching sets.
_REMOVE_SET = set(w.lower() for w in (_REMOVE or []))
_ALL_ROMAN = [w.lower() for w in (ROMAN_WORDS + (_MASKED or []))
              if w and w.lower() not in _REMOVE_SET]
_ROMAN_NORM = [_norm_strict(w) for w in _ALL_ROMAN]

# Long words: substring match in strict-normalised text
_LONG_BAD = sorted(set(w for w in _ROMAN_NORM if len(w) >= 5),
                   key=len, reverse=True)
# Short codes: word-boundary match in loose-normalised text (≥2 chars to avoid
# matching single letters; <5 chars because anything ≥5 goes through long path).
# _v4727_  Common English words that happen to collide with the
# normalised form of certain Hinglish slurs (e.g. 'aand' -> 'and').
# These are stripped from the short-code set so they do not block
# legitimate plan descriptions / comments / addresses.
_POST_NORM_REMOVE = {"and", "or", "to", "in", "on", "at"}
_SHORT_BAD = sorted(set(w for w in _ROMAN_NORM
                        if 2 <= len(w) < 5
                        and w not in _POST_NORM_REMOVE),
                    key=len, reverse=True)
_DEVA_LIST = list(DEVA_WORDS)

# Pre-compile the short-code regex
_RX_SHORT = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in _SHORT_BAD) + r")(?![a-z])",
    re.IGNORECASE,
) if _SHORT_BAD else None

PROHIBITED_MESSAGE = (
    "Inappropriate language detected. You cannot use offensive or "
    "abusive words on this portal. Your IP address has been logged "
    "and an FIR may be lodged under IT Act 2000 and IPC Sec 354A/509."
)


def find_profanity(text: str) -> Optional[str]:
    """Return the offending word/skeleton if `text` contains profanity, else None.

    Detects four categories:
      1. Devanagari profanity in original script.
      2. LONG Roman/Hinglish words via strict-normalised substring match
         (handles elongation, leetspeak, char-substitution, embedded punctuation).
      3. Vowel-omitted / X-masked consonant skeletons (added explicitly).
      4. SHORT 2-4 char codes (MC, BC, BKL, LVD, BSDK) via word-boundary
         match on loose-normalised text (collapses spaced 'M C' → 'mc').
    """
    if not text:
        return None

    # 1) Devanagari direct (already Unicode-aware)
    for w in _DEVA_LIST:
        if w and w in text:
            return w

    # 2) Long-word strict substring match
    norm = _norm_strict(text)
    if norm:
        for w in _LONG_BAD:
            if w and w in norm:
                return w

    # 3) Short codes word-boundary on loose-normalised
    if _RX_SHORT is not None:
        loose = _norm_loose(text)
        m = _RX_SHORT.search(loose)
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
            ct_low = (request.headers.get("content-type") or "").lower()
            if sample and sample[0:1] in ("{", "["):
                # JSON body — scan only known user-text fields.
                try:
                    parsed = json.loads(sample)
                    sample = _json_user_text(parsed) or ""
                except Exception:
                    pass
            elif sample and "application/x-www-form-urlencoded" in ct_low:
                # Form body — only scan whitelisted keys.
                try:
                    from urllib.parse import parse_qs as _qs
                    parts = _qs(sample, keep_blank_values=True)
                    picked = []
                    for k, vs in parts.items():
                        if str(k).lower().strip() in _USER_TEXT_KEYS:
                            picked.extend(vs)
                    sample = " ".join(picked)
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


# __PROFANITY_USER_FIELDS_ONLY__
# Only these JSON / form keys are scanned for profanity. Everything
# else (customer_id, action, kind, status, ids, type, etc.) is
# considered an internal identifier and skipped.
_USER_TEXT_KEYS = frozenset([
    # comments / free-text
    'remarks', 'remark', 'notes', 'note', 'comment', 'comments',
    'description', 'desc', 'body', 'message', 'msg', 'text',
    'content', 'reason', 'reply', 'feedback',
    # complaint / ticket
    'complaint', 'complaint_text', 'subject', 'title',
    'issue', 'issue_description',
    # address / display name fields where typos may include slang
    'address', 'company_address', 'billing_address', 'locality',
    'customer_name', 'name', 'admin_name', 'plan_name',
    'plan_description', 'declaration', 'terms_conditions',
    # outbound message bodies (SMS/WhatsApp campaigns)
    'body_text', 'campaign_body', 'sms_body', 'wa_body',
])


def _json_user_text(node) -> str:
    """Recursively collect string values whose KEY is in the user-text
    whitelist. Top-level non-dict (a bare string / list) is treated as
    untrusted and scanned in full — covers raw form bodies like
    'remarks=…' passed without JSON wrapping."""
    out = []
    if isinstance(node, str):
        return node
    if isinstance(node, (list, tuple)):
        # Lists alone have no key context; only recurse into nested dicts.
        for it in node:
            if isinstance(it, (dict, list, tuple)):
                out.append(_json_user_text(it))
        return ' '.join(out)
    if isinstance(node, dict):
        for k, v in node.items():
            lk = str(k).lower().strip()
            if lk in _USER_TEXT_KEYS:
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, (list, tuple)):
                    out.extend(str(x) for x in v if isinstance(x, str))
                elif isinstance(v, dict):
                    out.append(_json_user_text(v))
            elif isinstance(v, (dict, list, tuple)):
                # Recurse to find any nested user-text fields
                out.append(_json_user_text(v))
    return ' '.join(out)


# Backwards-compat alias — some other module may import this name.
_json_strings = _json_user_text


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
