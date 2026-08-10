"""Microsoft Graph change + lifecycle notification webhooks.

Handles:
1. Subscription validation handshake (POST with validationToken query param)
2. Change notifications for new inbox messages
3. Lifecycle notifications (reauthorizationRequired, subscriptionRemoved, missed)

Official docs:
https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks
https://learn.microsoft.com/en-us/graph/change-notifications-lifecycle-events

Delivery contract (Microsoft Learn):
- Return 2xx within ~3 seconds or Graph retries for up to 4 hours.
- Prefer 202 Accepted after queueing async work.
- Never return 4xx/5xx for an unparseable but received payload — that creates
  retry storms and can mark the endpoint slow/drop.
- Misconfiguration (missing clientState) must still ACK with 2xx and log critically.
"""

from __future__ import annotations

import json
from urllib.parse import unquote

import structlog
from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.dependencies import (
    RedisDep,
    SettingsDep,
    get_graph_auth,
    get_graph_client,
    get_redis,
)
from app.core.rate_limit import resolve_client_ip
from app.core.redis_keys import webhook_rate_limit_key
from app.db.session import get_session_factory
from app.graph.client import GraphClient
from app.models.schemas.graph import (
    GraphNotificationItemSchema,
    GraphNotificationSchema,
    IngestResultSchema,
)
from app.services import ingestion_service, pipeline_service, subscription_service
from app.workers.enqueue import enqueue

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks/graph", tags=["graph-webhooks"])

_TRIAGE_ELIGIBLE = frozenset({"ingested", "retry_triage"})


def _validation_response(validation_token: str) -> PlainTextResponse:
    decoded = unquote(validation_token)
    logger.info("graph_subscription_validation")
    return PlainTextResponse(content=decoded, status_code=status.HTTP_200_OK)


def _configured_client_state(settings: Settings) -> str | None:
    expected = settings.graph_webhook_client_state.strip()
    return expected or None


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
) -> tuple[str | None, str | None]:
    message_id = None
    if item.resource_data and item.resource_data.id:
        message_id = item.resource_data.id
    if not message_id:
        message_id = ingestion_service.extract_message_id_from_resource(item.resource)
    mailbox = ingestion_service.extract_mailbox_from_resource(item.resource)
    return mailbox, message_id


def _client_ip(request: Request, settings: Settings) -> str:
    """Rate-limit identity: peer, or trusted X-Real-IP behind our reverse proxy.

    Matches SlowAPI ``resolve_client_ip`` so nginx ``X-Real-IP $remote_addr``
    buckets by the real Graph edge / client, not a single Docker peer.
    Do not trust spoofable leftmost X-Forwarded-For.
    Refs:
    - https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header
    - https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks
    """
    return resolve_client_ip(request, settings)


_RATE_LIMIT_INCR_EXPIRE = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


async def _enforce_webhook_guards(
    request: Request,
    settings: Settings,
    redis: Redis,
) -> Response | None:
    """Buffer body and apply size / rate guards.

    Oversized bodies return 413 (not legitimate Graph traffic under normal
    operation). Rate-limit excess never returns 429: Microsoft Graph retries
    non-2xx for hours and may mark the endpoint unhealthy
    (https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks).

    Over-limit sets ``request.state.webhook_over_rate_limit``. Handlers still
    process payloads with a matching ``clientState`` so real Graph mail is not
    silently dropped behind a shared proxy peer. Abuse (parse fail / bad
    clientState) is ACK'd without enqueue whether or not over limit.

    Body size is enforced by reading at most ``webhook_max_body_bytes + 1`` bytes
    (Content-Length alone is insufficient for chunked / missing-length requests).
    The buffered body is stashed on ``request.state`` for the handler to parse.
    """
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > settings.webhook_max_body_bytes:
            logger.warning(
                "graph_webhook_body_too_large",
                content_length=len(body),
                max_bytes=settings.webhook_max_body_bytes,
            )
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    request.state.webhook_body = bytes(body)

    ip = _client_ip(request, settings)
    key = webhook_rate_limit_key(ip)
    count = int(await redis.eval(_RATE_LIMIT_INCR_EXPIRE, 1, key, "60"))
    if count > settings.webhook_rate_limit_per_minute:
        logger.warning(
            "graph_webhook_rate_limited",
            client_ip=ip,
            count=count,
            limit=settings.webhook_rate_limit_per_minute,
        )
        request.state.webhook_over_rate_limit = True
    return None


async def _parse_webhook_json(request: Request) -> object:
    """Parse JSON from the body buffered by ``_enforce_webhook_guards``."""
    raw: bytes = getattr(request.state, "webhook_body", b"")
    if not raw:
        return await request.json()
    return json.loads(raw)


async def _app_scoped_graph_deps(settings: Settings) -> tuple[Redis, GraphClient]:
    """Resolve process-scoped Redis/GraphClient (not request-tied yield deps)."""
    redis = await get_redis()
    auth = await get_graph_auth(settings, redis)
    return redis, get_graph_client(auth)


async def _run_triage_after_ingest(
    *,
    redis: Redis,
    settings: Settings,
    result: IngestResultSchema,
    mailbox: str | None,
    message_id: str | None,
) -> None:
    if result.status not in _TRIAGE_ELIGIBLE or not mailbox or not message_id:
        return

    claimed = await ingestion_service.claim_triage_lock(redis, mailbox, message_id)
    if not claimed:
        logger.info(
            "triage_skipped_lock_held",
            mailbox=mailbox,
            message_id=message_id,
        )
        return

    try:
        triage_state = await pipeline_service.run_post_ingest_triage(
            redis=redis,
            settings=settings,
            ingest_result=result,
        )
        if triage_state is not None:
            await ingestion_service.complete_ingest_dedup(redis, mailbox, message_id)
        else:
            await ingestion_service.release_ingest_dedup(redis, mailbox, message_id)
    finally:
        await ingestion_service.release_triage_lock(redis, mailbox, message_id, claimed)


async def _process_notifications(
    payload: GraphNotificationSchema,
    settings: Settings,
    redis: Redis | None = None,
    graph_client: GraphClient | None = None,
) -> None:
    if redis is None or graph_client is None:
        redis, graph_client = await _app_scoped_graph_deps(settings)

    expected_state = _configured_client_state(settings)
    if expected_state is None:
        logger.critical("graph_webhook_client_state_not_configured_skipping_batch")
        return

    session_factory = get_session_factory()

    for item in payload.value:
        if not _client_state_matches(item, expected_state):
            continue

        if item.lifecycle_event is not None:
            logger.info(
                "graph_notification_ignored_lifecycle_on_change_endpoint",
                subscription_id=item.subscription_id,
                event=item.lifecycle_event,
            )
            continue

        mailbox, message_id = _notification_ids(item)
        try:
            async with session_factory() as session, session.begin():
                result = await ingestion_service.ingest_notification(
                    session=session,
                    redis=redis,
                    graph_client=graph_client,
                    notification=item,
                    settings=settings,
                )
            await _run_triage_after_ingest(
                redis=redis,
                settings=settings,
                result=result,
                mailbox=mailbox,
                message_id=message_id,
            )
        except Exception:
            # Triage lock is owned only inside ``_run_triage_after_ingest`` and
            # released there with the owner token — never unconditional DEL here
            # (https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).
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
    redis: Redis | None = None,
    graph_client: GraphClient | None = None,
) -> None:
    if redis is None or graph_client is None:
        redis, graph_client = await _app_scoped_graph_deps(settings)

    expected_state = _configured_client_state(settings)
    if expected_state is None:
        logger.critical("graph_webhook_client_state_not_configured_skipping_lifecycle")
        return

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


async def _handle_validation(
    *,
    validation_token: str,
    redis: Redis,
) -> Response:
    """Echo validationToken only while our create_subscription is in flight.

    Graph requires a plain-text 200 echo during subscription creation
    (https://learn.microsoft.com/en-us/graph/change-notifications-delivery-webhooks).
    Rejecting outside that window blocks third parties from pointing subscriptions
    at this URL.
    """
    if not await subscription_service.webhook_validation_window_open(redis):
        logger.warning("graph_validation_rejected_no_pending_window")
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    return _validation_response(validation_token)


def _any_client_state_match(
    payload: GraphNotificationSchema,
    expected: str,
) -> bool:
    return any(item.client_state == expected for item in payload.value)


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
    validation_token: str | None = Query(default=None, alias="validationToken"),
) -> Response:
    """Receive Graph subscription validation or change notifications."""
    if validation_token is not None:
        return await _handle_validation(validation_token=validation_token, redis=redis)

    guard = await _enforce_webhook_guards(request, settings, redis)
    if guard is not None:
        return guard

    expected = _configured_client_state(settings)
    if expected is None:
        # ACK with 202 so Graph does not retry for 4 hours on permanent misconfig.
        logger.critical("graph_webhook_client_state_not_configured")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    try:
        body = await _parse_webhook_json(request)
        payload = GraphNotificationSchema.model_validate(body)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.critical("graph_notification_parse_failed", error=str(exc))
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except Exception as exc:
        logger.critical("graph_notification_parse_failed", error=str(exc))
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if not payload.value:
        logger.info("graph_notification_empty")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    # Cheap sync filter: skip enqueue when every item fails clientState.
    if not _any_client_state_match(payload, expected):
        logger.warning(
            "graph_notification_batch_all_client_state_mismatch",
            over_rate_limit=bool(
                getattr(request.state, "webhook_over_rate_limit", False)
            ),
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    # Matching clientState always enqueues, even when over the rate budget, so
    # legitimate Graph traffic is not dropped behind a shared proxy peer.
    if getattr(request.state, "webhook_over_rate_limit", False):
        logger.warning("graph_webhook_over_limit_valid_client_state_enqueued")

    enqueue(
        background_tasks,
        _process_notifications,
        payload,
        settings,
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
    validation_token: str | None = Query(default=None, alias="validationToken"),
) -> Response:
    """Receive Graph lifecycle validation or lifecycle notifications."""
    if validation_token is not None:
        return await _handle_validation(validation_token=validation_token, redis=redis)

    guard = await _enforce_webhook_guards(request, settings, redis)
    if guard is not None:
        return guard

    expected = _configured_client_state(settings)
    if expected is None:
        logger.critical("graph_webhook_client_state_not_configured")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    try:
        body = await _parse_webhook_json(request)
        payload = GraphNotificationSchema.model_validate(body)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.critical("graph_lifecycle_parse_failed", error=str(exc))
        return Response(status_code=status.HTTP_202_ACCEPTED)
    except Exception as exc:
        logger.critical("graph_lifecycle_parse_failed", error=str(exc))
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if not payload.value:
        logger.info("graph_lifecycle_empty")
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if not _any_client_state_match(payload, expected):
        logger.warning(
            "graph_lifecycle_batch_all_client_state_mismatch",
            over_rate_limit=bool(
                getattr(request.state, "webhook_over_rate_limit", False)
            ),
        )
        return Response(status_code=status.HTTP_202_ACCEPTED)

    if getattr(request.state, "webhook_over_rate_limit", False):
        logger.warning("graph_lifecycle_over_limit_valid_client_state_enqueued")

    enqueue(
        background_tasks,
        _process_lifecycle_notifications,
        payload,
        settings,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)
