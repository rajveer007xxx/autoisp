"""
Phase 1 broker — Redis-backed Dramatiq broker.

Set REDIS_URL env var to override; default = localhost.
All queues are PREFIXED with 'isp.' so they never collide with other apps
sharing the same Redis instance later.
"""
import os
import dramatiq
from dramatiq.brokers.redis import RedisBroker

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
NAMESPACE = "isp"

# Shared instance — import this from worker.py AND from any code that
# wants to enqueue tasks.
broker = RedisBroker(url=REDIS_URL, namespace=NAMESPACE)
dramatiq.set_broker(broker)
