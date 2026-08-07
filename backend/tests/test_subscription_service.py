"""Unit tests for Graph subscription reconcile / renew / lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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


@pytest.mark.asyncio
async def test_reconcile_creates_when_missing() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
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
    # validation window begin/end via Redis Lua + subscription cache SET
    assert redis.eval.await_count >= 2
    assert redis.set.await_count >= 1


@pytest.mark.asyncio
async def test_reconcile_recreates_when_lifecycle_url_missing() -> None:
    settings = _settings()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
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
    redis.set = AsyncMock(return_value=True)
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
    redis.set.assert_awaited()


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
    redis.set = AsyncMock(return_value=True)
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
    redis.set = AsyncMock(return_value=True)
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
    redis.set = AsyncMock(return_value=True)
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
    redis.set = AsyncMock(return_value=True)
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
