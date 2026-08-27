"""Unit tests for post-ingest triage pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError
from app.llm.prompts import PROMPT_VERSION
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.services import pipeline_service

_EMBED_SAFE = "app.services.pipeline.service.embedding_service.embed_and_store_safe"


def _patch_embed() -> object:
    return patch(_EMBED_SAFE, new=AsyncMock(return_value=None))


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
    thread_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ingest = IngestResultSchema(
        message_id="m1",
        status="ingested",
        thread_id=thread_uuid,
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

    async def _run_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: Need docs",
            reply_body="Here is the packet.",
            teaching_note="Reply with the docs.",
            urgency="HIGH",
            urgency_reason="Client waiting",
        )
        return state

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline.service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ) as draft_mock,
        patch(
            "app.services.pipeline.service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        _patch_embed() as embed_mock,
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            ingest_result=ingest,
        )

    assert state.draft_status == "DRAFTED"
    assert state.triage == triage
    assert state.draft is not None
    assert state.draft.urgency == "HIGH"
    draft_mock.assert_awaited_once()
    embed_mock.assert_awaited_once()
    assert audit.await_count == 2
    assert audit.await_args_list[0].kwargs["event_type"] == "triage.action_needed"
    assert audit.await_args_list[1].kwargs["event_type"] == "draft.generated"
    triage_payload = audit.await_args_list[0].kwargs["payload"]
    assert triage_payload["prompt_version"] == PROMPT_VERSION
    assert triage_payload["is_spam"] is False
    assert triage_payload["has_action_items"] is True
    assert triage_payload["needs_context"] is False
    assert "confidence" not in triage_payload
    assert "spam_reason" not in triage_payload
    assert "action_items_summary" not in triage_payload
    assert "context_reason" not in triage_payload
    assert "body" not in triage_payload
    draft_payload = audit.await_args_list[1].kwargs["payload"]
    assert draft_payload["urgency"] == "HIGH"
    assert draft_payload["prompt_version"] == PROMPT_VERSION
    assert "reply_body" not in draft_payload
    assert "body" not in draft_payload


@pytest.mark.asyncio
async def test_run_after_ingest_clears_spam_for_allowlisted_sender() -> None:
    """Haiku spam + reviewer allowlist must still draft, not discard."""
    from app.llm.triage import TriageCallResult

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
        thread_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        conversation_id="c1",
        thread_context=context,
    )
    llm_spam = TriageCallResult(
        triage=TriageResultSchema(
            is_spam=True,
            spam_reason="Automated vendor mail",
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
            routing_category="vendor",
        ),
        prompt_version="v",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        latency_ms=5,
    )

    async def _run_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: Need docs",
            reply_body="Here is the packet.",
            teaching_note="Reply with the docs.",
            urgency="NORMAL",
            urgency_reason="Routine",
        )
        return state

    with (
        patch(
            "app.services.triage_service.triage_llm.triage_email",
            new=AsyncMock(return_value=llm_spam),
        ),
        patch(
            "app.services.pipeline.service._allowlisted_senders",
            new=AsyncMock(return_value=frozenset({"vendor@example.com"})),
        ),
        patch(
            "app.services.pipeline.service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ),
        patch(
            "app.services.pipeline.service.audit_service.log_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.reply_memory_service.find_similar_replies",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.pipeline.service.thread_repo.set_thread_outcome",
            new=AsyncMock(return_value=None),
        ),
        _patch_embed(),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            ingest_result=ingest,
        )

    assert state.triage is not None
    assert state.triage.is_spam is False
    assert state.draft_status == "DRAFTED"


@pytest.mark.asyncio
async def test_run_after_ingest_draft_failure_audits_requires_human() -> None:
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
        thread_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        conversation_id="c1",
        thread_context=context,
    )

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
        )
        state.draft_status = "PENDING"
        return state

    async def _fail_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append("draft_failed:DraftGenerationError")
        return state

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline.service.draft_service.run_draft",
            new=AsyncMock(side_effect=_fail_draft),
        ),
        patch(
            "app.services.pipeline.service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        _patch_embed(),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            ingest_result=ingest,
        )

    assert state.draft_status == "REQUIRES_HUMAN"
    assert audit.await_args_list[1].kwargs["event_type"] == "draft.requires_human"


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

        async def commit(self) -> None:
            return None

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_fail_triage),
        ),
        patch(
            "app.services.pipeline.service.audit_service.log_event",
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
        patch(
            "app.core.dependencies.openai_client_from_settings",
            return_value=AsyncMock(),
        ),
        _patch_embed(),
    ):
        result = await pipeline_service.run_post_ingest_triage(
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            ingest_result=ingest,
        )

    assert result is None


@pytest.mark.asyncio
async def test_run_post_ingest_triage_returns_none_when_draft_requires_human() -> None:
    """Draft failure must release dedup (return None) so poll can retry."""
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
        thread_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        conversation_id="c1",
        thread_context=context,
    )

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
        )
        state.draft_status = "PENDING"
        return state

    class _SessionCM:
        async def __aenter__(self) -> object:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def begin(self) -> _SessionCM:
            return self

        async def commit(self) -> None:
            return None

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline.service.sent_reply_repo.get_by_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.draft_llm.generate_draft",
            new=AsyncMock(side_effect=DraftGenerationError("boom")),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.recurrence_service.count_automated_inbound_48h",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "app.services.pipeline.service.audit_service.log_event",
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
        patch(
            "app.core.dependencies.openai_client_from_settings",
            return_value=AsyncMock(),
        ),
        _patch_embed() as embed_mock,
    ):
        result = await pipeline_service.run_post_ingest_triage(
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            ingest_result=ingest,
        )

    assert result is None
    embed_mock.assert_awaited()


@pytest.mark.parametrize(
    ("draft_status", "slack_delivery", "has_triage", "ready"),
    [
        ("SKIPPED", "not_attempted", True, True),
        ("DRAFTED", "posted", True, True),
        ("DRAFTED", "already_posted", True, True),
        ("DRAFTED", "skipped_unconfigured", True, True),
        ("DRAFTED", "not_required", True, True),
        ("DRAFTED", "failed", True, False),
        ("DRAFTED", "not_attempted", True, False),
        ("REQUIRES_HUMAN", "not_attempted", True, False),
        ("PENDING", "not_attempted", True, False),
        ("REQUIRES_HUMAN", "not_attempted", False, False),
    ],
)
def test_pipeline_ready_for_dedup_matrix(
    draft_status: str,
    slack_delivery: str,
    has_triage: bool,
    ready: bool,
) -> None:
    """Independent oracle: docstring on pipeline_ready_for_dedup."""
    email = _email()
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    triage = (
        TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="x",
            needs_context=False,
        )
        if has_triage
        else None
    )
    state = EmailTriageState(
        original_email=email,
        thread_context=context,
        triage=triage,
        draft_status=draft_status,  # type: ignore[arg-type]
        slack_delivery=slack_delivery,  # type: ignore[arg-type]
    )
    assert pipeline_service.pipeline_ready_for_dedup(state) is ready


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
