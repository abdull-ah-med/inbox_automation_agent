"""Refresh token repository — hashed tokens only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.refresh_token import RefreshToken


class RefreshTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str
    family_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    replaced_by_id: uuid.UUID | None
    user_agent: str | None
    ip: str | None

    @field_validator("ip", mode="before")
    @classmethod
    def _ip_to_str(cls, value: object) -> str | None:
        # asyncpg returns ipaddress.IPv4Address/IPv6Address for INET columns.
        if value is None:
            return None
        return str(value)


async def insert(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    token_hash: str,
    family_id: uuid.UUID,
    expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> RefreshTokenRead:
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        family_id=family_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return RefreshTokenRead.model_validate(row)


async def get_by_hash(session: AsyncSession, token_hash: str) -> RefreshTokenRead | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return RefreshTokenRead.model_validate(row)


async def get_by_hash_for_update(
    session: AsyncSession,
    token_hash: str,
) -> RefreshTokenRead | None:
    """Load a refresh row with ``SELECT … FOR UPDATE`` to serialize rotation."""
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return RefreshTokenRead.model_validate(row)


async def revoke(
    session: AsyncSession,
    token_id: uuid.UUID,
    *,
    replaced_by_id: uuid.UUID | None = None,
) -> bool:
    """Revoke an active token. Returns True if a row was updated."""
    stmt = (
        update(RefreshToken)
        .where(
            RefreshToken.id == token_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(
            revoked_at=datetime.now(UTC),
            replaced_by_id=replaced_by_id,
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0) == 1  # type: ignore[attr-defined]


async def revoke_family(session: AsyncSession, family_id: uuid.UUID) -> int:
    stmt = (
        update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def revoke_all_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    stmt = (
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def purge_expired(session: AsyncSession, before: datetime) -> int:
    from sqlalchemy import delete

    stmt = delete(RefreshToken).where(RefreshToken.expires_at < before)
    result = await session.execute(stmt)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
