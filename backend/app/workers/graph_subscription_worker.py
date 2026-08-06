"""Graph webhook subscription create/reconcile/renew worker."""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.core.dependencies import get_graph_auth, get_graph_client, get_redis
from app.core.redis_keys import (
    RECONCILE_LOCK_TTL_SECONDS,
    SCHEDULER_RECONCILE_LOCK_KEY,
    SCHEDULER_RENEW_LOCK_KEY,
)
from app.core.redis_lock import acquire_lock, release_lock
from app.services import subscription_service

logger = structlog.get_logger(__name__)


async def run_subscription_reconcile() -> None:
    """Ensure one valid subscription exists per target mailbox.

    Only one Uvicorn worker may reconcile at a time (owner-token Redis lock).
    https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
    """
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_reconcile_no_mailboxes")
        return

    redis = await get_redis()
    mailbox_count = max(len(settings.mailbox_list), 1)
    lock_ttl = max(RECONCILE_LOCK_TTL_SECONDS, mailbox_count * 60)
    token = await acquire_lock(
        redis,
        SCHEDULER_RECONCILE_LOCK_KEY,
        ttl_seconds=lock_ttl,
    )
    if token is None:
        logger.info("subscription_reconcile_skipped_not_leader")
        return

    auth = await get_graph_auth(settings, redis)
    graph_client = get_graph_client(auth)
    try:
        await subscription_service.reconcile_all_mailboxes(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
        )
    except Exception:
        logger.exception("subscription_reconcile_failed")
    finally:
        await release_lock(redis, SCHEDULER_RECONCILE_LOCK_KEY, token)


async def run_subscription_renewal() -> None:
    """Renew subscriptions that are within the renew-before window.

    Redis owner-token NX lock ensures only one Uvicorn worker runs the renew
    interval, and a crashed holder cannot delete another worker's lock.
    """
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_renewal_no_mailboxes")
        return

    redis = await get_redis()
    lock_ttl = max(int(settings.subscription_renew_interval_hours * 3600) - 60, 300)
    token = await acquire_lock(
        redis,
        SCHEDULER_RENEW_LOCK_KEY,
        ttl_seconds=lock_ttl,
    )
    if token is None:
        logger.info("subscription_renewal_skipped_not_leader")
        return

    auth = await get_graph_auth(settings, redis)
    graph_client = get_graph_client(auth)
    try:
        await subscription_service.renew_all_mailboxes(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
        )
    except Exception:
        logger.exception("subscription_renewal_failed")
    finally:
        await release_lock(redis, SCHEDULER_RENEW_LOCK_KEY, token)
