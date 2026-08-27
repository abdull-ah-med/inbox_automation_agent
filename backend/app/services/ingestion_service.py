"""Ingestion service — Redis dedup, Graph fetch, thread/message persistence.

Does not call classification, rules, draft generation, or Slack.

Dedup lifecycle (Redis SET NX EX):
1. Claim ``processing`` with a short TTL before work starts.
2. On success *after* triage succeeds, mark ``completed`` (24h TTL).
3. On failure, delete the claim so webhook/poll retries can proceed.
4. If claim is ``in_flight`` but the message already exists in Postgres, return
   ``retry_triage`` so callers can finish triage without re-fetching Graph.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import unquote

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import GraphClientError
from app.core.redis_keys import (
    DEDUP_PROCESSING_TTL_SECONDS,
    DEDUP_TTL_SECONDS,
    DEDUP_VALUE_COMPLETED,
    DEDUP_VALUE_PROCESSING,
    TRIAGE_LOCK_TTL_SECONDS,
    dedup_key,
    triage_lock_key,
)
from app.core.tenant_scope import TenantScope
from app.graph.client import GraphClient
from app.llm import email_clean
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
)
from app.models.schemas.graph import (
    GraphMessageBodySchema,
    GraphMessageSchema,
    GraphNotificationItemSchema,
    GraphRecipientSchema,
    IngestResultSchema,
    SimulateIngestRequestSchema,
)
from app.repositories import message_repo, thread_repo
from app.services.related_match import alert_cluster_keys
from app.utils.email_quotes import split_quoted_history

logger = structlog.get_logger(__name__)

DedupClaim = Literal["claimed", "completed", "in_flight"]

_MESSAGE_ID_FROM_RESOURCE = re.compile(
    r"/messages/([^/?]+)",
    re.IGNORECASE,
)
_MAILBOX_FROM_RESOURCE = re.compile(
    r"^users/([^/]+)/",
    re.IGNORECASE,
)
_SENT_ITEMS_FOLDER = re.compile(
    r"mailfolders\('sentitems'\)",
    re.IGNORECASE,
)
_WELL_KNOWN_FOLDER = re.compile(
    r"mailfolders\('(?P<folder>inbox|junkemail|sentitems)'\)",
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
    mailbox = unquote(match.group(1))
    prefix = "AAD-UPN:"
    if mailbox.upper().startswith(prefix):
        return mailbox[len(prefix) :]
    return mailbox


def is_sent_items_resource(resource: str) -> bool:
    """True when the Graph resource path targets the Sent Items well-known folder."""
    normalized = resource.strip().lower().replace(" ", "")
    return _SENT_ITEMS_FOLDER.search(normalized) is not None


def extract_well_known_folder(resource: str) -> str | None:
    """Return inbox / junkemail / sentitems when the Graph resource names that folder."""
    normalized = resource.strip().lower().replace(" ", "")
    match = _WELL_KNOWN_FOLDER.search(normalized)
    if match is None:
        return None
    return match.group("folder").lower()


async def _maybe_set_alert_fingerprint(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    mailbox: str,
    sender: str,
    subject: str,
) -> None:
    keys = alert_cluster_keys(mailbox=mailbox, sender=sender, subject=subject)
    if keys is None:
        return
    fingerprint, signature, sender_norm = keys
    await thread_repo.set_alert_fingerprint(
        session,
        thread_id,
        fingerprint,
        signature=signature,
        sender_norm=sender_norm,
    )


def _sender_address(message: GraphMessageSchema) -> str:
    for candidate in (message.from_, message.sender):
        if candidate and candidate.email_address and candidate.email_address.address:
            return candidate.email_address.address
    return "unknown"


def _recipient_addresses(
    recipients: list[GraphRecipientSchema],
) -> list[str]:
    addresses: list[str] = []
    for recipient in recipients:
        if recipient.email_address and recipient.email_address.address:
            addresses.append(recipient.email_address.address)
    return addresses


def _item_plain_text(body: GraphMessageBodySchema | None) -> str:
    if body is None:
        return ""
    return email_clean.to_plain_text(
        body.content or "",
        content_type=body.content_type or "text",
    )


def _body_text(message: GraphMessageSchema) -> str:
    plain = _item_plain_text(message.body)
    if plain.strip():
        return plain
    return message.body_preview or ""


def _unique_body_text(message: GraphMessageSchema, full_body: str) -> str:
    """Newest reply only. Graph uniqueBody is a hint — ticket dumps still need split."""
    unique = _item_plain_text(message.unique_body)
    candidate = unique.strip() if unique.strip() else (full_body or "").strip()
    if not candidate:
        return ""
    main, quoted = split_quoted_history(candidate)
    if quoted is not None:
        return email_clean.strip_plain_text_artifacts(main.strip())
    return email_clean.strip_plain_text_artifacts(candidate)


def _direction_for_sender(
    mailbox: str,
    sender: str,
    *,
    settings: Settings | None = None,
) -> EmailDirectionEnum:
    if sender.lower() == mailbox.lower():
        return EmailDirectionEnum.OUTBOUND
    resolved = settings if settings is not None else get_settings()
    if resolved.is_reviewer_address(sender):
        return EmailDirectionEnum.OUTBOUND
    return EmailDirectionEnum.INBOUND


def _body_content_type(message: GraphMessageSchema) -> str:
    if message.body and message.body.content_type:
        ctype = message.body.content_type.strip().lower() or "text"
        if ctype == "html":
            return "text"
        return ctype
    return "text"


def _apply_body_clean(email: EmailMessageSchema) -> EmailMessageSchema:
    """Compute and attach ``body_clean`` (never raises)."""
    cleaned = email_clean.clean_email_body(
        email.body_text,
        content_type=email.body_content_type,
    )
    return email.model_copy(update={"body_clean": cleaned.body_clean})


def _ensure_body_clean_from_row(
    *,
    body_text: str,
    body_content_type: str,
    body_clean: str | None,
    body_clean_version: int | None,
) -> tuple[str, int]:
    """Return (body_clean, version), recomputing when missing or stale."""
    if body_clean and body_clean.strip() and body_clean_version == email_clean.CLEAN_VERSION:
        return body_clean, body_clean_version
    cleaned = email_clean.clean_email_body(body_text, content_type=body_content_type)
    return cleaned.body_clean, email_clean.CLEAN_VERSION


def _to_email_message_schema(
    *,
    mailbox: str,
    conversation_id: str,
    message: GraphMessageSchema,
) -> EmailMessageSchema:
    sender = _sender_address(message)
    received_at = message.received_date_time or datetime.now(UTC)
    body_text = _body_text(message)
    email = EmailMessageSchema(
        message_id=message.id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=sender,
        subject=message.subject or "(no subject)",
        body_text=body_text,
        body_preview=message.body_preview,
        unique_body_text=_unique_body_text(message, body_text),
        body_content_type=_body_content_type(message),
        received_at=received_at,
        direction=_direction_for_sender(mailbox, sender),
        to_recipients=_recipient_addresses(message.to_recipients),
        cc_recipients=_recipient_addresses(message.cc_recipients),
        bcc_recipients=_recipient_addresses(message.bcc_recipients),
        has_attachments=bool(message.has_attachments),
        graph_folder=message.source_folder,
        meeting_message_type=message.meeting_message_type,
        meeting_response_type=message.response_type,
    )
    return _apply_body_clean(email)


async def claim_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> DedupClaim:
    """Acquire an in-flight dedup claim or report why it could not be claimed."""
    key = dedup_key(mailbox, message_id)
    existing = await redis.get(key)
    if existing == DEDUP_VALUE_COMPLETED:
        return "completed"
    if existing == DEDUP_VALUE_PROCESSING:
        return "in_flight"
    created = await redis.set(
        key,
        DEDUP_VALUE_PROCESSING,
        nx=True,
        ex=DEDUP_PROCESSING_TTL_SECONDS,
    )
    if created:
        return "claimed"
    again = await redis.get(key)
    if again == DEDUP_VALUE_COMPLETED:
        return "completed"
    return "in_flight"


async def complete_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> None:
    """Mark ingest+triage as completed after a successful post-ingest pipeline."""
    await redis.set(
        dedup_key(mailbox, message_id),
        DEDUP_VALUE_COMPLETED,
        ex=DEDUP_TTL_SECONDS,
    )


async def release_ingest_dedup(redis: Redis, mailbox: str, message_id: str) -> None:
    """Drop an in-flight claim so failed work can be retried.

    Only deletes when the value is still ``processing`` — never wipes a
    concurrent ``completed`` mark.
    """
    from app.core.redis_lock import compare_delete

    await compare_delete(redis, dedup_key(mailbox, message_id), DEDUP_VALUE_PROCESSING)


async def claim_triage_lock(redis: Redis, mailbox: str, message_id: str) -> str | None:
    """Acquire an exclusive lock for post-ingest Haiku triage (NX + TTL).

    Returns the owner token on success, or ``None`` if the lock is held.
    """
    from app.core.redis_lock import acquire_lock

    return await acquire_lock(
        redis,
        triage_lock_key(mailbox, message_id),
        ttl_seconds=TRIAGE_LOCK_TTL_SECONDS,
    )


async def release_triage_lock(
    redis: Redis,
    mailbox: str,
    message_id: str,
    token: str,
) -> None:
    """Release the triage lock only when ``token`` still owns it.

    Per Redis distributed-lock guidance, never unconditional ``DEL`` — a slow
    holder must not delete a lock another worker acquired after TTL expiry.
    https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
    """
    from app.core.redis_lock import release_lock

    await release_lock(redis, triage_lock_key(mailbox, message_id), token)


async def build_thread_context_from_db(
    session: AsyncSession,
    *,
    mailbox: str,
    message_id: str,
) -> IngestResultSchema | None:
    """Rebuild triage inputs from Postgres when Redis says processing but rows exist."""
    existing = await message_repo.get_by_graph_id(session, message_id)
    if existing is None:
        return None
    thread = await thread_repo.get_by_id(session, existing.thread_id, TenantScope.single(mailbox))
    if thread is None:
        return None
    db_messages = await message_repo.list_by_thread(session, thread.id)
    context_messages: list[EmailMessageSchema] = []
    for row in db_messages:
        body_clean, clean_version = _ensure_body_clean_from_row(
            body_text=row.body_text,
            body_content_type=row.body_content_type or "text",
            body_clean=row.body_clean,
            body_clean_version=row.body_clean_version,
        )
        if row.body_clean != body_clean or row.body_clean_version != clean_version:
            await message_repo.update_body_clean(
                session,
                message_id=row.id,
                body_clean=body_clean,
                body_clean_version=clean_version,
                body_clean_computed_at=datetime.now(UTC),
            )
        context_messages.append(
            EmailMessageSchema(
                message_id=row.graph_message_id,
                conversation_id=thread.conversation_id,
                mailbox=mailbox,
                sender=row.sender,
                subject=thread.subject,
                body_text=row.body_text,
                body_preview=row.body_preview,
                body_content_type=row.body_content_type or "text",
                body_clean=body_clean,
                received_at=row.received_at,
                direction=(
                    EmailDirectionEnum(row.direction)
                    if row.direction in {e.value for e in EmailDirectionEnum}
                    else EmailDirectionEnum.INBOUND
                ),
                to_recipients=list(row.to_recipients or []),
                cc_recipients=list(row.cc_recipients or []),
                bcc_recipients=list(row.bcc_recipients or []),
                has_attachments=bool(row.has_attachments),
                graph_folder=row.graph_folder,
                summary_one_line=row.summary_one_line,
                summary_json=row.summary_json,
            )
        )
    thread_context = ThreadContextSchema(
        conversation_id=thread.conversation_id,
        mailbox=mailbox,
        subject=thread.subject,
        messages=context_messages,
    )
    return IngestResultSchema(
        message_id=message_id,
        status="retry_triage",
        thread_id=str(thread.id),
        conversation_id=thread.conversation_id,
        thread_context=thread_context,
    )


async def ingest_graph_message(
    *,
    session: AsyncSession,
    redis: Redis,
    graph_client: GraphClient,
    mailbox: str,
    message_id: str,
    source_folder: str | None = None,
) -> IngestResultSchema:
    """Fetch a Graph message, resolve its thread, and persist thread + messages.

    Caller must call ``complete_ingest_dedup`` after successful post-ingest triage,
    or ``release_ingest_dedup`` if ingest or triage fails so work can be retried.
    """
    claim = await claim_ingest_dedup(redis, mailbox, message_id)
    if claim == "completed":
        logger.info("ingestion_duplicate", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="duplicate")
    if claim == "in_flight":
        retry = await build_thread_context_from_db(session, mailbox=mailbox, message_id=message_id)
        if retry is not None:
            logger.info(
                "ingestion_retry_triage",
                mailbox=mailbox,
                message_id=message_id,
            )
            return retry
        logger.info("ingestion_in_flight", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="in_flight")

    try:
        message = await graph_client.get_message(mailbox, message_id)
        if source_folder:
            message = message.model_copy(update={"source_folder": source_folder})
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
        await _maybe_set_alert_fingerprint(
            session,
            thread_id=thread.id,
            mailbox=mailbox,
            sender=_sender_address(message),
            subject=subject,
        )

        thread_messages = await graph_client.list_thread_messages(mailbox, conversation_id)
        # Graph list can lag get_message (replication). Always include the trigger.
        by_id = {m.id: m for m in thread_messages}
        by_id[message.id] = message
        thread_messages = list(by_id.values())

        context_messages: list[EmailMessageSchema] = []
        trigger_persisted = None
        for thread_msg in thread_messages:
            email_msg = _to_email_message_schema(
                mailbox=mailbox,
                conversation_id=conversation_id,
                message=thread_msg,
            )
            context_messages.append(email_msg)
            persisted = await message_repo.create_message(
                session,
                thread_id=thread.id,
                graph_message_id=thread_msg.id,
                direction=email_msg.direction.value,
                sender=email_msg.sender,
                body_text=email_msg.body_text,
                body_preview=email_msg.body_preview,
                unique_body_text=email_msg.unique_body_text,
                received_at=email_msg.received_at,
                to_recipients=email_msg.to_recipients,
                cc_recipients=email_msg.cc_recipients,
                bcc_recipients=email_msg.bcc_recipients,
                has_attachments=email_msg.has_attachments,
                graph_folder=email_msg.graph_folder,
                body_content_type=email_msg.body_content_type,
                body_clean=email_msg.body_clean,
                body_clean_version=email_clean.CLEAN_VERSION,
                body_clean_computed_at=datetime.now(UTC),
                meeting_message_type=email_msg.meeting_message_type,
                meeting_response_type=email_msg.meeting_response_type,
            )
            if thread_msg.id == message.id:
                trigger_persisted = persisted

        if trigger_persisted is not None and get_settings().is_reviewer_address(
            _sender_address(message)
        ):
            from app.services import sent_reply_service

            await sent_reply_service.resolve_thread_from_outbound(
                session,
                thread_id=thread.id,
                message=trigger_persisted,
                conversation_id=conversation_id,
                mailbox=mailbox,
            )
            # status="outbound" is not in _TRIAGE_ELIGIBLE; poll/webhook call
            # run_catchup_after_outbound instead (triage-only + learn). Dedup
            # must complete here — otherwise the key stays "processing" until
            # TTL expires and the next poll window re-resolves this message.
            await complete_ingest_dedup(redis, mailbox, message_id)
            logger.info(
                "ingestion_reviewer_copy_resolved",
                mailbox=mailbox,
                message_id=message_id,
                thread_id=str(thread.id),
                conversation_id=conversation_id,
            )
            return IngestResultSchema(
                message_id=message_id,
                status="outbound",
                thread_id=str(thread.id),
                conversation_id=conversation_id,
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
    settings: Settings,
) -> IngestResultSchema:
    """Process a single Graph change notification item.

    Requires an extractable mailbox that passes ``TARGET_MAILBOXES`` when configured.
    No fallback to the first configured mailbox (forged resources must not redirect).
    """
    message_id = None
    if notification.resource_data and notification.resource_data.id:
        message_id = notification.resource_data.id
    if not message_id:
        message_id = extract_message_id_from_resource(notification.resource)
    if not message_id:
        raise GraphClientError(
            f"Could not extract message id from notification resource: {notification.resource}"
        )

    mailbox = extract_mailbox_from_resource(notification.resource)
    if not mailbox:
        logger.warning(
            "ingestion_notification_mailbox_missing",
            resource=notification.resource,
            subscription_id=notification.subscription_id,
        )
        return IngestResultSchema(message_id=message_id, status="skipped")

    if is_sent_items_resource(notification.resource):
        if not settings.outbound_mailbox_allowed(mailbox):
            logger.warning(
                "ingestion_notification_mailbox_not_allowed",
                mailbox=mailbox,
                subscription_id=notification.subscription_id,
            )
            return IngestResultSchema(message_id=message_id, status="skipped")
        return await handle_outbound_notification(
            session=session,
            redis=redis,
            graph_client=graph_client,
            mailbox=mailbox,
            message_id=message_id,
        )

    if not settings.mailbox_allowed(mailbox):
        logger.warning(
            "ingestion_notification_mailbox_not_allowed",
            mailbox=mailbox,
            subscription_id=notification.subscription_id,
        )
        return IngestResultSchema(message_id=message_id, status="skipped")

    return await ingest_graph_message(
        session=session,
        redis=redis,
        graph_client=graph_client,
        mailbox=mailbox,
        message_id=message_id,
        source_folder=extract_well_known_folder(notification.resource),
    )


async def handle_outbound_notification(
    *,
    session: AsyncSession,
    redis: Redis,
    graph_client: GraphClient,
    mailbox: str,
    message_id: str,
) -> IngestResultSchema:
    """Persist a Sent Items message as OUTBOUND and resolve the thread.

    Does **not** run Sonnet draft generation. Poll/webhook callers invoke
    ``sent_reply_learning_service.run_catchup_after_outbound`` after this
    returns ``status="outbound"`` so Haiku triage + reply-memory learning
    still run when inbound exists. Uses the same Redis dedup keys as inbound
    ingest so webhook retries remain idempotent.
    """
    from app.services import sent_reply_service

    claim = await claim_ingest_dedup(redis, mailbox, message_id)
    if claim == "completed":
        logger.info("outbound_ingestion_duplicate", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="duplicate")
    if claim == "in_flight":
        logger.info("outbound_ingestion_in_flight", mailbox=mailbox, message_id=message_id)
        return IngestResultSchema(message_id=message_id, status="in_flight")

    try:
        message = await graph_client.get_message(mailbox, message_id)
        conversation_id = message.conversation_id
        if not conversation_id:
            raise GraphClientError(f"Message {message_id} is missing conversationId")

        received_at = message.received_date_time or datetime.now(UTC)
        subject = message.subject or "(no subject)"
        settings = get_settings()
        shared = await thread_repo.find_thread_by_conversation_id(
            session,
            conversation_id,
            mailboxes=settings.mailbox_list,
        )
        if (
            shared is None
            and settings.is_reviewer_address(mailbox)
            and not settings.mailbox_allowed(mailbox)
        ):
            await complete_ingest_dedup(redis, mailbox, message_id)
            logger.info(
                "outbound_no_matching_shared_thread",
                mailbox=mailbox,
                message_id=message_id,
                conversation_id=conversation_id,
            )
            return IngestResultSchema(
                message_id=message_id,
                status="skipped",
                conversation_id=conversation_id,
            )
        attach_mailbox = shared.mailbox if shared is not None else mailbox
        thread = await thread_repo.upsert_thread(
            session,
            mailbox=attach_mailbox,
            conversation_id=conversation_id,
            subject=subject,
            last_message_at=received_at,
        )
        await _maybe_set_alert_fingerprint(
            session,
            thread_id=thread.id,
            mailbox=attach_mailbox,
            sender=_sender_address(message),
            subject=subject,
        )

        email_msg = _to_email_message_schema(
            mailbox=mailbox,
            conversation_id=conversation_id,
            message=message,
        )
        # Sent Items notifications are always treated as outbound from this mailbox.
        email_msg = email_msg.model_copy(update={"direction": EmailDirectionEnum.OUTBOUND})

        persisted = await message_repo.create_message(
            session,
            thread_id=thread.id,
            graph_message_id=message.id,
            direction=EmailDirectionEnum.OUTBOUND.value,
            sender=email_msg.sender,
            body_text=email_msg.body_text,
            body_preview=email_msg.body_preview,
            unique_body_text=email_msg.unique_body_text,
            received_at=email_msg.received_at,
            to_recipients=email_msg.to_recipients,
            cc_recipients=email_msg.cc_recipients,
            bcc_recipients=email_msg.bcc_recipients,
            has_attachments=email_msg.has_attachments,
            body_content_type=email_msg.body_content_type,
            body_clean=email_msg.body_clean,
            body_clean_version=email_clean.CLEAN_VERSION,
            body_clean_computed_at=datetime.now(UTC),
            meeting_message_type=email_msg.meeting_message_type,
            meeting_response_type=email_msg.meeting_response_type,
        )

        await sent_reply_service.resolve_thread_from_outbound(
            session,
            thread_id=thread.id,
            message=persisted,
            conversation_id=conversation_id,
            mailbox=attach_mailbox,
        )

        await complete_ingest_dedup(redis, mailbox, message_id)

        logger.info(
            "outbound_ingestion_complete",
            mailbox=mailbox,
            message_id=message_id,
            thread_id=str(thread.id),
            conversation_id=conversation_id,
        )
        return IngestResultSchema(
            message_id=message_id,
            status="outbound",
            thread_id=str(thread.id),
            conversation_id=conversation_id,
        )
    except Exception:
        await release_ingest_dedup(redis, mailbox, message_id)
        raise


async def ingest_simulated_message(
    *,
    session: AsyncSession,
    redis: Redis,
    payload: SimulateIngestRequestSchema,
) -> IngestResultSchema:
    """Persist a simulated email without calling Graph (Phase 0 / local demo).

    Caller must call ``complete_ingest_dedup`` after successful post-ingest triage,
    or ``release_ingest_dedup`` if triage fails so the same message_id can retry.
    """
    claim = await claim_ingest_dedup(redis, payload.mailbox, payload.message_id)
    if claim == "completed":
        logger.info(
            "simulate_ingestion_duplicate",
            mailbox=payload.mailbox,
            message_id=payload.message_id,
        )
        return IngestResultSchema(message_id=payload.message_id, status="duplicate")
    if claim == "in_flight":
        retry = await build_thread_context_from_db(
            session, mailbox=payload.mailbox, message_id=payload.message_id
        )
        if retry is not None:
            return retry
        return IngestResultSchema(message_id=payload.message_id, status="in_flight")

    try:
        thread = await thread_repo.upsert_thread(
            session,
            mailbox=payload.mailbox,
            conversation_id=payload.conversation_id,
            subject=payload.subject,
            last_message_at=payload.received_at,
        )
        await _maybe_set_alert_fingerprint(
            session,
            thread_id=thread.id,
            mailbox=payload.mailbox,
            sender=payload.sender,
            subject=payload.subject,
        )

        direction = _direction_for_sender(payload.mailbox, payload.sender)
        unique_main, unique_quoted = split_quoted_history(payload.body_text)
        unique_body_text = (
            unique_main.strip() if unique_quoted is not None else (payload.body_text or "").strip()
        )
        email_msg = _apply_body_clean(
            EmailMessageSchema(
                message_id=payload.message_id,
                conversation_id=payload.conversation_id,
                mailbox=payload.mailbox,
                sender=payload.sender,
                subject=payload.subject,
                body_text=payload.body_text,
                body_preview=payload.body_preview,
                unique_body_text=unique_body_text,
                body_content_type="text",
                received_at=payload.received_at,
                direction=direction,
                to_recipients=list(payload.to_recipients),
                cc_recipients=list(payload.cc_recipients),
                bcc_recipients=list(payload.bcc_recipients),
                has_attachments=payload.has_attachments,
            )
        )
        await message_repo.create_message(
            session,
            thread_id=thread.id,
            graph_message_id=payload.message_id,
            direction=direction.value,
            sender=payload.sender,
            body_text=payload.body_text,
            body_preview=payload.body_preview,
            unique_body_text=email_msg.unique_body_text,
            received_at=payload.received_at,
            to_recipients=list(payload.to_recipients),
            cc_recipients=list(payload.cc_recipients),
            bcc_recipients=list(payload.bcc_recipients),
            has_attachments=payload.has_attachments,
            body_content_type=email_msg.body_content_type,
            body_clean=email_msg.body_clean,
            body_clean_version=email_clean.CLEAN_VERSION,
            body_clean_computed_at=datetime.now(UTC),
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
