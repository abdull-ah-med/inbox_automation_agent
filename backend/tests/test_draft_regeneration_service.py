"""Tests for draft regeneration service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError, ThreadNotFoundError
from app.llm.draft_generator import DraftCallResult
from app.models.schemas.dashboard import TriageFlags
from app.models.schemas.draft import DraftResponseSchema, DraftSchema
from app.repositories.message_repo import MessageSchema
from app.services import draft_regeneration_service


def _settings() -> Settings:
    return Settings(
        draft_model="claude-sonnet-4-6",
        anthropic_api_key="test-key",
        target_mailboxes="elise@example.com",
    )


def _thread():
    return type(
        "T",
        (),
        {
            "id": uuid.uuid4(),
            "mailbox": "elise@example.com",
            "conversation_id": "c1",
            "subject": "Need docs",
        },
    )()


def _message(thread_id: uuid.UUID) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="graph-m1",
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please send docs",
        body_preview="Please send",
        received_at=datetime.now(UTC),
        to_recipients=["elise@example.com"],
        cc_recipients=[],
        has_attachments=False,
    )


def _draft_schema() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need docs",
        reply_body="Acknowledged.",
        teaching_note="Simple acknowledge",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )


@pytest.mark.asyncio
async def test_regenerate_creates_new_draft() -> None:
    thread = _thread()
    message = _message(thread.id)
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Need docs",
        reply_body="Acknowledged only.",
        teaching_note="Acknowledge path",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)
    client = AsyncMock()

    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(
                return_value=TriageFlags(
                    is_spam=False,
                    has_action_items=True,
                    needs_context=False,
                    action_items_summary="Send docs",
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_repo.list_active",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.reply_memory_service.find_similar_replies",
            AsyncMock(return_value=["Thanks — sending the packet now."]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=_draft_schema(),
                    prompt_version="2026-07-29.1",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=50,
                )
            ),
        ) as gen_mock,
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ) as create_mock,
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_regeneration_service.regenerate_draft(
            session,
            client=client,
            settings=_settings(),
            thread_id=thread.id,
            instruction="just acknowledge, no action items",
            actor="elise@example.com",
        )

    assert result.id == persisted.id
    assert gen_mock.await_args.kwargs["instruction"] == "just acknowledge, no action items"
    assert gen_mock.await_args.kwargs["tone_references"] == [
        "Thanks — sending the packet now."
    ]
    create_mock.assert_awaited_once()
    assert audit_mock.await_args.kwargs["event_type"] == "draft.regenerated"


@pytest.mark.asyncio
async def test_regenerate_thread_not_found() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ThreadNotFoundError),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=uuid.uuid4(),
            instruction="acknowledge",
        )


@pytest.mark.asyncio
async def test_regenerate_no_messages() -> None:
    thread = _thread()
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[]),
        ),
        pytest.raises(DraftGenerationError),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
            instruction="acknowledge",
        )
