"""sa_backups.py — Phase-4: backup is now a pg_dump pass-through.

The legacy code opened the SQLite app DB via sqlite3.connect() and called
src.backup(dst). Now we delegate to pg_dump which produces a portable
custom-format dump that can be restored with `pg_restore`.
"""
# __PHASE4_PG_DUMP__
from __future__ import annotations
import os
import subprocess
import tempfile
from datetime import datetime


def make_backup(out_path: str | None = None) -> str:
    """Produce a PG custom-format backup; return the file path."""
    if out_path is None:
        out_path = tempfile.mktemp(
            prefix=f"autoispbilling-pgdump-{datetime.now():%Y%m%d-%H%M%S}-",
            suffix=".dump",
        )
    subprocess.check_call(
        ["sudo", "-u", "postgres", "pg_dump", "--format=custom",
         "--file=" + out_path, "autoispbilling"]
    )
    return out_path
