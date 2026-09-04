"""Urgency prediction repository — log full probability vectors at triage time."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.urgency_prediction import UrgencyPrediction


class UrgencyPredictionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    draft_id: uuid.UUID
    thread_id: uuid.UUID
    mailbox: str
    routing_category: str | None = None
    sender_domain: str
    predicted_urgency: str
    probs: dict[str, Any]
    alert_fingerprint: str | None = None
    applied_rule_ids: list[uuid.UUID] | None = None
    final_urgency: str
    elise_edited_to: str | None = None
    created_at: datetime | None = None


async def insert_urgency_prediction(
    session: AsyncSession,
    *,
    draft_id: uuid.UUID,
    thread_id: uuid.UUID,
    mailbox: str,
    sender_domain: str,
    predicted_urgency: str,
    probs: dict[str, Any],
    final_urgency: str,
    routing_category: str | None = None,
    alert_fingerprint: str | None = None,
    applied_rule_ids: list[uuid.UUID] | None = None,
    elise_edited_to: str | None = None,
) -> UrgencyPredictionSchema:
    stmt = (
        pg_insert(UrgencyPrediction)
        .values(
            draft_id=draft_id,
            thread_id=thread_id,
            mailbox=mailbox,
            routing_category=routing_category,
            sender_domain=sender_domain,
            predicted_urgency=predicted_urgency,
            probs=probs,
            alert_fingerprint=alert_fingerprint,
            applied_rule_ids=applied_rule_ids,
            final_urgency=final_urgency,
            elise_edited_to=elise_edited_to,
        )
        .on_conflict_do_nothing(index_elements=["draft_id"])
        .returning(UrgencyPrediction)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        await session.flush()
        return UrgencyPredictionSchema.model_validate(row)

    existing = (
        await session.execute(
            select(UrgencyPrediction).where(UrgencyPrediction.draft_id == draft_id)
        )
    ).scalar_one()
    return UrgencyPredictionSchema.model_validate(existing)


async def get_urgency_prediction_by_draft_id(
    session: AsyncSession,
    draft_id: uuid.UUID,
) -> UrgencyPredictionSchema | None:
    stmt = select(UrgencyPrediction).where(UrgencyPrediction.draft_id == draft_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return UrgencyPredictionSchema.model_validate(row)


async def list_recent_for_mailbox(
    session: AsyncSession,
    mailbox: str,
    *,
    limit: int = 200,
) -> list[UrgencyPredictionSchema]:
    stmt = (
        select(UrgencyPrediction)
        .where(UrgencyPrediction.mailbox == mailbox)
        .order_by(UrgencyPrediction.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    result = await session.execute(stmt)
    return [UrgencyPredictionSchema.model_validate(row) for row in result.scalars().all()]


async def set_elise_edited_to(
    session: AsyncSession,
    draft_id: uuid.UUID,
    edited_to: str,
) -> None:
    """Record Elise's later urgency edit on the prediction row for *draft_id*."""
    await session.execute(
        sa_update(UrgencyPrediction)
        .where(UrgencyPrediction.draft_id == draft_id)
        .values(elise_edited_to=edited_to)
    )
