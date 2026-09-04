"""Golden set case repository — insert and list hand-curated evaluation cases."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.golden_set_case import GoldenSetCase


class GoldenSetCaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    sender_domain: str | None = None
    email_text: str
    expected_urgency: str | None = None
    expected_action: str | None = None
    expected_associations: dict[str, Any] | None = None
    expected_draft_body_criteria: str | None = None
    created_at: datetime | None = None


async def insert_golden_case(
    session: AsyncSession,
    *,
    mailbox: str,
    email_text: str,
    expected_urgency: str | None = None,
    expected_action: str | None = None,
    expected_associations: dict[str, Any] | None = None,
    expected_draft_body_criteria: str | None = None,
    sender_domain: str | None = None,
) -> GoldenSetCaseSchema:
    case = GoldenSetCase(
        mailbox=mailbox,
        email_text=email_text,
        sender_domain=sender_domain,
        expected_urgency=expected_urgency,
        expected_action=expected_action,
        expected_associations=expected_associations,
        expected_draft_body_criteria=expected_draft_body_criteria,
    )
    session.add(case)
    await session.flush()
    return GoldenSetCaseSchema.model_validate(case)


async def list_golden_cases_by_mailbox(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = 200,
) -> list[GoldenSetCaseSchema]:
    stmt = (
        select(GoldenSetCase)
        .where(GoldenSetCase.mailbox == mailbox)
        .order_by(GoldenSetCase.created_at.desc())
        .limit(max(1, min(limit, 1000)))
    )
    result = await session.execute(stmt)
    return [GoldenSetCaseSchema.model_validate(row) for row in result.scalars().all()]
