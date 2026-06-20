"""
__PHASE0_BUSY_TIMEOUT__

Bumps SQLite default connection lock timeout from 0 ms to 5000 ms.
After `install()` is called once at process startup, every subsequent
`sqlite3.connect(...)` returns a connection with `PRAGMA busy_timeout=5000`
already set.  No semantic change to queries — only buys 5 s of waiting
before a contended write raises "database is locked".

Idempotent: install() is safe to call any number of times.
"""
import sqlite3 as _sqlite3

_PATCHED_FLAG = "__phase0_busy_timeout_installed__"


def install(timeout_ms: int = 5000) -> None:
    if getattr(_sqlite3.connect, _PATCHED_FLAG, False):
        return  # already installed
    _orig = _sqlite3.connect

    def _wrapped(*args, **kwargs):
        # Default the Python-side wait timeout to 30s too — independent of pragma.
        kwargs.setdefault("timeout", 30.0)
        conn = _orig(*args, **kwargs)
        try:
            conn.execute(f"PRAGMA busy_timeout = {int(timeout_ms)}")
        except Exception:
            pass
        return conn

    setattr(_wrapped, _PATCHED_FLAG, True)
    _sqlite3.connect = _wrapped
