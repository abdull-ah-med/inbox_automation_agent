"""Tests for applied-skills snapshot on drafts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import AppliedSkillSchema, DraftResponseSchema, DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.repositories import draft_repo
from app.repositories.skill_repo import SkillSelectionRow
from app.services import skill_selection_service, thread_view_service


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="client@example.com",
        subject="Process SampleLab invoice",
        body_text="Please rebill Harmeyer.",
        body_preview="Please rebill",
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )


@pytest.mark.asyncio
async def test_select_skills_includes_applied_names() -> None:
    skill_id = uuid.uuid4()
    row = SkillSelectionRow(
        id=skill_id,
        name="samplelab-rebilling",
        description="Rebill SampleLab",
        content="Use client rules.",
        category="billing",
        always_apply=False,
        is_active=True,
    )
    with (
        patch(
            "app.services.skill_selection_service.skill_repo.list_active_for_selection",
            AsyncMock(return_value=[row]),
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
            settings=MagicMock(openai_api_key=""),
            openai_client=None,
            email=_email(),
            triage=TriageResultSchema(
                is_spam=False,
                has_action_items=True,
                needs_context=False,
                routing_category="billing",
            ),
        )
    assert selected.skill_ids == [skill_id]
    assert len(selected.applied) == 1
    assert selected.applied[0].id == skill_id
    assert selected.applied[0].name == "samplelab-rebilling"


def test_applied_skills_payload_and_parse_round_trip() -> None:
    skill_id = uuid.uuid4()
    payload = draft_repo._applied_skills_payload(
        [AppliedSkillSchema(id=skill_id, name="samplelab-rebilling")]
    )
    assert payload == [{"id": str(skill_id), "name": "samplelab-rebilling"}]
    parsed = draft_repo._parse_applied_skills(payload)
    assert parsed[0].id == skill_id
    assert parsed[0].name == "samplelab-rebilling"


def test_parse_tool_calls_filters_invalid() -> None:
    parsed = draft_repo._parse_tool_calls(
        [
            {"skill_id": "abc", "path": "references/a.md", "is_error": False},
            {"skill_id": 123, "path": "bad"},
            "nope",
        ]
    )
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0].path == "references/a.md"


def test_draft_response_to_view_includes_skills_and_tool_calls() -> None:
    skill_id = uuid.uuid4()
    draft = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        created_at=datetime.now(UTC),
        subject_line="Re: x",
        reply_body="Hello",
        teaching_note="note",
        urgency="NORMAL",
        urgency_reason="routine",
        applied_skills=[AppliedSkillSchema(id=skill_id, name="samplelab-rebilling")],
        tool_calls=[
            {
                "skill_id": str(skill_id),
                "path": "references/client_rules.md",
                "is_error": False,
            }
        ],
    )
    view = thread_view_service.draft_response_to_view(draft)
    assert len(view.applied_skills) == 1
    assert view.applied_skills[0].name == "samplelab-rebilling"
    assert view.tool_calls is not None
    assert view.tool_calls[0].path == "references/client_rules.md"


@pytest.mark.asyncio
async def test_create_draft_accepts_applied_skills() -> None:
    session = AsyncMock()
    skill_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    thread_id = uuid.uuid4()

    row = MagicMock()
    row.id = draft_id
    row.thread_id = thread_id
    row.created_at = datetime.now(UTC)
    row.approved_at = None
    row.rejected_at = None
    row.edited_body = None
    row.subject = "Re: x"
    row.body = "Hello"
    row.recipients = {"suggested_recipients": [], "forward_to": None}
    row.teaching_note = "note"
    row.urgency = "NORMAL"
    row.urgency_reason = "routine"
    row.context_match_confidence = None
    row.feedback_note = None
    row.feedback_action = None
    row.feedback_reason_code = None
    row.routing_category = "billing"
    row.suggested_actions = []
    row.applied_skills_json = [{"id": str(skill_id), "name": "samplelab-rebilling"}]
    row.tool_calls_json = [
        {"skill_id": str(skill_id), "path": "references/client_rules.md", "is_error": False}
    ]

    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()

    persisted = await draft_repo.create_draft(
        session,
        thread_id=thread_id,
        message_id="m-new",
        draft=DraftSchema(
            subject_line="Re: x",
            reply_body="Hello",
            teaching_note="note",
            urgency="NORMAL",
            urgency_reason="routine",
        ),
        prompt_version="2026-08-05.1",
        applied_skills=[AppliedSkillSchema(id=skill_id, name="samplelab-rebilling")],
        tool_calls=[
            {
                "skill_id": str(skill_id),
                "path": "references/client_rules.md",
                "is_error": False,
            }
        ],
    )
    assert persisted.applied_skills[0].name == "samplelab-rebilling"
    assert persisted.tool_calls is not None
    assert persisted.tool_calls[0].path == "references/client_rules.md"
