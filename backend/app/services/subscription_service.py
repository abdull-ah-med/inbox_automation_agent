"""Graph change-notification subscription orchestration.

Creates/reconciles/renews one subscription per target mailbox and handles
lifecycle events. Does not call classification, drafts, or Slack.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import (
    SUBSCRIPTION_RENEW_BEFORE_SECONDS,
    subscription_key,
)
from app.graph.client import GraphClient
from app.models.schemas.graph import (
    GraphNotificationItemSchema,
    GraphSubscriptionSchema,
)

logger = structlog.get_logger(__name__)


def _inbox_resource(mailbox: str) -> str:
    return f"users/{mailbox}/mailFolders('inbox')/messages"


def _subscription_matches_mailbox(sub: GraphSubscriptionSchema, mailbox: str) -> bool:
    resource = sub.resource.lower().replace(" ", "")
    expected = _inbox_resource(mailbox).lower()
    # Graph may return users/{guid}@tenant/... — match mailbox UPN substring or exact.
    return expected in resource or mailbox.lower() in resource


def _is_expired_or_near_expiry(
    expiration: datetime,
    *,
    renew_before_seconds: int = SUBSCRIPTION_RENEW_BEFORE_SECONDS,
) -> bool:
    now = datetime.now(UTC)
    exp = expiration if expiration.tzinfo else expiration.replace(tzinfo=UTC)
    return exp <= now + timedelta(seconds=renew_before_seconds)


def _serialize_subscription(sub: GraphSubscriptionSchema, mailbox: str) -> str:
    payload: dict[str, Any] = {
        "subscription_id": sub.id,
        "mailbox": mailbox,
        "resource": sub.resource,
        "expiration": sub.expiration_date_time.isoformat(),
        "notification_url": sub.notification_url,
        "lifecycle_notification_url": sub.lifecycle_notification_url,
    }
    return json.dumps(payload)


async def _store_subscription(
    redis: Redis,
    mailbox: str,
    sub: GraphSubscriptionSchema,
) -> None:
    await redis.set(subscription_key(mailbox), _serialize_subscription(sub, mailbox))


async def _clear_subscription(redis: Redis, mailbox: str) -> None:
    await redis.delete(subscription_key(mailbox))


async def _load_cached_subscription_id(redis: Redis, mailbox: str) -> str | None:
    raw = await redis.get(subscription_key(mailbox))
    if not isinstance(raw, str) or not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    sub_id = data.get("subscription_id")
    return sub_id if isinstance(sub_id, str) else None


def _subscription_urls_ok(
    sub: GraphSubscriptionSchema,
    *,
    notification_url: str,
    lifecycle_url: str,
    client_state: str,
) -> bool:
    if not sub.lifecycle_notification_url:
        return False
    if sub.notification_url.rstrip("/") != notification_url.rstrip("/"):
        return False
    if sub.lifecycle_notification_url.rstrip("/") != lifecycle_url.rstrip("/"):
        return False
    return not (client_state and sub.client_state != client_state)


async def reconcile_mailbox_subscription(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
    mailbox: str,
) -> GraphSubscriptionSchema | None:
    """Ensure exactly one valid subscription exists for the mailbox."""
    notification_url = settings.graph_notification_url.strip()
    lifecycle_url = settings.resolved_lifecycle_url
    client_state = settings.graph_webhook_client_state.strip()

    if not notification_url or not lifecycle_url or not client_state:
        logger.warning(
            "subscription_reconcile_skipped_missing_config",
            mailbox=mailbox,
            has_notification_url=bool(notification_url),
            has_lifecycle_url=bool(lifecycle_url),
            has_client_state=bool(client_state),
        )
        return None

    existing = await graph_client.list_subscriptions()
    mailbox_subs = [s for s in existing if _subscription_matches_mailbox(s, mailbox)]

    keep: GraphSubscriptionSchema | None = None
    for sub in mailbox_subs:
        if (
            keep is None
            and _subscription_urls_ok(
                sub,
                notification_url=notification_url,
                lifecycle_url=lifecycle_url,
                client_state=client_state,
            )
            and not _is_expired_or_near_expiry(sub.expiration_date_time, renew_before_seconds=0)
        ):
            keep = sub
            continue
        logger.info(
            "subscription_deleting_stale",
            mailbox=mailbox,
            subscription_id=sub.id,
        )
        try:
            await graph_client.delete_subscription(sub.id)
        except GraphClientError:
            logger.exception(
                "subscription_delete_failed",
                mailbox=mailbox,
                subscription_id=sub.id,
            )

    if keep is not None:
        await _store_subscription(redis, mailbox, keep)
        logger.info(
            "subscription_reconcile_kept",
            mailbox=mailbox,
            subscription_id=keep.id,
        )
        return keep

    created = await graph_client.create_subscription(
        mailbox,
        notification_url,
        client_state,
        lifecycle_notification_url=lifecycle_url,
    )
    await _store_subscription(redis, mailbox, created)
    logger.info(
        "subscription_reconcile_created",
        mailbox=mailbox,
        subscription_id=created.id,
    )
    return created


async def renew_mailbox_subscription(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
    mailbox: str,
    force: bool = False,
) -> GraphSubscriptionSchema | None:
    """Renew subscription if near expiry, or always when ``force=True``.

    ``force=True`` is required for Graph ``reauthorizationRequired`` lifecycle
    events: Microsoft expects an explicit PATCH renew (or POST reauthorize)
    even when expiration is still far away. See:
    https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events
    """
    cached_id = await _load_cached_subscription_id(redis, mailbox)
    existing = await graph_client.list_subscriptions()
    mailbox_subs = [s for s in existing if _subscription_matches_mailbox(s, mailbox)]

    target: GraphSubscriptionSchema | None = None
    if cached_id:
        target = next((s for s in mailbox_subs if s.id == cached_id), None)
    if target is None and mailbox_subs:
        target = mailbox_subs[0]

    if target is None:
        return await reconcile_mailbox_subscription(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            mailbox=mailbox,
        )

    if not force and not _is_expired_or_near_expiry(target.expiration_date_time):
        await _store_subscription(redis, mailbox, target)
        return target

    renewed = await graph_client.renew_subscription(target.id)
    await _store_subscription(redis, mailbox, renewed)
    logger.info(
        "subscription_renewed",
        mailbox=mailbox,
        subscription_id=renewed.id,
        forced=force,
    )
    return renewed


async def reconcile_all_mailboxes(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
) -> None:
    for mailbox in settings.mailbox_list:
        try:
            await reconcile_mailbox_subscription(
                redis=redis,
                graph_client=graph_client,
                settings=settings,
                mailbox=mailbox,
            )
        except Exception:
            logger.exception("subscription_reconcile_mailbox_failed", mailbox=mailbox)


async def renew_all_mailboxes(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
) -> None:
    for mailbox in settings.mailbox_list:
        try:
            await renew_mailbox_subscription(
                redis=redis,
                graph_client=graph_client,
                settings=settings,
                mailbox=mailbox,
            )
        except Exception:
            logger.exception("subscription_renew_mailbox_failed", mailbox=mailbox)


def _mailbox_from_subscription(
    *,
    settings: Settings,
    subscription_id: str,
    resource: str,
    redis_lookup: dict[str, str],
) -> str | None:
    for mailbox, cached_id in redis_lookup.items():
        if cached_id == subscription_id:
            return mailbox
    from app.services.ingestion_service import extract_mailbox_from_resource

    extracted = extract_mailbox_from_resource(resource)
    if extracted:
        return extracted
    if settings.mailbox_list:
        return settings.mailbox_list[0]
    return None


async def handle_lifecycle_event(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
    notification: GraphNotificationItemSchema,
    poll_mailbox_fn: Any | None = None,
) -> None:
    """Handle a Graph lifecycle notification item."""
    event = notification.lifecycle_event
    if event is None:
        logger.warning("lifecycle_event_missing", subscription_id=notification.subscription_id)
        return

    redis_lookup: dict[str, str] = {}
    for mailbox in settings.mailbox_list:
        cached = await _load_cached_subscription_id(redis, mailbox)
        if cached:
            redis_lookup[mailbox] = cached

    mailbox = _mailbox_from_subscription(
        settings=settings,
        subscription_id=notification.subscription_id,
        resource=notification.resource,
        redis_lookup=redis_lookup,
    )
    if not mailbox:
        logger.warning(
            "lifecycle_mailbox_unresolved",
            subscription_id=notification.subscription_id,
            lifecycle_event=event,
        )
        return

    logger.info(
        "lifecycle_event_received",
        lifecycle_event=event,
        mailbox=mailbox,
        subscription_id=notification.subscription_id,
    )

    if event == "reauthorizationRequired":
        # Always PATCH-renew: Graph may pause delivery until we respond, even
        # when expiration is outside the normal renew-before window.
        await renew_mailbox_subscription(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            mailbox=mailbox,
            force=True,
        )
        return

    if event == "subscriptionRemoved":
        await _clear_subscription(redis, mailbox)
        await reconcile_mailbox_subscription(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            mailbox=mailbox,
        )
        return

    if event == "missed":
        if poll_mailbox_fn is None:
            from app.workers.poll_fallback_worker import poll_mailbox

            poll_mailbox_fn = poll_mailbox
        lookback = datetime.now(UTC) - timedelta(hours=2)
        await poll_mailbox_fn(
            mailbox,
            redis=redis,
            graph_client=graph_client,
            lookback_override=lookback,
        )
        return

    logger.warning("lifecycle_event_unknown", lifecycle_event=event, mailbox=mailbox)
