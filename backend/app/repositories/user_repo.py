"""User repository — async CRUD returning Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.user import User


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    password_hash: str
    password_updated_at: datetime
    token_version: int
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


async def get_by_email(session: AsyncSession, email: str) -> UserRead | None:
    normalized = email.strip().lower()
    stmt = select(User).where(User.email == normalized)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return UserRead.model_validate(row)


async def get_by_id(session: AsyncSession, user_id: uuid.UUID) -> UserRead | None:
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return UserRead.model_validate(row)


async def create(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str,
    role: str = "user",
) -> UserRead:
    user = User(
        email=email.strip().lower(),
        password_hash=password_hash,
        role=role,
        is_active=True,
        token_version=0,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return UserRead.model_validate(user)


async def update_password(
    session: AsyncSession,
    user_id: uuid.UUID,
    password_hash: str,
) -> None:
    now = datetime.now(UTC)
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(
            password_hash=password_hash,
            password_updated_at=now,
            updated_at=now,
            token_version=User.token_version + 1,
        )
    )
    await session.execute(stmt)


async def bump_token_version(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Increment token_version; return the new value."""
    now = datetime.now(UTC)
    stmt = (
        update(User)
        .where(User.id == user_id)
        .values(
            token_version=User.token_version + 1,
            updated_at=now,
        )
        .returning(User.token_version)
    )
    result = await session.execute(stmt)
    value = result.scalar_one()
    return int(value)
