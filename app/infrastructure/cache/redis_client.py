"""
infrastructure/cache/redis_client.py
──────────────────────────────────────
Redis connection for SSE pub/sub.

We use a synchronous Redis client because:
  • Celery workers are synchronous (they use asyncio.run() for async calls)
  • The SSE endpoint reads from Redis synchronously inside an async generator
    using pubsub.get_message() with a timeout — no blocking issues.

Channel naming convention:
  job_progress:{officer_id}:{job_id}

The Celery task publishes to this channel at each pipeline step.
The SSE endpoint subscribes and streams the payloads to the Android app.

Usage:
    from app.infrastructure.cache.redis_client import get_redis_client
    redis = get_redis_client()
    redis.publish("job_progress:uid123:job456", json.dumps(payload))
"""

from __future__ import annotations
from functools import lru_cache

import redis as redis_lib
from redis import Redis

from config import get_settings


@lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    """
    Returns a cached synchronous Redis client.
    One instance per process — safe for both Celery workers and FastAPI.

    Connection pool is managed by the Redis client internally.
    decode_responses=True so message payloads come back as str, not bytes.
    """
    s = get_settings()
    client: Redis = redis_lib.from_url(
        s.redis_url,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry_on_timeout=True,
        ssl_cert_reqs=None,
    )
    return client


def publish_progress(
    officer_id: str,
    job_id: str,
    payload: dict,
) -> None:
    """
    Convenience wrapper. Import and call from Celery tasks.
    Swallows errors so a Redis hiccup never crashes the pipeline.

        from app.infrastructure.cache.redis_client import publish_progress
        publish_progress(officer_id, job_id, {"status": "PARSING", "pct": 25})
    """
    import json
    try:
        redis = get_redis_client()
        channel = f"job_progress:{officer_id}:{job_id}"
        redis.publish(channel, json.dumps(payload))
    except Exception:
        pass   # Logged at call sites; don't let Redis take down the pipeline
