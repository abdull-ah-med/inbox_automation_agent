"""Graph webhook subscription create/reconcile/renew worker."""

from __future__ import annotations

import structlog

from app.core.config import get_settings
from app.core.dependencies import get_redis
from app.graph.auth import GraphAuth
from app.graph.client import GraphClient
from app.services import subscription_service

logger = structlog.get_logger(__name__)


async def run_subscription_reconcile() -> None:
    """Ensure one valid subscription exists per target mailbox."""
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_reconcile_no_mailboxes")
        return

    redis = await get_redis()
    auth = GraphAuth(settings, redis=redis)
    graph_client = GraphClient(auth)
    try:
        await subscription_service.reconcile_all_mailboxes(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
        )
    except Exception:
        logger.exception("subscription_reconcile_failed")


async def run_subscription_renewal() -> None:
    """Renew subscriptions that are within the renew-before window."""
    settings = get_settings()
    if not settings.mailbox_list:
        logger.info("subscription_renewal_no_mailboxes")
        return

    redis = await get_redis()
    auth = GraphAuth(settings, redis=redis)
    graph_client = GraphClient(auth)
    try:
        await subscription_service.renew_all_mailboxes(
            redis=redis,
            graph_client=graph_client,
            settings=settings,
        )
    except Exception:
        logger.exception("subscription_renewal_failed")
