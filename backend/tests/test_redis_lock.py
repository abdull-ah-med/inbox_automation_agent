"""Unit tests for Redis lock helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.redis_lock import acquire_lock, compare_delete, release_lock


@pytest.mark.asyncio
async def test_acquire_lock_returns_token_on_success() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    token = await acquire_lock(redis, "lock:test", ttl_seconds=30)
    assert token is not None
    assert len(token) == 32
    redis.set.assert_awaited_once()
    assert redis.set.await_args.kwargs["nx"] is True
    assert redis.set.await_args.kwargs["ex"] == 30
    assert redis.set.await_args.args[1] == token


@pytest.mark.asyncio
async def test_acquire_lock_returns_none_when_held() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=False)
    assert await acquire_lock(redis, "lock:test", ttl_seconds=30) is None


@pytest.mark.asyncio
async def test_release_lock_uses_compare_and_delete() -> None:
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=1)
    assert await release_lock(redis, "lock:test", "abc123") is True
    redis.eval.assert_awaited_once()
    assert redis.eval.await_args.args[2] == "lock:test"
    assert redis.eval.await_args.args[3] == "abc123"


@pytest.mark.asyncio
async def test_compare_delete_processing_value() -> None:
    redis = AsyncMock()
    redis.eval = AsyncMock(return_value=0)
    assert await compare_delete(redis, "dedup:m:1", "processing") is False


@pytest.mark.asyncio
async def test_release_triage_lock_requires_owner_token() -> None:
    """Owner-safe release only — never unconditional DEL (Redis lock docs)."""
    from app.services import ingestion_service

    redis = AsyncMock()
    with patch(
        "app.core.redis_lock.release_lock",
        new=AsyncMock(return_value=True),
    ) as release:
        await ingestion_service.release_triage_lock(
            redis, "box@example.com", "msg-1", "owner-token"
        )
    release.assert_awaited_once()
    assert release.await_args.args[1].endswith("msg-1")
    assert release.await_args.args[2] == "owner-token"
    redis.delete.assert_not_called()
