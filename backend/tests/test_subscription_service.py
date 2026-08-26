"""Unit tests for Graph subscription reconcile / renew / lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ResponseError

from app.core.config import Settings
from app.models.schemas.graph import GraphNotificationItemSchema, GraphSubscriptionSchema
from app.services import subscription_service


def _settings() -> Settings:
    return Settings(
        graph_notification_url="https://example.com/webhooks/graph/notifications",
        graph_lifecycle_url="https://example.com/webhooks/graph/lifecycle",
        graph_webhook_client_state="secret",
        target_mailboxes="user@example.com",
    )


def _sub(
    *,
    sub_id: str = "sub-1",
    expiration: datetime | None = None,
    lifecycle: str | None = "https://example.com/webhooks/graph/lifecycle",
) -> GraphSubscriptionSchema:
    return GraphSubscriptionSchema.model_validate(
        {
            "id": sub_id,
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
            "changeType": "created",
            "notificationUrl": "https://example.com/webhooks/graph/notifications",
            "lifecycleNotificationUrl": lifecycle,
            "expirationDateTime": (expiration or datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "clientState": "secret",
        }
    )


@pytest.mark.asyncio
async def test_subscription_match_rejects_substring_mailbox() -> None:
    """``test@`` must not match a subscription for ``contest@``."""
    sub = GraphSubscriptionSchema.model_validate(
        {
            "id": "sub-1",
            "resource": "users/contest@company.com/mailFolders('inbox')/messages",
            "changeType": "created",
            "notificationUrl": "https://example.com/webhooks/graph/notifications",
            "lifecycleNotificationUrl": "https://example.com/webhooks/graph/lifecycle",
            "expirationDateTime": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "clientState": "secret",
        }
    )
    assert subscription_service._subscription_matches_mailbox(sub, "contest@company.com")
    assert not subscription_service._subscription_matches_mailbox(sub, "test@company.com")


def test_subscription_match_accepts_aad_upn_prefixed_resource() -> None:
    mailbox = "3f8c2a71-6d45-4e9b-a237-81c5f0d762ae@contoso.com"
    sub = GraphSubscriptionSchema.model_validate(
        {
            "id": "sub-1",
            "resource": f"users/AAD-UPN:{mailbox}/mailFolders('inbox')/messages",
            "changeType": "created",
            "notificationUrl": "https://example.com/webhooks/graph/notifications",
            "lifecycleNotificationUrl": "https://example.com/webhooks/graph/lifecycle",
            "expirationDateTime": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "clientState": "secret",
        }
    )
    assert subscription_service._subscription_matches_mailbox(sub, mailbox)


@pytest.mark.asyncio
async def test_reconcile_creates_when_missing() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    redis.eval = AsyncMock(return_value=1)
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[])
    created = _sub()
    graph_client.create_subscription = AsyncMock(return_value=created)

    result = await subscription_service.reconcile_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is not None
    assert result.id == "sub-1"
    graph_client.create_subscription.assert_awaited_once()
    # validation window begin/end via Redis Lua + subscription cache HSET
    assert redis.eval.await_count >= 2
    assert redis.hset.await_count >= 1


@pytest.mark.asyncio
async def test_reconcile_recreates_when_lifecycle_url_missing() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    redis.eval = AsyncMock(return_value=1)
    stale = _sub(lifecycle=None)
    created = _sub(sub_id="sub-2")
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[stale])
    graph_client.delete_subscription = AsyncMock()
    graph_client.create_subscription = AsyncMock(return_value=created)

    result = await subscription_service.reconcile_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is not None
    assert result.id == "sub-2"
    graph_client.delete_subscription.assert_awaited_once_with("sub-1")
    graph_client.create_subscription.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_skips_when_urls_missing() -> None:
    settings = Settings(target_mailboxes="user@example.com")
    redis = AsyncMock()
    graph_client = MagicMock()

    result = await subscription_service.reconcile_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is None
    graph_client.list_subscriptions.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_keeps_healthy_subscription() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.hset = AsyncMock(return_value=1)
    healthy = _sub()
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[healthy])
    graph_client.create_subscription = AsyncMock()
    graph_client.delete_subscription = AsyncMock()

    result = await subscription_service.reconcile_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is not None
    assert result.id == "sub-1"
    graph_client.create_subscription.assert_not_awaited()
    graph_client.delete_subscription.assert_not_awaited()
    redis.hset.assert_awaited()


@pytest.mark.asyncio
async def test_renew_skips_when_not_near_expiry() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    far = _sub(expiration=datetime.now(UTC) + timedelta(days=2))
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[far])
    graph_client.renew_subscription = AsyncMock()

    result = await subscription_service.renew_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is not None
    assert result.id == "sub-1"
    graph_client.renew_subscription.assert_not_awaited()


@pytest.mark.asyncio
async def test_renew_when_near_expiry() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    redis.hset = AsyncMock(return_value=1)
    near = _sub(expiration=datetime.now(UTC) + timedelta(hours=1))
    renewed = _sub(expiration=datetime.now(UTC) + timedelta(days=2))
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[near])
    graph_client.renew_subscription = AsyncMock(return_value=renewed)

    result = await subscription_service.renew_mailbox_subscription(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        mailbox="user@example.com",
    )

    assert result is not None
    graph_client.renew_subscription.assert_awaited_once_with("sub-1")


@pytest.mark.asyncio
async def test_lifecycle_subscription_removed_reconciles() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    redis.delete = AsyncMock(return_value=1)
    redis.hset = AsyncMock(return_value=1)
    redis.eval = AsyncMock(return_value=1)
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[])
    graph_client.create_subscription = AsyncMock(return_value=_sub())

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "lifecycleEvent": "subscriptionRemoved",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )

    await subscription_service.handle_lifecycle_event(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        notification=item,
    )

    redis.delete.assert_awaited()
    graph_client.create_subscription.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_missed_triggers_poll() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    graph_client = MagicMock()
    poll_fn = AsyncMock()

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "lifecycleEvent": "missed",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )

    await subscription_service.handle_lifecycle_event(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        notification=item,
        poll_mailbox_fn=poll_fn,
    )

    poll_fn.assert_awaited_once()
    assert poll_fn.await_args.kwargs["lookback_override"] is not None
    assert poll_fn.await_args.kwargs.get("outbound_only") in (None, False)
    assert poll_fn.await_args.kwargs.get("folders") is None


@pytest.mark.asyncio
async def test_lifecycle_reauthorization_force_renews_even_when_not_near_expiry() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    redis.hset = AsyncMock(return_value=1)
    far = _sub(expiration=datetime.now(UTC) + timedelta(days=2))
    renewed = _sub(expiration=datetime.now(UTC) + timedelta(days=3))
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[far])
    graph_client.renew_subscription = AsyncMock(return_value=renewed)

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "lifecycleEvent": "reauthorizationRequired",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )

    await subscription_service.handle_lifecycle_event(
        redis=redis,
        graph_client=graph_client,
        settings=settings,
        notification=item,
    )

    graph_client.renew_subscription.assert_awaited_once_with("sub-1")


@pytest.mark.asyncio
async def test_lifecycle_reauthorization_renews() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"subscription_id":"sub-1"}')
    redis.hset = AsyncMock(return_value=1)
    near = _sub(expiration=datetime.now(UTC) + timedelta(hours=1))
    renewed = _sub(expiration=datetime.now(UTC) + timedelta(days=2))
    graph_client = MagicMock()
    graph_client.list_subscriptions = AsyncMock(return_value=[near])
    graph_client.renew_subscription = AsyncMock(return_value=renewed)

    item = GraphNotificationItemSchema.model_validate(
        {
            "subscriptionId": "sub-1",
            "clientState": "secret",
            "lifecycleEvent": "reauthorizationRequired",
            "resource": "users/user@example.com/mailFolders('inbox')/messages",
        }
    )

    with patch.object(
        subscription_service,
        "renew_mailbox_subscription",
        new_callable=AsyncMock,
        return_value=renewed,
    ) as renew:
        await subscription_service.handle_lifecycle_event(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            notification=item,
        )

    renew.assert_awaited_once()
    assert renew.await_args.kwargs["force"] is True


class _MemoryRedis:
    """Minimal Redis: strings and hashes. Matches decode_responses=True."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, object]] = {}

    async def type(self, key: str) -> str:
        row = self._data.get(key)
        return "none" if row is None else row[0]

    async def get(self, key: str) -> str | None:
        row = self._data.get(key)
        if row is None:
            return None
        if row[0] != "string":
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        value = row[1]
        return value if isinstance(value, str) else None

    async def set(self, key: str, value: str) -> bool:
        self._data[key] = ("string", value)
        return True

    async def hset(self, key: str, mapping: dict[str, str] | None = None) -> int:
        if mapping is None:
            mapping = {}
        row = self._data.get(key)
        if row is not None and row[0] != "hash":
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        fields: dict[str, str]
        if row is None or row[0] != "hash":
            fields = {}
        else:
            existing = row[1]
            fields = dict(existing) if isinstance(existing, dict) else {}
        added = 0
        for field, value in mapping.items():
            if field not in fields:
                added += 1
            fields[field] = value
        self._data[key] = ("hash", fields)
        return added

    async def hget(self, key: str, field: str) -> str | None:
        row = self._data.get(key)
        if row is None:
            return None
        if row[0] != "hash":
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        fields = row[1]
        if not isinstance(fields, dict):
            return None
        value = fields.get(field)
        return value if isinstance(value, str) else None

    async def hgetall(self, key: str) -> dict[str, str]:
        row = self._data.get(key)
        if row is None:
            return {}
        if row[0] != "hash":
            raise ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")
        fields = row[1]
        if not isinstance(fields, dict):
            return {}
        return {
            field: value
            for field, value in fields.items()
            if isinstance(field, str) and isinstance(value, str)
        }

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_store_subscription_writes_hash_fields_not_json_blob() -> None:
    """Hashes allow HGET of subscription_id without parse+rewrite.

    https://redis.io/docs/latest/develop/data-types/hashes/
    """
    redis = _MemoryRedis()
    sub = _sub()
    await subscription_service._store_subscription(
        redis, "user@example.com", sub, "inbox"
    )
    key = "graph:sub:inbox:user@example.com"
    assert await redis.type(key) == "hash"
    assert await redis.hget(key, "subscription_id") == "sub-1"
    assert await redis.hget(key, "mailbox") == "user@example.com"
    assert await redis.hget(key, "folder") == "inbox"


@pytest.mark.asyncio
async def test_load_cached_subscription_id_migrates_legacy_json_string() -> None:
    """Existing JSON string keys must still yield the id, then become a hash."""
    redis = _MemoryRedis()
    key = "graph:sub:inbox:user@example.com"
    await redis.set(
        key,
        json.dumps(
            {
                "subscription_id": "sub-legacy",
                "mailbox": "user@example.com",
                "folder": "inbox",
            }
        ),
    )
    loaded = await subscription_service._load_cached_subscription_id(
        redis, "user@example.com", "inbox"
    )
    assert loaded == "sub-legacy"
    assert await redis.type(key) == "hash"
    assert await redis.hget(key, "subscription_id") == "sub-legacy"
