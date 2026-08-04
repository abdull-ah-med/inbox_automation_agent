"""Unit tests for rejection_memory_service."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.repositories.rejection_memory_repo import RejectionMemorySchema, format_constraint_line
from app.services import rejection_memory_service


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def test_format_constraint_line_truncates() -> None:
    long_note = "x" * 300
    line = format_constraint_line(reason_code="tone", note=long_note)
    assert line.startswith("[tone] ")
    assert line.endswith("…")
    assert len(line) <= 240 + len("[tone] ")


@pytest.mark.asyncio
async def test_store_rejection_skips_without_openai() -> None:
    session = AsyncMock()
    settings = _settings()
    settings.openai_api_key = ""
    result = await rejection_memory_service.store_rejection(
        session,
        openai_client=None,
        settings=settings,
        draft_id=uuid.uuid4(),
        mailbox="elise@example.com",
        routing_category="billing",
        reason_code="tone",
        note="Too curt",
    )
    assert result is None


@pytest.mark.asyncio
async def test_find_negative_constraints_uses_recent_when_few() -> None:
    session = AsyncMock()
    settings = _settings()
    rows = [
        RejectionMemorySchema(
            id=uuid.uuid4(),
            draft_id=uuid.uuid4(),
            mailbox="elise@example.com",
            routing_category="billing",
            reason_code="tone",
            note="Too curt",
        )
    ]
    with patch(
        "app.services.rejection_memory_service.rejection_memory_repo.list_recent_for_category",
        AsyncMock(return_value=rows),
    ):
        constraints = await rejection_memory_service.find_negative_constraints(
            session,
            openai_client=MagicMock(),
            settings=settings,
            email_text="Invoice overdue",
            mailbox="elise@example.com",
            routing_category="billing",
            limit=3,
        )
    assert constraints == ["[tone] Too curt"]


@pytest.mark.asyncio
async def test_find_negative_constraints_empty_without_openai() -> None:
    session = AsyncMock()
    settings = _settings()
    settings.openai_api_key = ""
    constraints = await rejection_memory_service.find_negative_constraints(
        session,
        openai_client=None,
        settings=settings,
        email_text="hello",
        mailbox="elise@example.com",
        routing_category="general",
    )
    assert constraints == []
