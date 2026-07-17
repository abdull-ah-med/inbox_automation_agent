"""Graph webhook subscription create/reconcile/renew worker."""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.core.dependencies import get_graph_auth, get_graph_client, get_redis
from app.core.redis_keys import SCHEDULER_RENEW_LOCK_KEY
from app.services import subscription_service

logger = structlog.get_logger(__name__)


async def run_subscription_reconcile() -> None:
    """Ensure one valid subscription exists per target mailbox."""
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_reconcile_no_mailboxes")
        return

    redis = await get_redis()
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


async def run_subscription_renewal() -> None:
    """Renew subscriptions that are within the renew-before window.

    Redis NX lock ensures only one Uvicorn worker runs the renew interval.
    """
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_renewal_no_mailboxes")
        return

    redis = await get_redis()
    lock_ttl = max(int(settings.subscription_renew_interval_hours * 3600) - 60, 300)
    acquired = await redis.set(SCHEDULER_RENEW_LOCK_KEY, "1", nx=True, ex=lock_ttl)
    if not acquired:
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
