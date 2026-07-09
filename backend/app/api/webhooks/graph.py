"""Microsoft Graph change notification webhook.

Handles:
1. Subscription validation handshake (POST with validationToken query param)
2. Change notifications for new inbox messages

Official docs:
https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks
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
from app.models.schemas.graph import GraphNotificationSchema
from app.services import ingestion_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/graph", tags=["graph-webhooks"])


async def _process_notifications(
    payload: GraphNotificationSchema,
    settings: Settings,
    redis: Redis,
    graph_client: GraphClient,
) -> None:
    expected_state = settings.graph_webhook_client_state
    session_factory = get_session_factory()

    for item in payload.value:
        if expected_state and item.client_state != expected_state:
            logger.warning(
                "graph_notification_client_state_mismatch",
                subscription_id=item.subscription_id,
            )
            continue

        try:
            async with session_factory() as session, session.begin():
                await ingestion_service.ingest_notification(
                    session=session,
                    redis=redis,
                    graph_client=graph_client,
                    notification=item,
                    fallback_mailbox=(
                        settings.mailbox_list[0] if settings.mailbox_list else None
                    ),
                )
        except Exception:
            logger.exception(
                "graph_notification_processing_failed",
                subscription_id=item.subscription_id,
                resource=item.resource,
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
        # Graph sends POST ?validationToken=... and requires plain-text echo within 10s.
        decoded = unquote(validation_token)
        logger.info("graph_subscription_validation")
        return PlainTextResponse(content=decoded, status_code=status.HTTP_200_OK)

    body = await request.json()
    try:
        payload = GraphNotificationSchema.model_validate(body)
    except Exception as exc:
        logger.error("graph_notification_parse_failed", error=str(exc))
        raise GraphClientError(f"Invalid Graph notification payload: {exc}") from exc

    if not payload.value:
        logger.info("graph_notification_empty")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    background_tasks.add_task(
        _process_notifications,
        payload,
        settings,
        redis,
        graph_client,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)
