"""Unit tests for skill_selection_service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import ClassificationError
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_selection_service


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        anthropic_api_key="sk-ant",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="client@example.com",
        subject="Invoice question",
        body_text="Please clarify the invoice.",
        body_preview="Please clarify",
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
        has_attachments=False,
    )


def _row(
    *,
    name: str,
    always_apply: bool = False,
    category: str | None = "billing",
    embedding: list[float] | None = None,
) -> SkillSelectionRow:
    return SkillSelectionRow(
        id=uuid.uuid4(),
        name=name,
        description=f"desc {name}",
        content=f"content for {name}",
        category=category,
        always_apply=always_apply,
        is_active=True,
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_select_always_apply_only_when_pool_empty() -> None:
    session = AsyncMock()
    always = _row(name="Core", always_apply=True, category=None)
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[always]),
        ),
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        contents = await skill_selection_service.select_skill_contents(
            session,
            client=MagicMock(),
            settings=_settings(),
            openai_client=None,
            email=_email(),
            triage=TriageResultSchema(
                is_spam=False,
                has_action_items=True,
                needs_context=False,
                routing_category="billing",
            ),
            conversation_id="c1",
        )
    assert len(contents) == 1
    assert "Core" in contents[0]


@pytest.mark.asyncio
async def test_select_haiku_failure_falls_back_to_always() -> None:
    session = AsyncMock()
    always = _row(name="Core", always_apply=True, category=None)
    pool = _row(name="Billing", always_apply=False, category="billing")
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[always, pool]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(side_effect=ClassificationError("fail")),
        ),
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        contents = await skill_selection_service.select_skill_contents(
            session,
            client=MagicMock(),
            settings=_settings(),
            openai_client=None,
            email=_email(),
            triage=TriageResultSchema(
                is_spam=False,
                has_action_items=True,
                needs_context=False,
                routing_category="billing",
            ),
            conversation_id="c1",
        )
    assert len(contents) == 1
    assert "Core" in contents[0]


@pytest.mark.asyncio
async def test_select_filters_invalid_ids() -> None:
    session = AsyncMock()
    always = _row(name="Core", always_apply=True, category=None)
    pool = _row(name="Billing", always_apply=False, category="billing")
    bogus = uuid.uuid4()
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[always, pool]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(return_value=[pool.id, bogus]),
        ),
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        contents = await skill_selection_service.select_skill_contents(
            session,
            client=MagicMock(),
            settings=_settings(),
            openai_client=None,
            email=_email(),
            triage=TriageResultSchema(
                is_spam=False,
                has_action_items=True,
                needs_context=False,
                routing_category="billing",
            ),
            conversation_id="c1",
        )
    assert len(contents) == 2
    joined = "\n".join(contents)
    assert "Core" in joined
    assert "Billing" in joined
