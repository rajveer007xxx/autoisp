"""S43ZQ — Fernet encryption helper for payment-gateway secrets."""
from __future__ import annotations
import os
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken


def _key() -> bytes:
    k = os.environ.get("PAYMENT_GW_FERNET_KEY") or ""
    if not k:
        raise RuntimeError(
            "PAYMENT_GW_FERNET_KEY missing from environment "
            "(check /etc/ispbilling.env)"
        )
    return k.encode("utf-8")


def encrypt(plaintext: Optional[str]) -> Optional[str]:
    """Returns base64 token, or None if input is None/empty."""
    if not plaintext:
        return None
    return Fernet(_key()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: Optional[str]) -> Optional[str]:
    """Returns plaintext, or None on bad/missing token (safe-fail)."""
    if not token:
        return None
    try:
        return Fernet(_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, Exception):
        return None


def mask(plaintext: Optional[str], keep_last: int = 4) -> str:
    """Helper for UI: '••••••••••rwxy' style masking."""
    if not plaintext:
        return ""
    s = str(plaintext)
    if len(s) <= keep_last:
        return "•" * len(s)
    return "•" * (len(s) - keep_last) + s[-keep_last:]
