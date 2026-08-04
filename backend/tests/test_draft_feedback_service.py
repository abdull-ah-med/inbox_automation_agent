"""Unit tests for draft feedback service (approve / reject / mark-wrong)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import DraftNotFoundError
from app.models.schemas.draft import DraftResponseSchema
from app.services import draft_feedback_service


def _draft(**overrides: object) -> DraftResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "thread_id": uuid.uuid4(),
        "created_at": datetime.now(UTC),
        "subject_line": "Re: Test",
        "reply_body": "Hello",
        "teaching_note": "Acknowledge and wait",
        "urgency": "NORMAL",
        "urgency_reason": "Routine inquiry",
        "suggested_recipients": [],
        "forward_to": None,
        "suggested_actions": [],
        "approved_at": None,
        "rejected_at": None,
        "edited_body": None,
        "feedback_note": None,
        "feedback_action": None,
        "context_match_confidence": None,
    }
    base.update(overrides)
    return DraftResponseSchema.model_validate(base)


@pytest.mark.asyncio
async def test_approve_sets_approved_at() -> None:
    draft_id = uuid.uuid4()
    existing = _draft(id=draft_id)
    approved = _draft(
        id=draft_id,
        thread_id=existing.thread_id,
        approved_at=datetime.now(UTC),
        feedback_action="approve",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.approve_draft",
            AsyncMock(return_value=approved),
        ) as approve_mock,
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.approve_draft(
            session,
            draft_id,
            actor="elise@example.com",
        )

    assert result.feedback_action == "approve"
    assert result.approved_at is not None
    approve_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    assert audit_mock.await_args.kwargs["event_type"] == "draft.approved"


@pytest.mark.asyncio
async def test_approve_with_edited_body() -> None:
    draft_id = uuid.uuid4()
    existing = _draft(id=draft_id)
    approved = _draft(
        id=draft_id,
        thread_id=existing.thread_id,
        approved_at=datetime.now(UTC),
        feedback_action="approve",
        edited_body="Edited hello",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.approve_draft",
            AsyncMock(return_value=approved),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.approve_draft(
            session,
            draft_id,
            edited_body="Edited hello",
            actor="elise@example.com",
        )

    assert result.edited_body == "Edited hello"
    assert audit_mock.await_args.kwargs["event_type"] == "draft.edited_and_approved"


@pytest.mark.asyncio
async def test_approve_idempotent_skips_audit() -> None:
    draft_id = uuid.uuid4()
    approved = _draft(
        id=draft_id,
        approved_at=datetime.now(UTC),
        feedback_action="approve",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=approved),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.approve_draft",
            AsyncMock(),
        ) as approve_mock,
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.approve_draft(session, draft_id)

    assert result.feedback_action == "approve"
    approve_mock.assert_not_awaited()
    audit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_already_approved_applies_new_edit() -> None:
    draft_id = uuid.uuid4()
    approved = _draft(
        id=draft_id,
        thread_id=uuid.uuid4(),
        approved_at=datetime.now(UTC),
        feedback_action="approve",
        reply_body="Hello",
        edited_body=None,
    )
    updated = _draft(
        id=draft_id,
        thread_id=approved.thread_id,
        approved_at=approved.approved_at,
        feedback_action="approve",
        reply_body="Hello",
        edited_body="Revised hello",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=approved),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.approve_draft",
            AsyncMock(return_value=updated),
        ) as approve_mock,
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.approve_draft(
            session,
            draft_id,
            edited_body="Revised hello",
        )

    assert result.edited_body == "Revised hello"
    approve_mock.assert_awaited_once()
    assert approve_mock.await_args.kwargs["edited_body"] == "Revised hello"
    assert audit_mock.await_args.kwargs["event_type"] == "draft.edited_and_approved"


@pytest.mark.asyncio
async def test_reject_sets_note() -> None:
    draft_id = uuid.uuid4()
    existing = _draft(id=draft_id)
    rejected = _draft(
        id=draft_id,
        thread_id=existing.thread_id,
        rejected_at=datetime.now(UTC),
        feedback_action="reject",
        feedback_note="Tone is too curt",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.reject_draft",
            AsyncMock(return_value=rejected),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.reject_draft(
            session,
            draft_id,
            feedback_note="Tone is too curt",
            reason_code="tone",
        )

    assert result.feedback_action == "reject"
    assert result.feedback_note == "Tone is too curt"
    assert audit_mock.await_args.kwargs["event_type"] == "draft.rejected"


@pytest.mark.asyncio
async def test_mark_wrong() -> None:
    draft_id = uuid.uuid4()
    existing = _draft(id=draft_id)
    wrong = _draft(
        id=draft_id,
        thread_id=existing.thread_id,
        feedback_action="wrong",
        feedback_note="Should have forwarded to Jordan",
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_feedback_service.draft_repo.mark_wrong",
            AsyncMock(return_value=wrong),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_feedback_service.mark_wrong(
            session,
            draft_id,
            feedback_note="Should have forwarded to Jordan",
            reason_code="wrong_action",
        )

    assert result.feedback_action == "wrong"
    assert audit_mock.await_args.kwargs["event_type"] == "draft.marked_wrong"


@pytest.mark.asyncio
async def test_approve_not_found() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=None),
        ),
        pytest.raises(DraftNotFoundError),
    ):
        await draft_feedback_service.approve_draft(session, uuid.uuid4())


@pytest.mark.asyncio
async def test_approve_mailbox_not_allowed() -> None:
    from app.core.config import Settings

    draft_id = uuid.uuid4()
    existing = _draft(id=draft_id)
    session = AsyncMock()
    settings = Settings(
        target_mailboxes="allowed@example.com",
        jwt_secret="c" * 64,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )

    with (
        patch(
            "app.services.draft_feedback_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "other@example.com"},
                )()
            ),
        ),
        pytest.raises(DraftNotFoundError),
    ):
        await draft_feedback_service.approve_draft(
            session,
            draft_id,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_approve_stores_reply_memory_helper() -> None:
    from app.core.config import Settings

    draft = _draft(
        approved_at=datetime.now(UTC),
        feedback_action="approve",
        reply_body="Approved body",
    )
    session = AsyncMock()
    session.in_transaction = lambda: False
    settings = Settings(
        target_mailboxes="elise@example.com",
        openai_api_key="sk-test",
        jwt_secret="c" * 64,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )

    class _CM:
        async def __aenter__(self) -> AsyncMock:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    with (
        patch(
            "app.services.draft_feedback_service.get_session_factory",
            return_value=lambda: _CM(),
        ),
        patch(
            "app.services.draft_feedback_service.thread_repo.get_by_id",
            AsyncMock(
                return_value=type(
                    "T",
                    (),
                    {"conversation_id": "c1", "mailbox": "elise@example.com", "subject": "Subj"},
                )()
            ),
        ),
        patch(
            "app.services.draft_feedback_service.reply_memory_service.store_approved_reply",
            AsyncMock(),
        ) as store_mock,
    ):
        await draft_feedback_service.store_approved_reply_memory(
            draft=draft,
            settings=settings,
            openai_client=AsyncMock(),
        )

    store_mock.assert_awaited_once()
    assert store_mock.await_args.kwargs["final_body"] == "Approved body"
    assert store_mock.await_args.kwargs["mailbox"] == "elise@example.com"


@pytest.mark.asyncio
async def test_approve_reply_memory_failure_does_not_raise() -> None:
    """Dedicated session errors must not escape (approve HTTP stays 200)."""
    from app.core.config import Settings

    draft = _draft(
        approved_at=datetime.now(UTC),
        feedback_action="approve",
        reply_body="Approved body",
    )
    settings = Settings(
        target_mailboxes="elise@example.com",
        openai_api_key="sk-test",
        jwt_secret="c" * 64,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )

    with patch(
        "app.services.draft_feedback_service.get_session_factory",
        side_effect=RuntimeError("db down"),
    ):
        await draft_feedback_service.store_approved_reply_memory(
            draft=draft,
            settings=settings,
            openai_client=AsyncMock(),
        )
