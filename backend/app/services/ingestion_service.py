"""Ingestion service — Redis dedup, Graph fetch, thread/message persistence.

Does not call classification, rules, draft generation, or Slack.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import unquote

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GraphClientError
from app.graph.client import GraphClient
from app.models.schemas.email import EmailDirectionEnum
from app.models.schemas.graph import (
    GraphMessageSchema,
    GraphNotificationItemSchema,
    IngestResultSchema,
)
from app.repositories import message_repo, thread_repo

logger = structlog.get_logger(__name__)

DEDUP_TTL_SECONDS = 86_400
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


async def _claim_dedup_key(redis: Redis, mailbox: str, message_id: str) -> bool:
    """Return True if this is the first time we see the message (SET NX succeeded)."""
    key = f"{mailbox}:{message_id}"
    created = await redis.set(key, "1", nx=True, ex=DEDUP_TTL_SECONDS)
    return bool(created)


async def ingest_graph_message(
    *,
    session: AsyncSession,
    redis: Redis,
    graph_client: GraphClient,
    mailbox: str,
    message_id: str,
) -> IngestResultSchema:
    """Fetch a Graph message, resolve its thread, and persist thread + message."""
    if not await _claim_dedup_key(redis, mailbox, message_id):
        logger.info("ingestion_duplicate", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="duplicate")

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

    # Fetch full thread context for future classification (persisted messages only for now).
    thread_messages = await graph_client.list_thread_messages(mailbox, conversation_id)
    logger.debug(
        "ingestion_thread_context",
        conversation_id=conversation_id,
        thread_message_count=len(thread_messages),
    )

    persisted = await message_repo.create_message(
        session,
        thread_id=thread.id,
        graph_message_id=message.id,
        direction=EmailDirectionEnum.INBOUND.value,
        sender=_sender_address(message),
        body_text=_body_text(message),
        body_preview=message.body_preview,
        received_at=received_at,
    )

    logger.info(
        "ingestion_complete",
        mailbox=mailbox,
        message_id=persisted.graph_message_id,
        thread_id=str(thread.id),
        conversation_id=conversation_id,
    )
    return IngestResultSchema(
        message_id=persisted.graph_message_id,
        status="ingested",
        thread_id=str(thread.id),
        conversation_id=conversation_id,
    )


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

    # Graph may return users/{guid}@{tenant}/messages/... — strip tenant suffix if present.
    if "@" in mailbox and mailbox.count("@") == 1:
        # Keep as-is; Graph accepts UPN and object id forms.
        pass

    return await ingest_graph_message(
        session=session,
        redis=redis,
        graph_client=graph_client,
        mailbox=mailbox,
        message_id=message_id,
    )
