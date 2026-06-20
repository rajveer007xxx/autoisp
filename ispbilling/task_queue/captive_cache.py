"""
Phase 1 captive-portal read-through cache.

Public API:
    get_customer_status_cached(customer_id, company_id, fetch_fn, ttl=30)

If USE_REDIS_CACHE != '1' in env, fetch_fn() is called directly — i.e.
cache layer is transparent and OFF by default. Operator flips
USE_REDIS_CACHE=1 to engage. Falls back to direct fetch on any Redis error.
"""
import json
import os
import time

_REDIS = None
_ENABLED = os.environ.get("USE_REDIS_CACHE", "0") == "1"


def _client():
    global _REDIS
    if _REDIS is None:
        import redis
        _REDIS = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
            socket_timeout=0.05,          # 50 ms — fail-fast
            socket_connect_timeout=0.05,
            decode_responses=True,
        )
    return _REDIS


def get_customer_status_cached(customer_id, company_id, fetch_fn, ttl: int = 30):
    if not _ENABLED:
        return fetch_fn()
    key = f"cust:{company_id}:{customer_id}:status"
    try:
        c = _client()
        cached = c.get(key)
        if cached:
            return json.loads(cached)
        val = fetch_fn()
        try:
            c.setex(key, ttl, json.dumps(val))
        except Exception:
            pass
        return val
    except Exception:
        # Redis down → always fall back to direct fetch.
        return fetch_fn()


def invalidate_customer_status(customer_id, company_id):
    if not _ENABLED:
        return
    try:
        _client().delete(f"cust:{company_id}:{customer_id}:status")
    except Exception:
        pass
