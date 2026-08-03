"""Unit tests for tone_profile_service draft assembly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.tone_profile import ToneProfileResponseSchema, ToneProfileSchema
from app.services import tone_profile_service


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


def _profile() -> ToneProfileResponseSchema:
    return ToneProfileResponseSchema(
        id=uuid.uuid4(),
        mailbox="elise@example.com",
        routing_category="billing",
        profile=ToneProfileSchema(
            formality="professional",
            greeting_pattern="Hi,",
            sign_off_pattern="Thanks,",
            typical_length="short",
            favored_phrases=["please"],
            avoided_phrases=["asap"],
            behavioral_rules=["Open with the answer"],
        ),
        sample_count=12,
        version=2,
        built_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_load_for_draft_uses_profile_and_recent() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.tone_profile_service.tone_profile_repo.get_profile",
            AsyncMock(return_value=_profile()),
        ),
        patch(
            "app.services.tone_profile_service.draft_repo.list_recent_approved_bodies",
            AsyncMock(return_value=["Recent approved reply"]),
        ),
    ):
        profile_block, examples = await tone_profile_service.load_for_draft(
            session,
            openai_client=None,
            settings=_settings(),
            mailbox="elise@example.com",
            routing_category="billing",
            email_text="invoice",
        )
    assert profile_block is not None
    assert "professional" in profile_block
    assert examples == ["Recent approved reply"]


@pytest.mark.asyncio
async def test_load_for_draft_cold_start_semantic() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.tone_profile_service.tone_profile_repo.get_profile",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.tone_profile_service.reply_memory_service.find_similar_replies",
            AsyncMock(return_value=["Semantic match"]),
        ),
    ):
        profile_block, examples = await tone_profile_service.load_for_draft(
            session,
            openai_client=AsyncMock(),
            settings=_settings(),
            mailbox="elise@example.com",
            routing_category="billing",
            email_text="invoice",
        )
    assert profile_block is None
    assert examples == ["Semantic match"]
