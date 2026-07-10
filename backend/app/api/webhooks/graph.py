"""Microsoft Graph change + lifecycle notification webhooks.

Handles:
1. Subscription validation handshake (POST with validationToken query param)
2. Change notifications for new inbox messages
3. Lifecycle notifications (reauthorizationRequired, subscriptionRemoved, missed)

Official docs:
https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks
https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events
"""

from __future__ import annotations

from urllib.parse import unquote

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.dependencies import GraphClientDep, RedisDep, SettingsDep
from app.core.exceptions import GraphClientError
from app.db.session import get_session_factory
from app.graph.client import GraphClient
from app.models.schemas.graph import GraphNotificationItemSchema, GraphNotificationSchema
from app.services import ingestion_service, subscription_service
from app.workers.enqueue import enqueue

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/graph", tags=["graph-webhooks"])


def _validation_response(validation_token: str) -> PlainTextResponse:
    decoded = unquote(validation_token)
    logger.info("graph_subscription_validation")
    return PlainTextResponse(content=decoded, status_code=status.HTTP_200_OK)


def _require_client_state(settings: Settings) -> str:
    """Return configured clientState or raise — required for all non-validation POSTs."""
    expected = settings.graph_webhook_client_state.strip()
    if not expected:
        logger.error("graph_webhook_client_state_not_configured")
        raise GraphClientError(
            "GRAPH_WEBHOOK_CLIENT_STATE must be configured to accept Graph notifications"
        )
    return expected


def _client_state_matches(item: GraphNotificationItemSchema, expected: str) -> bool:
    if item.client_state != expected:
        logger.warning(
            "graph_notification_client_state_mismatch",
            subscription_id=item.subscription_id,
        )
        return False
    return True


def _notification_ids(
    item: GraphNotificationItemSchema,
    fallback_mailbox: str | None,
) -> tuple[str | None, str | None]:
    message_id = None
    if item.resource_data and item.resource_data.id:
        message_id = item.resource_data.id
    if not message_id:
        message_id = ingestion_service.extract_message_id_from_resource(item.resource)
    mailbox = ingestion_service.extract_mailbox_from_resource(item.resource) or fallback_mailbox
    return mailbox, message_id


async def _process_notifications(
    payload: GraphNotificationSchema,
    settings: Settings,
    redis: Redis,
    graph_client: GraphClient,
) -> None:
    expected_state = _require_client_state(settings)
    session_factory = get_session_factory()
    fallback_mailbox = settings.mailbox_list[0] if settings.mailbox_list else None

    for item in payload.value:
        if not _client_state_matches(item, expected_state):
            continue

        if item.lifecycle_event is not None:
            # Change notifications endpoint should not receive lifecycle events,
            # but ignore safely if misconfigured to the same URL.
            logger.info(
                "graph_notification_ignored_lifecycle_on_change_endpoint",
                subscription_id=item.subscription_id,
                event=item.lifecycle_event,
            )
            continue

        mailbox, message_id = _notification_ids(item, fallback_mailbox)
        try:
            async with session_factory() as session, session.begin():
                result = await ingestion_service.ingest_notification(
                    session=session,
                    redis=redis,
                    graph_client=graph_client,
                    notification=item,
                    fallback_mailbox=fallback_mailbox,
                )
            if result.status == "ingested" and mailbox and message_id:
                await ingestion_service.complete_ingest_dedup(redis, mailbox, message_id)
        except Exception:
            if mailbox and message_id:
                await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)
            logger.exception(
                "graph_notification_processing_failed",
                subscription_id=item.subscription_id,
                resource=item.resource,
            )


async def _process_lifecycle_notifications(
    payload: GraphNotificationSchema,
    settings: Settings,
    redis: Redis,
    graph_client: GraphClient,
) -> None:
    expected_state = _require_client_state(settings)

    for item in payload.value:
        if not _client_state_matches(item, expected_state):
            continue

        if item.lifecycle_event is None:
            logger.info(
                "graph_lifecycle_ignored_change_notification",
                subscription_id=item.subscription_id,
            )
            continue

        try:
            await subscription_service.handle_lifecycle_event(
                redis=redis,
                graph_client=graph_client,
                settings=settings,
                notification=item,
            )
        except Exception:
            logger.exception(
                "graph_lifecycle_processing_failed",
                subscription_id=item.subscription_id,
                event=item.lifecycle_event,
            )


@router.post(
    "/notifications",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
)
async def receive_graph_notifications(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    redis: RedisDep,
    graph_client: GraphClientDep,
    validation_token: str | None = Query(default=None, alias="validationToken"),
) -> Response:
    """Receive Graph subscription validation or change notifications."""
    if validation_token is not None:
        return _validation_response(validation_token)

    # Reject forged traffic when clientState is not configured.
    _require_client_state(settings)

    body = await request.json()
    try:
        payload = GraphNotificationSchema.model_validate(body)
    except Exception as exc:
        logger.error("graph_notification_parse_failed", error=str(exc))
        raise GraphClientError(f"Invalid Graph notification payload: {exc}") from exc

    if not payload.value:
        logger.info("graph_notification_empty")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    enqueue(
        background_tasks,
        _process_notifications,
        payload,
        settings,
        redis,
        graph_client,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/lifecycle",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
)
async def receive_graph_lifecycle(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    redis: RedisDep,
    graph_client: GraphClientDep,
    validation_token: str | None = Query(default=None, alias="validationToken"),
) -> Response:
    """Receive Graph lifecycle validation or lifecycle notifications."""
    if validation_token is not None:
        return _validation_response(validation_token)

    _require_client_state(settings)

    body = await request.json()
    try:
        payload = GraphNotificationSchema.model_validate(body)
    except Exception as exc:
        logger.error("graph_lifecycle_parse_failed", error=str(exc))
        raise GraphClientError(f"Invalid Graph lifecycle payload: {exc}") from exc

    if not payload.value:
        logger.info("graph_lifecycle_empty")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    enqueue(
        background_tasks,
        _process_lifecycle_notifications,
        payload,
        settings,
        redis,
        graph_client,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)
