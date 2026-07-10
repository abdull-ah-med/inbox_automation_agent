"""Ingestion service — Redis dedup, Graph fetch, thread/message persistence.

Does not call classification, rules, draft generation, or Slack.

Dedup lifecycle (Redis SET NX EX):
1. Claim ``processing`` with a short TTL before work starts.
2. On success *after* the DB transaction commits, mark ``completed`` (24h TTL).
3. On failure, delete the claim so webhook/poll retries can proceed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import unquote

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GraphClientError
from app.core.redis_keys import (
    DEDUP_PROCESSING_TTL_SECONDS,
    DEDUP_TTL_SECONDS,
    DEDUP_VALUE_COMPLETED,
    DEDUP_VALUE_PROCESSING,
    dedup_key,
)
from app.graph.client import GraphClient
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
)
from app.models.schemas.graph import (
    GraphMessageSchema,
    GraphNotificationItemSchema,
    IngestResultSchema,
    SimulateIngestRequestSchema,
)
from app.repositories import message_repo, thread_repo

logger = structlog.get_logger(__name__)

_MESSAGE_ID_FROM_RESOURCE = re.compile(
    r"/messages/([^/?]+)",
    re.IGNORECASE,
)
_MAILBOX_FROM_RESOURCE = re.compile(
    r"^users/([^/]+)/",
    re.IGNORECASE,
)


def extract_message_id_from_resource(resource: str) -> str | None:
    match = _MESSAGE_ID_FROM_RESOURCE.search(resource)
    if not match:
        return None
    return unquote(match.group(1))


def extract_mailbox_from_resource(resource: str) -> str | None:
    match = _MAILBOX_FROM_RESOURCE.search(resource)
    if not match:
        return None
    return unquote(match.group(1))


def _sender_address(message: GraphMessageSchema) -> str:
    for candidate in (message.from_, message.sender):
        if candidate and candidate.email_address and candidate.email_address.address:
            return candidate.email_address.address
    return "unknown"


def _body_text(message: GraphMessageSchema) -> str:
    if message.body and message.body.content:
        return message.body.content
    return message.body_preview or ""


def _direction_for_sender(mailbox: str, sender: str) -> EmailDirectionEnum:
    if sender.lower() == mailbox.lower():
        return EmailDirectionEnum.OUTBOUND
    return EmailDirectionEnum.INBOUND


def _to_email_message_schema(
    *,
    mailbox: str,
    conversation_id: str,
    message: GraphMessageSchema,
) -> EmailMessageSchema:
    sender = _sender_address(message)
    received_at = message.received_date_time or datetime.now(UTC)
    return EmailMessageSchema(
        message_id=message.id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=sender,
        subject=message.subject or "(no subject)",
        body_text=_body_text(message),
        body_preview=message.body_preview,
        received_at=received_at,
        direction=_direction_for_sender(mailbox, sender),
    )


async def claim_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> bool:
    """Acquire an in-flight dedup claim. False = already completed or in progress."""
    key = dedup_key(mailbox, message_id)
    existing = await redis.get(key)
    if existing in (DEDUP_VALUE_COMPLETED, DEDUP_VALUE_PROCESSING):
        return False
    created = await redis.set(
        key,
        DEDUP_VALUE_PROCESSING,
        nx=True,
        ex=DEDUP_PROCESSING_TTL_SECONDS,
    )
    return bool(created)


async def complete_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> None:
    """Mark ingest as completed after a successful DB commit."""
    await redis.set(
        dedup_key(mailbox, message_id),
        DEDUP_VALUE_COMPLETED,
        ex=DEDUP_TTL_SECONDS,
    )


async def release_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> None:
    """Drop an in-flight claim so failed work can be retried."""
    await redis.delete(dedup_key(mailbox, message_id))


async def ingest_graph_message(
    *,
    session: AsyncSession,
    redis: Redis,
    graph_client: GraphClient,
    mailbox: str,
    message_id: str,
) -> IngestResultSchema:
    """Fetch a Graph message, resolve its thread, and persist thread + messages.

    Caller must call ``complete_ingest_dedup`` after a successful DB commit, or
    ``release_ingest_dedup`` if the surrounding transaction fails.
    """
    if not await claim_ingest_dedup(redis, mailbox, message_id):
        logger.info("ingestion_duplicate", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="duplicate")

    try:
        message = await graph_client.get_message(mailbox, message_id)
        conversation_id = message.conversation_id
        if not conversation_id:
            raise GraphClientError(f"Message {message_id} is missing conversationId")

        received_at = message.received_date_time or datetime.now(UTC)
        subject = message.subject or "(no subject)"

        thread = await thread_repo.upsert_thread(
            session,
            mailbox=mailbox,
            conversation_id=conversation_id,
            subject=subject,
            last_message_at=received_at,
        )

        thread_messages = await graph_client.list_thread_messages(mailbox, conversation_id)
        if not thread_messages:
            thread_messages = [message]

        context_messages: list[EmailMessageSchema] = []
        for thread_msg in thread_messages:
            email_msg = _to_email_message_schema(
                mailbox=mailbox,
                conversation_id=conversation_id,
                message=thread_msg,
            )
            context_messages.append(email_msg)
            await message_repo.create_message(
                session,
                thread_id=thread.id,
                graph_message_id=thread_msg.id,
                direction=email_msg.direction.value,
                sender=email_msg.sender,
                body_text=email_msg.body_text,
                body_preview=email_msg.body_preview,
                received_at=email_msg.received_at,
            )

        thread_context = ThreadContextSchema(
            conversation_id=conversation_id,
            mailbox=mailbox,
            subject=subject,
            messages=context_messages,
        )

        logger.info(
            "ingestion_complete",
            mailbox=mailbox,
            message_id=message_id,
            thread_id=str(thread.id),
            conversation_id=conversation_id,
            thread_message_count=len(context_messages),
        )
        return IngestResultSchema(
            message_id=message_id,
            status="ingested",
            thread_id=str(thread.id),
            conversation_id=conversation_id,
            thread_context=thread_context,
        )
    except Exception:
        await release_ingest_dedup(redis, mailbox, message_id)
        raise


async def ingest_notification(
    *,
    session: AsyncSession,
    redis: Redis,
    graph_client: GraphClient,
    notification: GraphNotificationItemSchema,
    fallback_mailbox: str | None = None,
) -> IngestResultSchema:
    """Process a single Graph change notification item."""
    message_id = None
    if notification.resource_data and notification.resource_data.id:
        message_id = notification.resource_data.id
    if not message_id:
        message_id = extract_message_id_from_resource(notification.resource)
    if not message_id:
        raise GraphClientError(
            f"Could not extract message id from notification resource: {notification.resource}"
        )

    mailbox = extract_mailbox_from_resource(notification.resource) or fallback_mailbox
    if not mailbox:
        raise GraphClientError(
            f"Could not extract mailbox from notification resource: {notification.resource}"
        )

    return await ingest_graph_message(
        session=session,
        redis=redis,
        graph_client=graph_client,
        mailbox=mailbox,
        message_id=message_id,
    )


async def ingest_simulated_message(
    *,
    session: AsyncSession,
    redis: Redis,
    payload: SimulateIngestRequestSchema,
) -> IngestResultSchema:
    """Persist a simulated email without calling Graph (Phase 0 / local demo).

    Caller must call ``complete_ingest_dedup`` after a successful DB commit.
    """
    if not await claim_ingest_dedup(redis, payload.mailbox, payload.message_id):
        logger.info(
            "simulate_ingestion_duplicate",
            mailbox=payload.mailbox,
            message_id=payload.message_id,
        )
        return IngestResultSchema(message_id=payload.message_id, status="duplicate")

    try:
        thread = await thread_repo.upsert_thread(
            session,
            mailbox=payload.mailbox,
            conversation_id=payload.conversation_id,
            subject=payload.subject,
            last_message_at=payload.received_at,
        )

        direction = _direction_for_sender(payload.mailbox, payload.sender)
        await message_repo.create_message(
            session,
            thread_id=thread.id,
            graph_message_id=payload.message_id,
            direction=direction.value,
            sender=payload.sender,
            body_text=payload.body_text,
            body_preview=payload.body_preview,
            received_at=payload.received_at,
        )

        email_msg = EmailMessageSchema(
            message_id=payload.message_id,
            conversation_id=payload.conversation_id,
            mailbox=payload.mailbox,
            sender=payload.sender,
            subject=payload.subject,
            body_text=payload.body_text,
            body_preview=payload.body_preview,
            received_at=payload.received_at,
            direction=direction,
        )
        thread_context = ThreadContextSchema(
            conversation_id=payload.conversation_id,
            mailbox=payload.mailbox,
            subject=payload.subject,
            messages=[email_msg],
        )

        logger.info(
            "simulate_ingestion_complete",
            mailbox=payload.mailbox,
            message_id=payload.message_id,
            thread_id=str(thread.id),
        )
        return IngestResultSchema(
            message_id=payload.message_id,
            status="ingested",
            thread_id=str(thread.id),
            conversation_id=payload.conversation_id,
            thread_context=thread_context,
        )
    except Exception:
        await release_ingest_dedup(redis, payload.mailbox, payload.message_id)
        raise
