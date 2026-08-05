"""End-to-end skill import → selection → draft tool loop (mocked LLM)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.llm import draft_generator as draft_llm
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.skill import ImportSkillResultSchema
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_archive_service, skill_selection_service

FIXTURE_ZIP = (
    Path(__file__).resolve().parents[2] / "misc" / "samplelab-rebilling-skill.zip"
)


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        anthropic_api_key="test-key",
        draft_model="claude-sonnet-4-6",
        target_mailboxes="elise@example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.mark.asyncio
async def test_import_fixture_then_draft_reads_client_rules(
    local_settings: Settings,
) -> None:
    """Upload real zip; Haiku selects rebilling skill; Sonnet loads client_rules."""
    skill_id = uuid.uuid4()
    import_result = ImportSkillResultSchema(
        skill_id=skill_id,
        name="samplelab-rebilling",
        description="Handle SampleLab rebilling for Harmeyer departments",
        reference_files=[
            "references/client_rules.md",
            "references/output_format.md",
            "references/harmeyer_departments.csv",
        ],
        asset_files=[],
        warnings=[],
        overwritten=False,
    )

    selection_row = SkillSelectionRow(
        id=skill_id,
        name="samplelab-rebilling",
        description="Handle SampleLab rebilling for Harmeyer departments",
        content="Use client rules when rebilling SampleLab invoices.",
        category="billing",
        always_apply=False,
        is_active=True,
        embedding=None,
        reference_manifest=[
            "references/client_rules.md",
            "references/output_format.md",
            "references/harmeyer_departments.csv",
        ],
        has_assets=False,
    )

    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
        patch(
            "app.api.web.skills.skill_archive_service.import_skill_archive",
            AsyncMock(return_value=import_result),
        ) as import_mock,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.uuid4(),
                email="elise@example.com",
                role="admin",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db

        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills/import",
                files={
                    "file": (
                        "samplelab-rebilling-skill.zip",
                        FIXTURE_ZIP.read_bytes(),
                        "application/zip",
                    )
                },
            )
        application.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["name"] == "samplelab-rebilling"
    import_mock.assert_awaited()
    # Also parse the real archive locally to prove fixture validity
    name, _desc, body, _extras, files, warnings = skill_archive_service.parse_skill_archive(
        FIXTURE_ZIP.read_bytes()
    )
    assert name == "samplelab-rebilling"
    assert warnings == []
    assert any(f["relative_path"] == "references/client_rules.md" for f in files)

    email = EmailMessageSchema(
        message_id="m-bill-1",
        conversation_id="c-bill-1",
        mailbox="elise@example.com",
        sender="client@example.com",
        subject="SampleLab invoice rebilling for Harmeyer",
        body_text="Please rebill this SampleLab invoice to the correct Harmeyer department.",
        body_preview="Please rebill",
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        needs_context=False,
        routing_category="billing",
        action_items_summary="Rebill SampleLab invoice",
    )

    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[selection_row]),
        ),
        patch(
            "app.services.skill_selection_service.skill_selector.select_skills",
            AsyncMock(return_value=[skill_id]),
        ),
        patch(
            "app.services.skill_selection_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        selected = await skill_selection_service.select_skills(
            AsyncMock(),
            client=MagicMock(),
            settings=local_settings,
            openai_client=None,
            email=email,
            triage=triage,
            conversation_id=email.conversation_id,
        )

    assert skill_id in selected.skill_ids
    assert any("samplelab-rebilling" in block for block in selected.blocks)
    assert any("references/client_rules.md" in block for block in selected.blocks)

    # Draft tool loop loads client_rules and final body mentions billing terms
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "tu1"
    tool_block.name = "read_skill_reference"
    tool_block.input = {
        "skill_id": str(skill_id),
        "path": "references/client_rules.md",
    }
    tool_resp = MagicMock()
    tool_resp.stop_reason = "tool_use"
    tool_resp.content = [tool_block]
    tool_resp.usage = MagicMock(input_tokens=10, output_tokens=5)

    end_resp = MagicMock()
    end_resp.stop_reason = "end_turn"
    end_resp.content = [MagicMock(type="text", text="ok")]
    end_resp.usage = MagicMock(input_tokens=8, output_tokens=3)

    parsed = MagicMock()
    parsed.parsed_output = DraftSchema(
        subject_line="Re: SampleLab invoice rebilling for Harmeyer",
        reply_body=(
            "We will rebill the SampleLab invoice to the correct Harmeyer "
            "department per client billing rules."
        ),
        suggested_recipients=[],
        forward_to=None,
        teaching_note="Billing rebilling request; applied SampleLab skill.",
        urgency="NORMAL",
        urgency_reason="Routine billing correction.",
    )
    parsed.usage = MagicMock(input_tokens=20, output_tokens=40)

    async def loader(sid: uuid.UUID, path: str) -> dict:
        assert sid == skill_id
        assert path == "references/client_rules.md"
        return {
            "content": "# Client rules\nAlways confirm Harmeyer department codes.",
            "bytes": 60,
            "is_error": False,
        }

    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[tool_resp, end_resp])
    client.messages.parse = AsyncMock(return_value=parsed)

    result = await draft_llm.generate_draft(
        email,
        ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage,
        client=client,
        settings=local_settings,
        skills=selected.blocks,
        reference_loader=loader,
    )

    assert result.tool_calls
    assert result.tool_calls[0]["path"] == "references/client_rules.md"
    lowered = result.draft.reply_body.lower()
    assert "billing" in lowered or "rebill" in lowered or "samplelab" in lowered
    assert "harmeyer" in lowered
    # Prove SKILL.md body from fixture mentions billing vocabulary used in draft path
    assert "bill" in body.lower() or "rebil" in body.lower()
