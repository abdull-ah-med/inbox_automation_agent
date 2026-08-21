"""Unit tests for draft service orchestration."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm.draft_generator import DraftCallResult
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftResponseSchema, DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.services import draft_service


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Need docs",
        body_text="Please send the packet",
        received_at=datetime(2026, 7, 10, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )


def _state(*, triage: TriageResultSchema | None = None) -> EmailTriageState:
    email = _email()
    return EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id="c1",
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage=triage
        or TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
        ),
        draft_status="PENDING",
    )


def _draft() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need docs",
        reply_body="Here is the packet.",
        teaching_note="Reply with the requested docs.",
        urgency="NORMAL",
        urgency_reason="Routine document request",
    )


def _persisted(draft: DraftSchema, *, thread_id: uuid.UUID) -> DraftResponseSchema:
    return DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        created_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
        subject_line=draft.subject_line,
        reply_body=draft.reply_body,
        suggested_recipients=list(draft.suggested_recipients),
        forward_to=draft.forward_to,
        teaching_note=draft.teaching_note,
        urgency=draft.urgency,
        urgency_reason=draft.urgency_reason,
    )


@pytest.mark.asyncio
async def test_run_draft_pending_to_drafted() -> None:
    thread_id = uuid.uuid4()
    draft = _draft()
    call = DraftCallResult(
        draft=draft,
        prompt_version="v-draft",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=20,
        latency_ms=50,
    )

    with (
        patch(
            "app.services.draft_service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_service.skill_selection_service.select_skill_contents",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.tone_profile_service.load_for_draft",
            new=AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_service.rejection_memory_service.find_negative_constraints",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.draft_llm.generate_draft",
            new=AsyncMock(return_value=call),
        ) as generate,
        patch(
            "app.services.draft_service.draft_repo.create_draft",
            new=AsyncMock(return_value=_persisted(draft, thread_id=thread_id)),
        ) as create,
    ):
        state = await draft_service.run_draft(
            _state(),
            session=AsyncMock(),
            client=AsyncMock(),
            settings=Settings(anthropic_api_key="test-key"),
            thread_id=thread_id,
        )

    assert state.draft_status == "DRAFTED"
    assert state.draft is not None
    assert state.draft.urgency == "NORMAL"
    assert "confidence" not in type(state.draft).model_fields
    generate.assert_awaited_once()
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_draft_generation_failure_requires_human() -> None:
    with (
        patch(
            "app.services.draft_service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_service.skill_selection_service.select_skill_contents",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.tone_profile_service.load_for_draft",
            new=AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_service.rejection_memory_service.find_negative_constraints",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.draft_llm.generate_draft",
            new=AsyncMock(side_effect=DraftGenerationError("boom")),
        ),
        patch(
            "app.services.draft_service.draft_repo.create_draft",
            new=AsyncMock(),
        ) as create,
    ):
        state = await draft_service.run_draft(
            _state(),
            session=AsyncMock(),
            client=AsyncMock(),
            settings=Settings(anthropic_api_key="test-key"),
            thread_id=uuid.uuid4(),
        )

    assert state.draft_status == "REQUIRES_HUMAN"
    assert state.draft is None
    assert any(err.startswith("draft_failed:") for err in state.error_logs)
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_draft_persists_context_match_confidence() -> None:
    thread_id = uuid.uuid4()
    draft = _draft()
    call = DraftCallResult(
        draft=draft,
        prompt_version="v-draft",
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=20,
        latency_ms=50,
    )
    prior = EmailMessageSchema(
        message_id="prior-1",
        conversation_id="conv-matched",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Prior",
        body_text="Earlier",
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    cross = CrossThreadContextSchema(
        matched_conversation_id="conv-matched",
        similarity_score=0.84,
        thread_messages=[prior],
    )

    with (
        patch(
            "app.services.draft_service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_service.skill_selection_service.select_skill_contents",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.tone_profile_service.load_for_draft",
            new=AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_service.rejection_memory_service.find_negative_constraints",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_service.draft_llm.generate_draft",
            new=AsyncMock(return_value=call),
        ) as generate,
        patch(
            "app.services.draft_service.draft_repo.create_draft",
            new=AsyncMock(return_value=_persisted(draft, thread_id=thread_id)),
        ) as create,
    ):
        state = await draft_service.run_draft(
            _state(),
            session=AsyncMock(),
            client=AsyncMock(),
            settings=Settings(anthropic_api_key="test-key"),
            thread_id=thread_id,
            cross_thread_context=cross,
        )

    assert state.draft_status == "DRAFTED"
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["cross_thread_context"] is cross
    create.assert_awaited_once()
    assert create.await_args.kwargs["context_match_confidence"] == 0.84

    thread_id = uuid.uuid4()
    draft = _draft()
    existing = _persisted(draft, thread_id=thread_id)

    with (
        patch(
            "app.services.draft_service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_service.draft_llm.generate_draft",
            new=AsyncMock(),
        ) as generate,
        patch(
            "app.services.draft_service.draft_repo.create_draft",
            new=AsyncMock(),
        ) as create,
    ):
        state = await draft_service.run_draft(
            _state(),
            session=AsyncMock(),
            client=AsyncMock(),
            settings=Settings(anthropic_api_key="test-key"),
            thread_id=thread_id,
        )

    assert state.draft_status == "DRAFTED"
    assert state.draft is not None
    assert state.draft.subject_line == draft.subject_line
    generate.assert_not_awaited()
    create.assert_not_awaited()
