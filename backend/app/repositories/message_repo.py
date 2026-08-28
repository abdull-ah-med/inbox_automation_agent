"""Message repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_scope import TenantScope
from app.models.db.message import Message
from app.models.db.thread import Thread


class MessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    graph_message_id: str
    direction: str
    sender: str
    body_text: str
    body_preview: str | None = None
    unique_body_text: str | None = None
    body_content_type: str = "text"
    body_clean: str | None = None
    body_clean_version: int | None = None
    body_clean_computed_at: datetime | None = None
    received_at: datetime
    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    bcc_recipients: list[str] = Field(default_factory=list)
    has_attachments: bool = False
    graph_folder: str | None = None
    meeting_message_type: str | None = None
    meeting_response_type: str | None = None
    sender_name: str | None = None
    is_automated: bool = False
    summary_json: dict[str, Any] | None = None
    summary_one_line: str | None = None
    summarized_at: datetime | None = None
    summary_model: str | None = None
    summary_clean_version: int | None = None


async def get_by_graph_id(
    session: AsyncSession,
    graph_message_id: str,
) -> MessageSchema | None:
    stmt = select(Message).where(Message.graph_message_id == graph_message_id)
    result = await session.execute(stmt)
    message = result.scalar_one_or_none()
    if message is None:
        return None
    return MessageSchema.model_validate(message)


async def get_by_id(
    session: AsyncSession,
    message_id: uuid.UUID,
    scope: TenantScope,
) -> MessageSchema | None:
    stmt = (
        select(Message)
        .join(Thread, Message.thread_id == Thread.id)
        .where(
            Message.id == message_id,
            Thread.mailbox.in_(list(scope.mailboxes)),
        )
    )
    result = await session.execute(stmt)
    message = result.scalar_one_or_none()
    if message is None:
        return None
    return MessageSchema.model_validate(message)


async def get_by_id_trusted(
    session: AsyncSession,
    message_id: uuid.UUID,
) -> MessageSchema | None:
    """Load a message by PK without mailbox filter (system catch-up paths)."""
    stmt = select(Message).where(Message.id == message_id)
    result = await session.execute(stmt)
    message = result.scalar_one_or_none()
    if message is None:
        return None
    return MessageSchema.model_validate(message)


async def list_by_thread(
    session: AsyncSession,
    thread_id: uuid.UUID,
) -> list[MessageSchema]:
    stmt = select(Message).where(Message.thread_id == thread_id).order_by(Message.received_at.asc())
    result = await session.execute(stmt)
    return [MessageSchema.model_validate(m) for m in result.scalars().all()]


async def list_by_thread_ids(
    session: AsyncSession,
    thread_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[MessageSchema]]:
    """Load messages for many threads in one query, grouped and ordered per thread."""
    if not thread_ids:
        return {}
    stmt = (
        select(Message)
        .where(Message.thread_id.in_(thread_ids))
        .order_by(Message.thread_id.asc(), Message.received_at.asc())
    )
    result = await session.execute(stmt)
    grouped: dict[uuid.UUID, list[MessageSchema]] = {}
    for row in result.scalars().all():
        schema = MessageSchema.model_validate(row)
        grouped.setdefault(schema.thread_id, []).append(schema)
    return grouped


async def create_message(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    graph_message_id: str,
    direction: str,
    sender: str,
    body_text: str,
    body_preview: str | None,
    unique_body_text: str | None = None,
    received_at: datetime,
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    bcc_recipients: list[str] | None = None,
    has_attachments: bool = False,
    graph_folder: str | None = None,
    body_content_type: str = "text",
    body_clean: str | None = None,
    body_clean_version: int | None = None,
    body_clean_computed_at: datetime | None = None,
    meeting_message_type: str | None = None,
    meeting_response_type: str | None = None,
    sender_name: str | None = None,
    is_automated: bool = False,
) -> MessageSchema:
    """Insert a message or fill an empty existing row on ``graph_message_id`` conflict."""
    insert_stmt = insert(Message).values(
        thread_id=thread_id,
        graph_message_id=graph_message_id,
        direction=direction,
        sender=sender,
        body_text=body_text,
        body_preview=body_preview,
        unique_body_text=unique_body_text,
        received_at=received_at,
        to_recipients=list(to_recipients or []),
        cc_recipients=list(cc_recipients or []),
        bcc_recipients=list(bcc_recipients or []),
        has_attachments=has_attachments,
        graph_folder=graph_folder,
        body_content_type=body_content_type,
        body_clean=body_clean,
        body_clean_version=body_clean_version,
        body_clean_computed_at=body_clean_computed_at,
        meeting_message_type=meeting_message_type,
        meeting_response_type=meeting_response_type,
        sender_name=sender_name,
        is_automated=is_automated,
    )
    upsert_stmt = insert_stmt.on_conflict_do_nothing(
        index_elements=["graph_message_id"],
    ).returning(Message)
    result = await session.execute(upsert_stmt)
    message = result.scalar_one_or_none()
    if message is not None:
        await session.flush()
        return MessageSchema.model_validate(message)

    existing = await get_by_graph_id(session, graph_message_id)
    if existing is None:
        raise RuntimeError(
            f"Message insert conflicted but row missing for graph_message_id={graph_message_id}"
        )
    incoming = body_text or ""
    if not (existing.body_text or "").strip() and incoming.strip():
        stmt = (
            update(Message)
            .where(Message.id == existing.id)
            .values(
                body_text=incoming,
                body_preview=body_preview,
                unique_body_text=unique_body_text,
                body_content_type=body_content_type,
                body_clean=body_clean,
                body_clean_version=body_clean_version,
                body_clean_computed_at=body_clean_computed_at,
                meeting_message_type=meeting_message_type,
                meeting_response_type=meeting_response_type,
            )
            .returning(Message)
        )
        updated = (await session.execute(stmt)).scalar_one()
        await session.flush()
        return MessageSchema.model_validate(updated)
    if meeting_message_type and not existing.meeting_message_type:
        stmt = (
            update(Message)
            .where(Message.id == existing.id)
            .values(
                meeting_message_type=meeting_message_type,
                meeting_response_type=meeting_response_type,
            )
            .returning(Message)
        )
        updated = (await session.execute(stmt)).scalar_one()
        await session.flush()
        return MessageSchema.model_validate(updated)
    fill: dict[str, object] = {}
    if sender_name and not existing.sender_name:
        fill["sender_name"] = sender_name
    if is_automated and not existing.is_automated:
        fill["is_automated"] = True
    if fill:
        stmt = update(Message).where(Message.id == existing.id).values(**fill).returning(Message)
        updated = (await session.execute(stmt)).scalar_one()
        await session.flush()
        return MessageSchema.model_validate(updated)
    return existing


async def update_body_clean(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    body_clean: str,
    body_clean_version: int,
    body_clean_computed_at: datetime,
) -> None:
    stmt = (
        update(Message)
        .where(Message.id == message_id)
        .values(
            body_clean=body_clean,
            body_clean_version=body_clean_version,
            body_clean_computed_at=body_clean_computed_at,
        )
    )
    await session.execute(stmt)


async def update_summary(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    summary_json: dict[str, Any],
    summary_one_line: str,
    summarized_at: datetime,
    summary_model: str,
    summary_clean_version: int,
) -> None:
    stmt = (
        update(Message)
        .where(Message.id == message_id)
        .values(
            summary_json=summary_json,
            summary_one_line=summary_one_line,
            summarized_at=summarized_at,
            summary_model=summary_model,
            summary_clean_version=summary_clean_version,
        )
    )
    await session.execute(stmt)
