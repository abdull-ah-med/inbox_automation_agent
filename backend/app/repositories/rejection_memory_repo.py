"""Rejection memory repository — store rejects, retrieve as negative constraints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.internal_mail import extract_email_address
from app.models.db.draft import Draft
from app.models.db.rejection_memory import RejectionMemory
from app.repositories._vector_common import cap_limit, set_hnsw_session_defaults
from app.repositories.memory_list_common import latest_inbound_sender_subquery
from app.utils.text import truncate_display

_NOTE_DISPLAY_MAX = 240
_BODY_PREVIEW_MAX = 400
_ONE_LINE_MAX = 140


class RejectionMemorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    mailbox: str
    routing_category: str
    reason_code: str
    note: str
    is_excluded: bool = False
    created_at: datetime | None = None


class RejectionMemoryListItem(BaseModel):
    """Settings list row — rejection note plus draft context."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID | None = None
    mailbox: str
    routing_category: str
    reason_code: str
    note: str
    reason_text: str | None = None
    is_excluded: bool = False
    created_at: datetime | None = None
    draft_subject: str | None = None
    draft_body: str | None = None
    draft_body_preview: str | None = None
    preview_line: str | None = None
    sender_email: str | None = None
    receiver_email: str | None = None


def format_constraint_line(*, reason_code: str, note: str) -> str:
    text = truncate_display(note.strip(), _NOTE_DISPLAY_MAX)
    return f"[{reason_code}] {text}"


async def store_rejection_memory(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    note: str,
    embedding: list[float],
) -> RejectionMemorySchema:
    existing_stmt = select(RejectionMemory).where(RejectionMemory.draft_id == draft_id)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return RejectionMemorySchema.model_validate(existing)

    stmt = (
        pg_insert(RejectionMemory)
        .values(
            draft_id=draft_id,
            mailbox=mailbox,
            routing_category=routing_category,
            reason_code=reason_code,
            note=note,
            embedding=embedding,
        )
        .on_conflict_do_nothing(index_elements=["draft_id"])
        .returning(RejectionMemory)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return RejectionMemorySchema.model_validate(row)

    existing = (
        await session.execute(select(RejectionMemory).where(RejectionMemory.draft_id == draft_id))
    ).scalar_one()
    return RejectionMemorySchema.model_validate(existing)


async def count_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
) -> int:
    stmt = (
        select(func.count())
        .select_from(RejectionMemory)
        .where(
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
            RejectionMemory.reason_code == reason_code,
            RejectionMemory.is_excluded.is_(False),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def list_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str | None = None,
    limit: int = 20,
) -> list[RejectionMemorySchema]:
    stmt = (
        select(RejectionMemory)
        .where(
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
            RejectionMemory.is_excluded.is_(False),
        )
        .order_by(RejectionMemory.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    if reason_code is not None:
        stmt = stmt.where(RejectionMemory.reason_code == reason_code)
    result = await session.execute(stmt)
    return [RejectionMemorySchema.model_validate(row) for row in result.scalars().all()]


async def list_notes_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    limit: int = 20,
) -> list[str]:
    rows = await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        limit=limit,
    )
    return [row.note for row in rows]


async def list_ids_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    limit: int = 20,
) -> list[uuid.UUID]:
    rows = await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        limit=limit,
    )
    return [row.id for row in rows]


async def list_recent_for_category(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    limit: int = 3,
) -> list[RejectionMemorySchema]:
    return await list_for_bucket(
        session,
        mailbox=mailbox,
        routing_category=routing_category,
        limit=limit,
    )


async def find_similar_constraints(
    session: AsyncSession,
    *,
    query_embedding: list[float],
    mailbox: str,
    routing_category: str,
    limit: int = 3,
) -> list[str]:
    if limit < 1:
        return []
    await set_hnsw_session_defaults(session, get_settings())
    distance = RejectionMemory.embedding.cosine_distance(query_embedding)
    stmt = (
        select(
            RejectionMemory.reason_code,
            RejectionMemory.note,
            distance.label("distance"),
        )
        .where(
            RejectionMemory.is_excluded.is_(False),
            RejectionMemory.mailbox == mailbox,
            RejectionMemory.routing_category == routing_category,
        )
        .order_by(distance)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        format_constraint_line(reason_code=row.reason_code, note=row.note)
        for row in result.all()
        if row.note
    ]


async def set_excluded_for_ids(
    session: AsyncSession,
    memory_ids: list[uuid.UUID],
    *,
    is_excluded: bool = True,
) -> int:
    if not memory_ids:
        return 0
    stmt = (
        sa_update(RejectionMemory)
        .where(RejectionMemory.id.in_(memory_ids))
        .values(is_excluded=is_excluded)
    )
    result = await session.execute(stmt)
    await session.flush()
    return int(getattr(result, "rowcount", 0) or 0)


def _one_line(text: str | None, *, max_len: int = _ONE_LINE_MAX) -> str | None:
    if not text or not str(text).strip():
        return None
    collapsed = " ".join(str(text).split())
    return truncate_display(collapsed, max_len)


def _sender_email(raw: str | None) -> str | None:
    if not raw or not str(raw).strip():
        return None
    return extract_email_address(str(raw)) or str(raw).strip()


def _list_item_from_row(
    memory: RejectionMemory,
    *,
    thread_id: uuid.UUID | None,
    draft_subject: str | None,
    draft_body: str | None,
    sender_raw: str | None,
) -> RejectionMemoryListItem:
    preview = None
    if draft_body and draft_body.strip():
        preview = truncate_display(draft_body.strip(), _BODY_PREVIEW_MAX)
    return RejectionMemoryListItem(
        id=memory.id,
        draft_id=memory.draft_id,
        thread_id=thread_id,
        mailbox=memory.mailbox,
        routing_category=memory.routing_category,
        reason_code=memory.reason_code,
        note=memory.note,
        reason_text=memory.note,
        is_excluded=bool(memory.is_excluded),
        created_at=memory.created_at,
        draft_subject=draft_subject,
        draft_body=draft_body,
        draft_body_preview=preview,
        preview_line=_one_line(draft_body),
        sender_email=_sender_email(sender_raw),
        receiver_email=memory.mailbox,
    )


async def _list_enriched(
    session: AsyncSession,
    *,
    where_clause: object,
    limit: int,
) -> list[RejectionMemoryListItem]:
    capped = cap_limit(limit, maximum=500)
    body_expr = func.coalesce(Draft.edited_body, Draft.body)
    inbound = latest_inbound_sender_subquery()
    stmt = (
        select(
            RejectionMemory,
            Draft.thread_id,
            Draft.subject,
            body_expr,
            inbound.c.sender,
        )
        .outerjoin(Draft, Draft.id == RejectionMemory.draft_id)
        .outerjoin(inbound, inbound.c.thread_id == Draft.thread_id)
        .where(where_clause)
        .order_by(RejectionMemory.created_at.desc())
        .limit(capped)
    )
    result = await session.execute(stmt)
    return [
        _list_item_from_row(
            memory,
            thread_id=thread_id,
            draft_subject=subject,
            draft_body=body,
            sender_raw=sender,
        )
        for memory, thread_id, subject, body, sender in result.all()
    ]


async def list_for_mailbox(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 100,
) -> list[RejectionMemoryListItem]:
    """List rejection memories newest-first for Settings (includes excluded)."""
    if not (mailbox or "").strip():
        raise ValueError("mailbox is required")
    return await _list_enriched(
        session,
        where_clause=RejectionMemory.mailbox == mailbox,
        limit=limit,
    )


async def list_all_for_mailboxes(
    session: AsyncSession,
    *,
    mailboxes: list[str],
    limit: int = 100,
) -> list[RejectionMemoryListItem]:
    """Cross-mailbox list for Settings. Empty allowlist returns no rows."""
    allowed = [item.strip() for item in mailboxes if item and item.strip()]
    if not allowed:
        return []
    return await _list_enriched(
        session,
        where_clause=RejectionMemory.mailbox.in_(allowed),
        limit=limit,
    )


async def set_excluded(
    session: AsyncSession,
    memory_id: uuid.UUID,
    *,
    is_excluded: bool,
) -> RejectionMemoryListItem | None:
    """Toggle exclusion for one rejection memory row."""
    stmt = (
        sa_update(RejectionMemory)
        .where(RejectionMemory.id == memory_id)
        .values(is_excluded=is_excluded)
        .returning(RejectionMemory)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    items = await _list_enriched(
        session,
        where_clause=RejectionMemory.id == memory_id,
        limit=1,
    )
    return items[0] if items else None
