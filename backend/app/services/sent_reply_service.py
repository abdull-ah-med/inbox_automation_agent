"""Detect outbound Sent Items replies and mark threads RESOLVED."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import GraphClientError
from app.core.tenant_scope import TenantScope
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.graph import GraphMessageSchema
from app.repositories import draft_repo, message_repo, sent_reply_repo, thread_repo
from app.repositories.message_repo import MessageSchema
from app.repositories.sent_reply_repo import MatchedBy, SentReplySchema
from app.services import audit_service
from app.services.thread_view_service import reply_text_for_message

logger = structlog.get_logger(__name__)

_SENT_BODY_CAP = 20_000
_TIME_WINDOW = timedelta(hours=48)

# Graph eventMessage.meetingMessageType values that are calendar mail, not replies.
_MEETING_MESSAGE_TYPES = frozenset(
    {
        "meetingRequest",
        "meetingCancelled",
        "meetingAccepted",
        "meetingTenativelyAccepted",
        "meetingDeclined",
    }
)


def is_meeting_message(message: MessageSchema) -> bool:
    """True when Graph identified this row as an event/meeting message."""
    return (message.meeting_message_type or "") in _MEETING_MESSAGE_TYPES


def _newest_local_message(messages: list[MessageSchema]) -> MessageSchema | None:
    if not messages:
        return None
    return max(messages, key=lambda m: m.received_at)


async def thread_tip_is_outbound(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> bool:
    """True when the newest local message is outbound. No sent_replies required."""
    messages = await message_repo.list_by_thread(session, thread_id)
    tip = _newest_local_message(messages)
    return tip is not None and tip.direction == "outbound"


async def thread_tip_already_replied(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> bool:
    """True when the tip message is still the Outlook send we resolved on.

    A newer inbound after resolve must return False so drafting can reopen.
    """
    messages = await message_repo.list_by_thread(session, thread_id)
    tip = _newest_local_message(messages)
    if tip is None or tip.direction != "outbound":
        return False
    sent = await sent_reply_repo.get_by_thread(session, thread_id)
    if sent is None:
        return False
    return tip.id == sent.message_id


def _graph_newest_id(graph_messages: list[GraphMessageSchema]) -> str | None:
    dated = [m for m in graph_messages if m.received_date_time is not None]
    if not dated:
        return None
    newest = max(dated, key=lambda m: m.received_date_time or datetime.min.replace(tzinfo=UTC))
    return newest.id


async def fetch_graph_newest_message_id(
    graph_client: Any | None,
    *,
    mailbox: str,
    conversation_id: str,
    trigger_graph_message_id: str | None = None,
) -> str | None:
    """Newest Graph conversation message id, or None when GET cannot confirm."""
    if graph_client is None:
        return None
    list_fn = getattr(graph_client, "list_thread_messages", None)
    if list_fn is None:
        return None
    try:
        graph_messages = await list_fn(mailbox, conversation_id)
    except (GraphClientError, TypeError):
        return None
    if not isinstance(graph_messages, list):
        return None
    by_id = {m.id: m for m in graph_messages if isinstance(m, GraphMessageSchema)}
    trigger = trigger_graph_message_id
    if trigger and trigger not in by_id:
        get_fn = getattr(graph_client, "get_message", None)
        if get_fn is not None:
            try:
                fetched = await get_fn(mailbox, trigger)
            except (GraphClientError, TypeError):
                fetched = None
            if isinstance(fetched, GraphMessageSchema):
                by_id[fetched.id] = fetched
    return _graph_newest_id(list(by_id.values()))


async def graph_outbound_tip_in_sync(
    session: AsyncSession,
    graph_client: Any | None,
    *,
    mailbox: str,
    conversation_id: str,
    thread_id: uuid.UUID,
    trigger_graph_message_id: str | None = None,
) -> bool:
    """True when Graph conversation newest id equals the local outbound tip.

    Mail.Read ``list_thread_messages`` only. GET failure cannot confirm in-sync.
    Always merge the ingest trigger (replication lag), same as ingest.
    """
    if graph_client is None:
        return False
    newest_id = await fetch_graph_newest_message_id(
        graph_client,
        mailbox=mailbox,
        conversation_id=conversation_id,
        trigger_graph_message_id=trigger_graph_message_id,
    )
    if newest_id is None:
        return False
    messages = await message_repo.list_by_thread(session, thread_id)
    tip = _newest_local_message(messages)
    return tip is not None and tip.direction == "outbound" and newest_id == tip.graph_message_id


def _cap_body(body: str) -> str:
    if len(body) <= _SENT_BODY_CAP:
        return body
    return body[:_SENT_BODY_CAP]


def _normalize_sent_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def _pick_draft_match(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    sent_at: datetime,
) -> tuple[uuid.UUID | None, MatchedBy]:
    """Choose draft linkage: approved_draft first, else most recent within 48h."""
    drafts = await draft_repo.list_by_thread(session, thread_id)
    if not drafts:
        return None, "time_window"

    for draft in drafts:
        if draft.approved_at is None:
            continue
        approved_at = _normalize_sent_at(draft.approved_at)
        if approved_at >= sent_at:
            continue
        existing = await sent_reply_repo.get_by_draft(session, draft.id)
        if existing is not None:
            continue
        return draft.id, "approved_draft"

    window_start = sent_at - _TIME_WINDOW
    for draft in drafts:
        created = _normalize_sent_at(draft.created_at)
        if window_start <= created <= sent_at:
            return draft.id, "time_window"

    return None, "time_window"


async def resolve_thread_from_outbound(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    message: MessageSchema,
    conversation_id: str,
    mailbox: str,
) -> SentReplySchema | None:
    """Link an outbound message to a draft (when possible) and set RESOLVED.

    Idempotent on ``message.id`` via unique constraint. Never logs body text.
    Calendar accepts/declines/cancels still resolve (Elise acted) but do not
    attach a drafted letter — they are not email replies to learn from.

    Does not set RESOLVED when a newer message is already the tip (inbound
    follow-up, or a later send). Graph Sent Items often uses a different id
    than conversation sync; that must not close an open follow-up.
    """
    sent_at = _normalize_sent_at(message.received_at)
    if is_meeting_message(message):
        draft_id: uuid.UUID | None = None
        matched_by: MatchedBy = "time_window"
    else:
        draft_id, matched_by = await _pick_draft_match(
            session,
            thread_id=thread_id,
            sent_at=sent_at,
        )

    sent_reply, created = await sent_reply_repo.insert_sent_reply(
        session,
        thread_id=thread_id,
        message_id=message.id,
        draft_id=draft_id,
        sent_body_snapshot=_cap_body(
            reply_text_for_message(
                body_text=message.body_text,
                unique_body_text=message.unique_body_text,
                body_preview=message.body_preview,
            )
        ),
        sent_at=sent_at,
        matched_by=matched_by,
    )

    if not created:
        logger.info(
            "sent_reply_duplicate",
            thread_id=str(thread_id),
            message_id=str(message.id),
            mailbox=mailbox,
        )
        return sent_reply

    messages = await message_repo.list_by_thread(session, thread_id)
    if messages:
        tip = max(messages, key=lambda row: (row.received_at, str(row.id)))
        if tip.id != message.id:
            logger.info(
                "sent_reply_skipped_newer_tip",
                thread_id=str(thread_id),
                message_id=str(message.id),
                tip_id=str(tip.id),
                tip_direction=tip.direction,
                mailbox=mailbox,
            )
            return sent_reply

    await thread_repo.set_thread_outcome(
        session,
        thread_id,
        state=ThreadStateEnum.RESOLVED.value,
    )

    try:
        thread = await thread_repo.get_by_id(session, thread_id, TenantScope.single(mailbox))
        urgency_assessed = thread.urgency if thread is not None else None
        if matched_by == "approved_draft":
            human_body = "You sent the approved draft from Outlook. Taken off Needs Attention."
        elif matched_by == "time_window":
            human_body = (
                "DraftAssistant saw you send from Outlook and closed this. Taken off Needs Attention."
            )
        else:
            human_body = "You sent a reply from Outlook. Taken off Needs Attention."
        await audit_service.log_event(
            session,
            event_type="thread.resolved.sent_reply_detected",
            conversation_id=conversation_id,
            mailbox=mailbox,
            payload={
                "message_id": str(message.id),
                "draft_id": str(draft_id) if draft_id else None,
                "matched_by": matched_by,
                "urgency_assessed": urgency_assessed,
                "human": {
                    "title": "Resolved from sent reply",
                    "body": human_body,
                    "actor_kind": "agent",
                },
            },
            actor="system",
        )
    except Exception:
        logger.warning(
            "sent_reply_audit_failed",
            thread_id=str(thread_id),
            message_id=str(message.id),
        )

    logger.info(
        "sent_reply_resolved",
        thread_id=str(thread_id),
        message_id=str(message.id),
        draft_id=str(draft_id) if draft_id else None,
        matched_by=matched_by,
        mailbox=mailbox,
    )
    return sent_reply
