"""Unit tests for post-ingest triage pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.services import pipeline_service


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


@pytest.mark.asyncio
async def test_run_after_ingest_sets_state_and_audits() -> None:
    email = _email()
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    ingest = IngestResultSchema(
        message_id="m1",
        status="ingested",
        thread_id="t1",
        conversation_id="c1",
        thread_context=context,
    )
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        action_items_summary="Send packet",
        needs_context=False,
    )

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = triage
        state.draft_status = "PENDING"
        return state

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(),
            client=AsyncMock(),
            ingest_result=ingest,
        )

    assert state.draft_status == "PENDING"
    assert state.triage == triage
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["event_type"] == "triage.action_needed"
    payload = audit.await_args.kwargs["payload"]
    assert payload["prompt_version"] == PROMPT_VERSION
    assert payload["is_spam"] is False
    assert payload["has_action_items"] is True
    assert payload["needs_context"] is False
    assert "confidence" not in payload
    assert "spam_reason" not in payload
    assert "action_items_summary" not in payload
    assert "context_reason" not in payload
    assert "body" not in payload


@pytest.mark.asyncio
async def test_run_after_ingest_rejects_duplicate_status() -> None:
    with pytest.raises(ValueError, match="status in"):
        await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(),
            client=AsyncMock(),
            ingest_result=IngestResultSchema(message_id="m1", status="duplicate"),
        )


@pytest.mark.asyncio
async def test_run_post_ingest_triage_returns_none_when_haiku_fails() -> None:
    """TriageError must not look like success to webhook/poll dedup callers."""
    email = _email()
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    ingest = IngestResultSchema(
        message_id="m1",
        status="ingested",
        thread_id="t1",
        conversation_id="c1",
        thread_context=context,
    )

    async def _fail_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("triage_failed:TriageError")
        return state

    class _SessionCM:
        async def __aenter__(self) -> object:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def begin(self) -> _SessionCM:
            return self

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_fail_triage),
        ),
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ),
        patch(
            "app.db.session.get_session_factory",
            return_value=lambda: _SessionCM(),
        ),
        patch(
            "app.core.dependencies.anthropic_client_from_settings",
            return_value=AsyncMock(),
        ),
    ):
        result = await pipeline_service.run_post_ingest_triage(
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            ingest_result=ingest,
        )

    assert result is None


def test_select_original_email_requires_exact_message_id() -> None:
    older = EmailMessageSchema(
        message_id="old",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="a@example.com",
        subject="thread",
        body_text="old",
        received_at=datetime(2026, 7, 9, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    newer = EmailMessageSchema(
        message_id="new",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="b@example.com",
        subject="thread",
        body_text="new",
        received_at=datetime(2026, 7, 10, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox="elise@example.com",
        subject="thread",
        messages=[older, newer],
    )
    selected = pipeline_service._select_original_email(
        message_id="old",
        thread_context=context,
    )
    assert selected.message_id == "old"
    with pytest.raises(ValueError, match="not found"):
        pipeline_service._select_original_email(
            message_id="missing",
            thread_context=context,
        )
