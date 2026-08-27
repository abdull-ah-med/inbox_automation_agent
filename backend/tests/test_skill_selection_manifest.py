"""Unit tests for skill selection manifest formatting and long-body pool keep."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_selection_service
from app.services.skill_selection_service import format_skill_block


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        anthropic_api_key="sk-ant",
        openai_api_key="sk-test",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="client@example.com",
        subject="Invoice rebilling question",
        body_text="Need to rebill the SampleLab invoice for Harmeyer.",
        body_preview="Need to rebill",
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
        has_attachments=False,
    )


def _row(
    *,
    name: str,
    content: str = "short",
    embedding: list[float] | None = None,
    refs: list[str] | None = None,
) -> SkillSelectionRow:
    return SkillSelectionRow(
        id=uuid.uuid4(),
        name=name,
        description=f"desc {name}",
        content=content,
        category="billing",
        always_apply=False,
        is_active=True,
        embedding=embedding,
        reference_manifest=refs or [],
        has_assets=False,
    )


def test_format_skill_block_omits_manifest_when_empty() -> None:
    block = format_skill_block(_row(name="plain"))
    assert "## Skill: plain" in block
    assert "Available reference files" not in block
    assert "read_skill_reference" not in block


def test_format_skill_block_includes_manifest_when_present() -> None:
    skill = _row(
        name="samplelab-rebilling",
        refs=["references/client_rules.md", "references/output_format.md"],
    )
    block = format_skill_block(skill)
    assert "### Available reference files" in block
    assert "references/client_rules.md" in block
    assert "read_skill_reference" in block
    assert f"id: {skill.id}" in block


@pytest.mark.asyncio
async def test_long_body_skill_stays_in_haiku_candidate_pool() -> None:
    """Embedding narrow keeps a long-body skill that ranks high by cosine."""
    long_body = "billing rebilling invoice department\n" + ("x" * 20_000)
    target = _row(
        name="samplelab-rebilling",
        content=long_body,
        embedding=[1.0, 0.0, 0.0],
        refs=["references/client_rules.md"],
    )
    fillers = [_row(name=f"filler-{i}", embedding=[0.0, 1.0, 0.0]) for i in range(9)]
    pool = [target, *fillers]
    assert len(pool) > 8

    session = AsyncMock()
    with (
        patch(
            "app.services.skill_selection_service.embedding_service.embed_text",
            AsyncMock(return_value=[1.0, 0.0, 0.0]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(return_value=[target.id]),
        ),
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=pool),
        ),
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        selected = await skill_selection_service.select_skills(
            session,
            client=MagicMock(),
            settings=_settings(),
            openai_client=MagicMock(),
            email=_email(),
            triage=TriageResultSchema(
                is_spam=False,
                has_action_items=True,
                needs_context=False,
                routing_category="billing",
            ),
            conversation_id="c1",
        )

    assert target.id in selected.skill_ids
    assert any("samplelab-rebilling" in block for block in selected.blocks)
    assert any("Available reference files" in block for block in selected.blocks)
