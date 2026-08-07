"""Graph change-notification subscription orchestration.

Creates/reconciles/renews inbox + sentitems subscriptions per target mailbox
and handles lifecycle events. Does not call classification, drafts, or Slack.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import (
    MISSED_POLL_LOOKBACK_SECONDS,
    SUBSCRIPTION_RENEW_BEFORE_SECONDS,
    VALIDATION_PENDING_TTL_SECONDS,
    WEBHOOK_VALIDATION_PENDING_KEY,
    inbox_subscription_key,
    legacy_subscription_key,
    subscription_key,
)
from app.graph.client import GraphClient
from app.models.schemas.graph import (
    GraphNotificationItemSchema,
    GraphSubscriptionSchema,
)

logger = structlog.get_logger(__name__)

SubscriptionFolder = Literal["inbox", "sentitems"]
_SUBSCRIPTION_FOLDERS: tuple[SubscriptionFolder, ...] = ("inbox", "sentitems")

# Refcount the validation window so concurrent create_subscription handshakes
# cannot close each other. INCR opens / refreshes TTL; DECR closes at zero.
# https://redis.io/docs/latest/commands/incr/
_BEGIN_VALIDATION_WINDOW = """
local count = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return count
"""

_END_VALIDATION_WINDOW = """
if redis.call('EXISTS', KEYS[1]) == 0 then
  return 0
end
local count = redis.call('DECR', KEYS[1])
if count <= 0 then
  redis.call('DEL', KEYS[1])
  return 0
end
return count
"""


def _folder_resource(mailbox: str, folder: SubscriptionFolder) -> str:
    return f"users/{mailbox}/mailFolders('{folder}')/messages"


def _normalize_resource(resource: str) -> str:
    return resource.strip().lower().replace(" ", "")


def extract_folder_from_resource(resource: str) -> SubscriptionFolder | None:
    """Return ``inbox`` / ``sentitems`` when the resource targets a well-known folder."""
    normalized = _normalize_resource(resource)
    if "mailfolders('sentitems')" in normalized:
        return "sentitems"
    if "mailfolders('inbox')" in normalized:
        return "inbox"
    return None


def _subscription_matches_mailbox_folder(
    sub: GraphSubscriptionSchema,
    mailbox: str,
    folder: SubscriptionFolder,
) -> bool:
    """Match a subscription to one mailbox folder without substring false positives.

    Uses the mailbox path segment extracted from ``resource`` (exact, case-insensitive)
    plus exact folder equality. Never uses ``mailbox in resource``.
    """
    from app.services.ingestion_service import extract_mailbox_from_resource

    extracted = extract_mailbox_from_resource(sub.resource)
    if extracted is None or extracted.strip().lower() != mailbox.strip().lower():
        return False
    return _normalize_resource(sub.resource) == _normalize_resource(
        _folder_resource(mailbox, folder)
    )


def _subscription_matches_mailbox(sub: GraphSubscriptionSchema, mailbox: str) -> bool:
    """Backward-compatible inbox match used by existing tests and callers."""
    return _subscription_matches_mailbox_folder(sub, mailbox, "inbox")


async def begin_webhook_validation_window(redis: Redis) -> None:
    """Allow Graph validationToken echoes while create_subscription is in flight.

    Uses a refcount so overlapping creates (multi-mailbox reconcile) keep the
    window open until the last create finishes.
    """
    await redis.eval(
        _BEGIN_VALIDATION_WINDOW,
        1,
        WEBHOOK_VALIDATION_PENDING_KEY,
        str(VALIDATION_PENDING_TTL_SECONDS),
    )


async def end_webhook_validation_window(redis: Redis) -> None:
    """Decrement the validation-window refcount; delete at zero."""
    await redis.eval(_END_VALIDATION_WINDOW, 1, WEBHOOK_VALIDATION_PENDING_KEY)


async def webhook_validation_window_open(redis: Redis) -> bool:
    raw = await redis.get(WEBHOOK_VALIDATION_PENDING_KEY)
    if not isinstance(raw, str) or not raw:
        return False
    try:
        return int(raw) > 0
    except ValueError:
        # Legacy single-flag values (e.g. "1" from SET) still count as open.
        return True


def _is_expired_or_near_expiry(
    expiration: datetime,
    *,
    renew_before_seconds: int = SUBSCRIPTION_RENEW_BEFORE_SECONDS,
) -> bool:
    now = datetime.now(UTC)
    exp = expiration if expiration.tzinfo else expiration.replace(tzinfo=UTC)
    return exp <= now + timedelta(seconds=renew_before_seconds)


def _serialize_subscription(
    sub: GraphSubscriptionSchema,
    mailbox: str,
    folder: SubscriptionFolder,
) -> str:
    payload: dict[str, Any] = {
        "subscription_id": sub.id,
        "mailbox": mailbox,
        "folder": folder,
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
    folder: SubscriptionFolder,
) -> None:
    await redis.set(
        subscription_key(mailbox, folder),
        _serialize_subscription(sub, mailbox, folder),
    )
    if folder == "inbox":
        # Drop legacy key once rewritten so future reads use the folder key.
        await redis.delete(legacy_subscription_key(mailbox))


async def _clear_subscription(
    redis: Redis,
    mailbox: str,
    folder: SubscriptionFolder,
) -> None:
    await redis.delete(subscription_key(mailbox, folder))
    if folder == "inbox":
        await redis.delete(legacy_subscription_key(mailbox))


async def _load_cached_subscription_id(
    redis: Redis,
    mailbox: str,
    folder: SubscriptionFolder = "inbox",
) -> str | None:
    raw = await redis.get(subscription_key(mailbox, folder))
    if (not isinstance(raw, str) or not raw) and folder == "inbox":
        # One-shot migration: pre-folder key ``graph:sub:{mailbox}``.
        raw = await redis.get(legacy_subscription_key(mailbox))
        if isinstance(raw, str) and raw:
            await redis.set(inbox_subscription_key(mailbox), raw)
            await redis.delete(legacy_subscription_key(mailbox))
            logger.info("subscription_legacy_key_migrated", mailbox=mailbox)
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
    folder: SubscriptionFolder = "inbox",
) -> GraphSubscriptionSchema | None:
    """Ensure exactly one valid subscription exists for the mailbox folder."""
    notification_url = settings.graph_notification_url.strip()
    lifecycle_url = settings.resolved_lifecycle_url
    client_state = settings.graph_webhook_client_state.strip()

    if not notification_url or not lifecycle_url or not client_state:
        logger.warning(
            "subscription_reconcile_skipped_missing_config",
            mailbox=mailbox,
            folder=folder,
            has_notification_url=bool(notification_url),
            has_lifecycle_url=bool(lifecycle_url),
            has_client_state=bool(client_state),
        )
        return None

    existing = await graph_client.list_subscriptions()
    mailbox_subs = [s for s in existing if _subscription_matches_mailbox_folder(s, mailbox, folder)]

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
            folder=folder,
            subscription_id=sub.id,
        )
        try:
            await graph_client.delete_subscription(sub.id)
        except GraphClientError:
            logger.exception(
                "subscription_delete_failed",
                mailbox=mailbox,
                folder=folder,
                subscription_id=sub.id,
            )

    if keep is not None:
        await _store_subscription(redis, mailbox, keep, folder)
        logger.info(
            "subscription_reconcile_kept",
            mailbox=mailbox,
            folder=folder,
            subscription_id=keep.id,
        )
        return keep

    await begin_webhook_validation_window(redis)
    try:
        created = await graph_client.create_subscription(
            mailbox,
            notification_url,
            client_state,
            lifecycle_notification_url=lifecycle_url,
            folder=folder,
        )
    finally:
        await end_webhook_validation_window(redis)
    await _store_subscription(redis, mailbox, created, folder)
    logger.info(
        "subscription_reconcile_created",
        mailbox=mailbox,
        folder=folder,
        subscription_id=created.id,
    )
    return created


async def renew_mailbox_subscription(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
    mailbox: str,
    folder: SubscriptionFolder = "inbox",
    force: bool = False,
) -> GraphSubscriptionSchema | None:
    """Renew subscription if near expiry, or always when ``force=True``.

    ``force=True`` is required for Graph ``reauthorizationRequired`` lifecycle
    events: Microsoft expects an explicit PATCH renew (or POST reauthorize)
    even when expiration is still far away. See:
    https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events
    """
    cached_id = await _load_cached_subscription_id(redis, mailbox, folder)
    existing = await graph_client.list_subscriptions()
    mailbox_subs = [s for s in existing if _subscription_matches_mailbox_folder(s, mailbox, folder)]

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
            folder=folder,
        )

    if not force and not _is_expired_or_near_expiry(target.expiration_date_time):
        await _store_subscription(redis, mailbox, target, folder)
        return target

    renewed = await graph_client.renew_subscription(target.id)
    await _store_subscription(redis, mailbox, renewed, folder)
    logger.info(
        "subscription_renewed",
        mailbox=mailbox,
        folder=folder,
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
        for folder in _SUBSCRIPTION_FOLDERS:
            try:
                await reconcile_mailbox_subscription(
                    redis=redis,
                    graph_client=graph_client,
                    settings=settings,
                    mailbox=mailbox,
                    folder=folder,
                )
            except Exception:
                logger.exception(
                    "subscription_reconcile_mailbox_failed",
                    mailbox=mailbox,
                    folder=folder,
                )


async def renew_all_mailboxes(
    *,
    redis: Redis,
    graph_client: GraphClient,
    settings: Settings,
) -> None:
    for mailbox in settings.mailbox_list:
        for folder in _SUBSCRIPTION_FOLDERS:
            try:
                await renew_mailbox_subscription(
                    redis=redis,
                    graph_client=graph_client,
                    settings=settings,
                    mailbox=mailbox,
                    folder=folder,
                )
            except Exception:
                logger.exception(
                    "subscription_renew_mailbox_failed",
                    mailbox=mailbox,
                    folder=folder,
                )


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
    if extracted and settings.mailbox_allowed(extracted):
        return extracted
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

    folder = extract_folder_from_resource(notification.resource) or "inbox"

    redis_lookup: dict[str, str] = {}
    for configured_mailbox in settings.mailbox_list:
        cached = await _load_cached_subscription_id(redis, configured_mailbox, folder)
        if cached:
            redis_lookup[configured_mailbox] = cached

    mailbox = _mailbox_from_subscription(
        settings=settings,
        subscription_id=notification.subscription_id,
        resource=notification.resource,
        redis_lookup=redis_lookup,
    )
    if not mailbox:
        # Fall back: scan both folder caches for this subscription id.
        for configured_mailbox in settings.mailbox_list:
            for candidate_folder in _SUBSCRIPTION_FOLDERS:
                cached = await _load_cached_subscription_id(
                    redis, configured_mailbox, candidate_folder
                )
                if cached == notification.subscription_id:
                    mailbox = configured_mailbox
                    folder = candidate_folder
                    break
            if mailbox:
                break

    if not mailbox:
        logger.warning(
            "lifecycle_mailbox_unresolved",
            subscription_id=notification.subscription_id,
            lifecycle_event=event,
            folder=folder,
        )
        return

    if not settings.mailbox_allowed(mailbox):
        logger.warning(
            "lifecycle_mailbox_not_allowed",
            mailbox=mailbox,
            subscription_id=notification.subscription_id,
            lifecycle_event=event,
            folder=folder,
        )
        return

    logger.info(
        "lifecycle_event_received",
        lifecycle_event=event,
        mailbox=mailbox,
        folder=folder,
        subscription_id=notification.subscription_id,
    )

    if event == "reauthorizationRequired":
        await renew_mailbox_subscription(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            mailbox=mailbox,
            folder=folder,
            force=True,
        )
        return

    if event == "subscriptionRemoved":
        await _clear_subscription(redis, mailbox, folder)
        await reconcile_mailbox_subscription(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
            mailbox=mailbox,
            folder=folder,
        )
        return

    if event == "missed":
        if poll_mailbox_fn is None:
            from app.workers.poll_fallback_worker import poll_mailbox

            poll_mailbox_fn = poll_mailbox
        lookback = datetime.now(UTC) - timedelta(seconds=MISSED_POLL_LOOKBACK_SECONDS)
        kwargs: dict[str, Any] = {
            "redis": redis,
            "graph_client": graph_client,
            "lookback_override": lookback,
        }
        # SentItems missed events poll that folder via outbound path when supported.
        try:
            await poll_mailbox_fn(
                mailbox,
                folders=("sentitems",) if folder == "sentitems" else None,
                outbound_only=(folder == "sentitems"),
                **kwargs,
            )
        except TypeError:
            await poll_mailbox_fn(mailbox, **kwargs)
        return

    logger.warning(
        "lifecycle_event_unknown",
        lifecycle_event=event,
        mailbox=mailbox,
        folder=folder,
    )
