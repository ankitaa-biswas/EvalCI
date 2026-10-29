# evalci/core/cache.py
# Redis cache layer for evaluation scores and pub/sub for SSE streaming.

import json
import os
from typing import Any

import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", str(86400 * 7)))  # 7 days
PUBSUB_CHANNEL_PREFIX = "evalci:run:"

def get_redis_client() -> aioredis.Redis:
    """Create a caller-owned client. Close it within the same event loop.

    Celery drives a new event loop per task, so sharing a module-level async
    connection pool across tasks is unsafe.
    """
    return aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True,
                            socket_connect_timeout=3, socket_timeout=3)


def make_cache_key(question_id: str, commit_sha: str) -> str:
    """Compute a deterministic Redis cache key for a question + commit pair."""
    return f"evalci:score:{question_id}:{commit_sha}"


async def get_cached_score(
    redis: aioredis.Redis,
    question_id: str,
    commit_sha: str,
) -> dict | None:
    """
    Fetch a previously cached Ragas score from Redis.
    Returns None if the key does not exist or has expired.
    """
    key = make_cache_key(question_id, commit_sha)
    raw = await redis.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def set_cached_score(
    redis: aioredis.Redis,
    question_id: str,
    commit_sha: str,
    score: dict,
) -> None:
    """Persist a Ragas score dict to Redis with a TTL."""
    key = make_cache_key(question_id, commit_sha)
    await redis.setex(key, CACHE_TTL_SECONDS, json.dumps(score))


async def publish_score_event(
    redis: aioredis.Redis,
    run_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """
    Publish a scored-question event to the Redis pub/sub channel for a run.
    Channel format: evalci:run:{run_id}
    """
    channel = f"{PUBSUB_CHANNEL_PREFIX}{run_id}"
    message = json.dumps({"type": event_type, "data": payload})
    await redis.publish(channel, message)


async def invalidate_run_cache(
    redis: aioredis.Redis,
    commit_sha: str,
) -> int:
    """
    Delete all cached scores associated with a specific commit SHA.
    Uses SCAN to avoid blocking on large keyspaces.
    """
    pattern = f"evalci:score:*:{commit_sha}"
    deleted = 0
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor, match=pattern, count=100)
        if keys:
            deleted += await redis.delete(*keys)
        if cursor == 0:
            break
    return deleted
