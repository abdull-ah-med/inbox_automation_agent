"""Urgency rule repository — Snorkel-style labelling functions evaluated post-model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.urgency_rule import UrgencyRule


class UrgencyRuleSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mailbox: str
    scope: str
    scope_key: str
    condition: dict[str, Any]
    action: dict[str, Any]
    status: str
    canary_until: datetime | None = None
    activated_at: datetime | None = None
    paused_at: datetime | None = None
    impact_num: int | None = None
    impact_den: int | None = None
    precision_num: int | None = None
    precision_den: int | None = None
    hit_count: int
    override_count: int
    person_bound: bool
    previous_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


async def create_urgency_rule(
    session: AsyncSession,
    *,
    mailbox: str,
    scope: str,
    scope_key: str,
    condition: dict[str, Any],
    action: dict[str, Any],
    status: str = "canary",
    canary_until: datetime | None = None,
    impact_num: int | None = None,
    impact_den: int | None = None,
    precision_num: int | None = None,
    precision_den: int | None = None,
    person_bound: bool = False,
) -> UrgencyRuleSchema:
    rule = UrgencyRule(
        mailbox=mailbox,
        scope=scope,
        scope_key=scope_key,
        condition=condition,
        action=action,
        status=status,
        canary_until=canary_until,
        impact_num=impact_num,
        impact_den=impact_den,
        precision_num=precision_num,
        precision_den=precision_den,
        person_bound=person_bound,
    )
    session.add(rule)
    await session.flush()
    return UrgencyRuleSchema.model_validate(rule)


async def list_urgency_rules_by_mailbox_status(
    session: AsyncSession,
    *,
    mailbox: str,
    status: str | None = None,
    limit: int = 100,
) -> list[UrgencyRuleSchema]:
    stmt = (
        select(UrgencyRule)
        .where(UrgencyRule.mailbox == mailbox)
        .order_by(UrgencyRule.created_at.desc())
        .limit(max(1, min(limit, 500)))
    )
    if status is not None:
        stmt = stmt.where(UrgencyRule.status == status)
    result = await session.execute(stmt)
    return [UrgencyRuleSchema.model_validate(row) for row in result.scalars().all()]


LIVE_STATUSES = ("active", "canary")
LIVE_RULE_CAP = 2000


async def list_live_urgency_rules(
    session: AsyncSession,
    *,
    mailbox: str,
    limit: int = LIVE_RULE_CAP,
) -> list[UrgencyRuleSchema]:
    """Active + canary rules only, oldest first, independent of archived volume."""
    stmt = (
        select(UrgencyRule)
        .where(
            UrgencyRule.mailbox == mailbox,
            UrgencyRule.status.in_(LIVE_STATUSES),
        )
        .order_by(UrgencyRule.created_at.asc())
        .limit(max(1, min(limit, LIVE_RULE_CAP)))
    )
    result = await session.execute(stmt)
    return [UrgencyRuleSchema.model_validate(row) for row in result.scalars().all()]


async def get_urgency_rule_by_id(
    session: AsyncSession,
    rule_id: uuid.UUID,
) -> UrgencyRuleSchema | None:
    stmt = select(UrgencyRule).where(UrgencyRule.id == rule_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return UrgencyRuleSchema.model_validate(row)


async def set_urgency_rule_status(
    session: AsyncSession,
    rule_id: uuid.UUID,
    status: str,
    extra: dict | None = None,
) -> UrgencyRuleSchema | None:
    from sqlalchemy import update as sa_update

    values: dict[str, object] = {"status": status}
    if extra:
        values.update(extra)
    stmt = (
        sa_update(UrgencyRule)
        .where(UrgencyRule.id == rule_id)
        .values(**values)
        .returning(UrgencyRule)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return UrgencyRuleSchema.model_validate(row)


async def increment_hit_count(session: AsyncSession, rule_id: uuid.UUID) -> None:
    await increment_hit_counts(session, [rule_id])


async def increment_hit_counts(session: AsyncSession, rule_ids: list[uuid.UUID]) -> None:
    if not rule_ids:
        return
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(UrgencyRule)
        .where(UrgencyRule.id.in_(rule_ids))
        .values(hit_count=UrgencyRule.hit_count + 1)
    )
    await session.execute(stmt)


async def increment_override_count(session: AsyncSession, rule_id: uuid.UUID) -> None:
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(UrgencyRule)
        .where(UrgencyRule.id == rule_id)
        .values(override_count=UrgencyRule.override_count + 1)
    )
    await session.execute(stmt)
