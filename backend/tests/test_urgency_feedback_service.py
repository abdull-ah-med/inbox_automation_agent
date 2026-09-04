"""Unit tests for urgency feedback service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

from app.core.config import Settings
from app.core.exceptions import DraftNotFoundError
from app.models.schemas.draft import DraftResponseSchema
from app.models.schemas.urgency_feedback import UrgencyEditRequestSchema
from app.services import urgency_feedback_service


def _draft(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "subject_line": "Re: Test",
        "reply_body": "Hello",
        "teaching_note": "Note",
        "urgency": "LOW",
        "urgency_reason": "Routine",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
        "routing_category": "billing",
    }
    base.update(overrides)
    return DraftResponseSchema.model_validate(base)


def _settings() -> Settings:
    return Settings(
        target_mailboxes="elise@example.com",
        openai_api_key="sk-test",
        jwt_secret="c" * 64,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.mark.asyncio
async def test_apply_manual_urgency_edit_updates_draft_and_thread() -> None:
    draft = _draft()
    session = AsyncMock()
    request = UrgencyEditRequestSchema(new_urgency="HIGH", reason="SLA deadline tomorrow")

    with (
        patch(
            "app.services.urgency_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=draft),
        ),
        patch(
            "app.services.urgency_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {
                        "id": draft.thread_id,
                        "mailbox": "elise@example.com",
                        "conversation_id": "c1",
                    },
                )()
            ),
        ),
        patch(
            "app.services.urgency_feedback_service.draft_repo.set_urgency",
            AsyncMock(return_value=_draft(id=draft.id, thread_id=draft.thread_id, urgency="HIGH")),
        ) as draft_set,
        patch(
            "app.services.urgency_feedback_service.thread_repo.set_urgency",
            AsyncMock(return_value=object()),
        ) as thread_set,
        patch(
            "app.services.urgency_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await urgency_feedback_service.apply_manual_urgency_edit(
            session,
            draft.id,
            request,
            actor="elise@example.com",
            settings=_settings(),
        )

    assert result.response.urgency == "HIGH"
    assert result.response.urgency_reason == "SLA deadline tomorrow"
    assert result.previous_urgency == "LOW"
    assert result.mailbox == "elise@example.com"
    draft_set.assert_awaited_once()
    thread_set.assert_awaited_once()
    audit_mock.assert_awaited_once()
    payload = audit_mock.await_args.kwargs["payload"]
    assert payload["previous"] == "LOW"
    assert payload["new"] == "HIGH"
    assert "reason" not in payload
    assert "SLA" not in str(payload)


@pytest.mark.asyncio
async def test_apply_manual_urgency_edit_missing_draft() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.urgency_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=None),
        ),
        pytest.raises(DraftNotFoundError),
    ):
        await urgency_feedback_service.apply_manual_urgency_edit(
            session,
            uuid.uuid4(),
            UrgencyEditRequestSchema(new_urgency="HIGH", reason="Because"),
            actor="elise@example.com",
        )


@pytest.mark.asyncio
async def test_store_urgency_feedback_memory_best_effort_on_embed_failure() -> None:
    with (
        patch(
            "app.services.urgency_feedback_service.embedding_service.embed_text",
            AsyncMock(side_effect=RuntimeError("embed down")),
        ),
        patch(
            "app.services.urgency_feedback_service.get_session_factory",
            AsyncMock(),
        ),
        capture_logs() as entries,
    ):
        result = await urgency_feedback_service.store_urgency_feedback_memory(
            draft_id=uuid.uuid4(),
            thread_id=uuid.uuid4(),
            mailbox="elise@example.com",
            routing_category="billing",
            previous_urgency="LOW",
            new_urgency="HIGH",
            reason="SLA",
            edited_by_user_id=None,
            settings=_settings(),
            openai_client=AsyncMock(),
        )
    assert result is None
    assert any(entry.get("event") == "urgency_feedback_embed_failed" for entry in entries)


@pytest.mark.asyncio
async def test_store_urgency_feedback_atomizes_reason_as_urgency_edit() -> None:
    """Plan §4.1: urgency edits are atomized with source_kind=urgency_edit."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    draft_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    thread_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    feedback_id = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    session = AsyncMock()
    session.in_transaction = MagicMock(return_value=False)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    anthropic = AsyncMock()

    with (
        patch(
            "app.services.urgency_feedback_service.embedding_service.embed_text",
            AsyncMock(return_value=[0.1] * 8),
        ),
        patch(
            "app.services.urgency_feedback_service.get_session_factory",
            return_value=factory,
        ),
        patch(
            "app.services.urgency_feedback_service.urgency_feedback_repo.insert_urgency_feedback",
            AsyncMock(return_value=SimpleNamespace(id=feedback_id)),
        ),
        patch(
            "app.services.urgency_feedback_service.atomize_and_persist",
            AsyncMock(return_value=[]),
        ) as atomize,
    ):
        await urgency_feedback_service.store_urgency_feedback_memory(
            draft_id=draft_id,
            thread_id=thread_id,
            mailbox="elise@example.com",
            routing_category="billing",
            previous_urgency="HIGH",
            new_urgency="LOW",
            reason="statuspage resolved notices are never urgent",
            edited_by_user_id=None,
            settings=_settings(),
            openai_client=AsyncMock(),
            anthropic_client=anthropic,
        )

    atomize.assert_awaited_once()
    kwargs = atomize.await_args.kwargs
    assert kwargs["source_kind"] == "urgency_edit"
    assert kwargs["source_id"] == feedback_id
    assert kwargs["text"] == "statuspage resolved notices are never urgent"
    assert kwargs["mailbox"] == "elise@example.com"
