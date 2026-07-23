"""Safe Redis distributed locks (SET NX EX + token + Lua release).

Per Redis lock guidance and redis-py ``Lock``: store a unique owner token and
only delete when the token still matches, so a slow holder cannot release a
lock another worker acquired after TTL expiry.

Refs:
- https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
- https://github.com/redis/redis-py/blob/master/redis/lock.py
"""

from __future__ import annotations

import asyncio
import uuid

from redis.asyncio import Redis

# Atomic compare-and-delete (owner-safe release).
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""

# Drop a key only when its value still matches (e.g. release in-flight dedup).
_COMPARE_DELETE_SCRIPT = _RELEASE_LOCK_SCRIPT


async def acquire_lock(
    redis: Redis,
    key: str,
    *,
    ttl_seconds: int,
) -> str | None:
    """Acquire ``key`` with NX+TTL. Returns owner token, or None if held."""
    token = uuid.uuid4().hex
    acquired = await redis.set(key, token, nx=True, ex=ttl_seconds)
    if acquired:
        return token
    return None


async def acquire_lock_with_retry(
    redis: Redis,
    key: str,
    *,
    ttl_seconds: int,
    attempts: int,
    delay_seconds: float,
) -> str | None:
    """Retry ``acquire_lock`` until success or attempts exhausted."""
    for _ in range(attempts):
        token = await acquire_lock(redis, key, ttl_seconds=ttl_seconds)
        if token is not None:
            return token
        await asyncio.sleep(delay_seconds)
    return None


async def release_lock(redis: Redis, key: str, token: str) -> bool:
    """Release only if ``token`` still owns ``key``. Returns True if deleted."""
    deleted = await redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
    return bool(deleted)


async def compare_delete(redis: Redis, key: str, expected_value: str) -> bool:
    """Delete ``key`` only when its current value equals ``expected_value``."""
    deleted = await redis.eval(_COMPARE_DELETE_SCRIPT, 1, key, expected_value)
    return bool(deleted)
