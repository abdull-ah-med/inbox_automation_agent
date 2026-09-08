"""Skill candidate repository — pending proposals from reject clusters."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.skill_candidate import SkillCandidate
from app.models.schemas.skill_candidate import SkillCandidateResponseSchema
from app.repositories._vector_common import cap_limit
from app.repositories.memory_list_common import (
    mailbox_in_allowlist,
    mailbox_matches,
    normalize_mailbox_allowlist,
)


def _to_response(row: SkillCandidate) -> SkillCandidateResponseSchema:
    raw_ids = row.source_rejection_ids or []
    ids: list[uuid.UUID] = []
    for item in raw_ids:
        if isinstance(item, uuid.UUID):
            ids.append(item)
        else:
            ids.append(uuid.UUID(str(item)))
    return SkillCandidateResponseSchema(
        id=row.id,
        mailbox=row.mailbox,
        routing_category=row.routing_category,
        reason_code=row.reason_code,
        proposed_name=row.proposed_name,
        proposed_content=row.proposed_content,
        source_rejection_ids=ids,
        status=row.status,
        created_at=row.created_at,
    )


async def list_candidates(
    session: AsyncSession,
    *,
    status: str | None = "pending",
    mailbox: str | None = None,
    mailboxes: list[str] | None = None,
    limit: int = 100,
) -> list[SkillCandidateResponseSchema]:
    capped = cap_limit(limit, maximum=500)
    stmt = select(SkillCandidate).order_by(SkillCandidate.created_at.desc()).limit(capped)
    if status is not None:
        stmt = stmt.where(SkillCandidate.status == status)
    if mailbox is not None:
        stmt = stmt.where(mailbox_matches(SkillCandidate.mailbox, mailbox))
    elif mailboxes:
        if not normalize_mailbox_allowlist(mailboxes):
            return []
        stmt = stmt.where(mailbox_in_allowlist(SkillCandidate.mailbox, mailboxes))
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def get_by_id(
    session: AsyncSession,
    candidate_id: uuid.UUID,
) -> SkillCandidateResponseSchema | None:
    stmt = select(SkillCandidate).where(SkillCandidate.id == candidate_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def has_open_for_bucket(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
) -> bool:
    """True if a pending or accepted candidate already exists for this triple."""
    stmt = (
        select(SkillCandidate.id)
        .where(
            SkillCandidate.mailbox == mailbox,
            SkillCandidate.routing_category == routing_category,
            SkillCandidate.reason_code == reason_code,
            SkillCandidate.status.in_(("pending", "accepted")),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def create_candidate(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    reason_code: str,
    proposed_name: str,
    proposed_content: str,
    source_rejection_ids: list[uuid.UUID],
) -> SkillCandidateResponseSchema:
    row = SkillCandidate(
        mailbox=mailbox,
        routing_category=routing_category,
        reason_code=reason_code,
        proposed_name=proposed_name,
        proposed_content=proposed_content,
        source_rejection_ids=[str(i) for i in source_rejection_ids],
        status="pending",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_response(row)


async def set_status(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    status: str,
) -> SkillCandidateResponseSchema | None:
    stmt = (
        sa_update(SkillCandidate)
        .where(SkillCandidate.id == candidate_id)
        .values(status=status)
        .returning(SkillCandidate)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    await session.flush()
    return _to_response(row)
