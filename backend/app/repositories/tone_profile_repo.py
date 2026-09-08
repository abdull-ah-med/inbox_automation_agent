"""Tone profile repository — upsert distilled voice rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.tone_profile import ToneProfile
from app.models.schemas.tone_profile import ToneProfileResponseSchema, ToneProfileSchema
from app.repositories._vector_common import cap_limit
from app.repositories.memory_list_common import (
    mailbox_in_allowlist,
    mailbox_matches,
    normalize_mailbox_allowlist,
)


def _to_response(row: ToneProfile) -> ToneProfileResponseSchema:
    profile = ToneProfileSchema.model_validate(row.profile)
    return ToneProfileResponseSchema(
        id=row.id,
        mailbox=row.mailbox,
        routing_category=row.routing_category,
        profile=profile,
        sample_count=row.sample_count,
        version=row.version,
        built_at=row.built_at,
    )


async def get_profile(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
) -> ToneProfileResponseSchema | None:
    stmt = select(ToneProfile).where(
        ToneProfile.mailbox == mailbox,
        ToneProfile.routing_category == routing_category,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_response(row)


async def list_profiles(
    session: AsyncSession,
    *,
    mailbox: str | None = None,
    mailboxes: list[str] | None = None,
    limit: int = 100,
) -> list[ToneProfileResponseSchema]:
    capped = cap_limit(limit, maximum=500)
    stmt = select(ToneProfile).order_by(ToneProfile.built_at.desc()).limit(capped)
    if mailbox is not None:
        stmt = stmt.where(mailbox_matches(ToneProfile.mailbox, mailbox))
    elif mailboxes:
        if not normalize_mailbox_allowlist(mailboxes):
            return []
        stmt = stmt.where(mailbox_in_allowlist(ToneProfile.mailbox, mailboxes))
    result = await session.execute(stmt)
    return [_to_response(row) for row in result.scalars().all()]


async def upsert_profile(
    session: AsyncSession,
    *,
    mailbox: str,
    routing_category: str,
    profile: ToneProfileSchema,
    sample_count: int,
) -> ToneProfileResponseSchema:
    """Insert or update tone profile; bump version on update."""
    category = routing_category or "general"
    payload: dict[str, Any] = profile.model_dump()
    now = datetime.now(UTC)

    existing = await get_profile(
        session,
        mailbox=mailbox,
        routing_category=category,
    )
    next_version = (existing.version + 1) if existing is not None else 1

    stmt = (
        pg_insert(ToneProfile)
        .values(
            id=uuid.uuid4(),
            mailbox=mailbox,
            routing_category=category,
            profile=payload,
            sample_count=sample_count,
            version=next_version,
            built_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_tone_profiles_mailbox_category",
            set_={
                "profile": payload,
                "sample_count": sample_count,
                "version": next_version,
                "built_at": now,
            },
        )
        .returning(ToneProfile)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    await session.flush()
    return _to_response(row)
