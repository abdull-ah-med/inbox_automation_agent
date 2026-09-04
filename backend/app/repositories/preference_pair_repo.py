"""Preference pair repository — store paired approve/reject decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.preference_pair import PreferencePair


class PreferencePairSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID
    mailbox: str
    sender_address: str
    sender_domain: str
    routing_category: str
    email_text_hash: str
    chosen_body: str | None = None
    rejected_body: str | None = None
    decision: str
    created_at: datetime | None = None


async def insert_preference_pair(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    thread_id: uuid.UUID,
    mailbox: str,
    sender_address: str,
    sender_domain: str,
    routing_category: str,
    email_text_hash: str,
    email_embedding: list[float],
    decision: str,
    chosen_body: str | None = None,
    rejected_body: str | None = None,
) -> PreferencePairSchema:
    stmt = (
        pg_insert(PreferencePair)
        .values(
            draft_id=draft_id,
            thread_id=thread_id,
            mailbox=mailbox,
            sender_address=sender_address,
            sender_domain=sender_domain,
            routing_category=routing_category,
            email_text_hash=email_text_hash,
            email_embedding=email_embedding,
            decision=decision,
            chosen_body=chosen_body,
            rejected_body=rejected_body,
        )
        .on_conflict_do_nothing(index_elements=["draft_id"])
        .returning(PreferencePair)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return PreferencePairSchema.model_validate(row)

    existing = (
        await session.execute(select(PreferencePair).where(PreferencePair.draft_id == draft_id))
    ).scalar_one()
    return PreferencePairSchema.model_validate(existing)


async def get_by_draft_id(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> PreferencePairSchema | None:
    stmt = select(PreferencePair).where(PreferencePair.draft_id == draft_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return PreferencePairSchema.model_validate(row)


async def list_by_mailbox(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 50,
) -> list[PreferencePairSchema]:
    stmt = (
        select(PreferencePair)
        .where(PreferencePair.mailbox == mailbox)
        .order_by(PreferencePair.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    result = await session.execute(stmt)
    return [PreferencePairSchema.model_validate(row) for row in result.scalars().all()]
