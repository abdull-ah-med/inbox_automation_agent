"""Message repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.message import Message


class MessageSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    graph_message_id: str
    direction: str
    sender: str
    body_text: str
    body_preview: str | None = None
    body_content_type: str = "text"
    body_clean: str | None = None
    body_clean_version: int | None = None
    body_clean_computed_at: datetime | None = None
    received_at: datetime
    to_recipients: list[str] = Field(default_factory=list)
    cc_recipients: list[str] = Field(default_factory=list)
    has_attachments: bool = False
    graph_folder: str | None = None
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
) -> MessageSchema | None:
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


async def create_message(
    session: AsyncSession,
    *,
    thread_id: uuid.UUID,
    graph_message_id: str,
    direction: str,
    sender: str,
    body_text: str,
    body_preview: str | None,
    received_at: datetime,
    to_recipients: list[str] | None = None,
    cc_recipients: list[str] | None = None,
    has_attachments: bool = False,
    graph_folder: str | None = None,
    body_content_type: str = "text",
    body_clean: str | None = None,
    body_clean_version: int | None = None,
    body_clean_computed_at: datetime | None = None,
) -> MessageSchema:
    """Insert a message or return the existing row on ``graph_message_id`` conflict."""
    insert_stmt = insert(Message).values(
        thread_id=thread_id,
        graph_message_id=graph_message_id,
        direction=direction,
        sender=sender,
        body_text=body_text,
        body_preview=body_preview,
        received_at=received_at,
        to_recipients=list(to_recipients or []),
        cc_recipients=list(cc_recipients or []),
        has_attachments=has_attachments,
        graph_folder=graph_folder,
        body_content_type=body_content_type,
        body_clean=body_clean,
        body_clean_version=body_clean_version,
        body_clean_computed_at=body_clean_computed_at,
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
