"""Catch-up triage + learn from quick Outlook replies (no Sonnet draft)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftResponseSchema, DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.repositories.sent_reply_repo import SentReplySchema
from app.services import sent_reply_learning_service

LEARNED_NOTE = "Learned from Outlook send"
SENT_BODY = "Thanks — we will send the packet today."


def _settings() -> Settings:
    return Settings(environment="local", openai_api_key="sk-test")


def _inbound(*, message_id: str = "inbound-1") -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox="inquiries@example.com",
        sender="client@vendor.example",
        subject="Need docs",
        body_text="Please send the packet",
        received_at=datetime(2026, 8, 27, 14, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )


def _outbound(*, message_id: str = "outbound-1") -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox="inquiries@example.com",
        sender="elise@example.com",
        subject="Re: Need docs",
        body_text=SENT_BODY,
        received_at=datetime(2026, 8, 27, 14, 5, tzinfo=UTC),
        direction=EmailDirectionEnum.OUTBOUND,
    )


def _sent_reply(
    *,
    thread_id: uuid.UUID,
    draft_id: uuid.UUID | None = None,
    snapshot: str = SENT_BODY,
) -> SentReplySchema:
    return SentReplySchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        message_id=uuid.uuid4(),
        draft_id=draft_id,
        sent_body_snapshot=snapshot,
        sent_at=datetime(2026, 8, 27, 14, 5, tzinfo=UTC),
        matched_by="time_window",
        created_at=datetime(2026, 8, 27, 14, 5, tzinfo=UTC),
    )


def _approved_draft(
    *,
    thread_id: uuid.UUID,
    draft_id: uuid.UUID | None = None,
    body: str = SENT_BODY,
) -> DraftResponseSchema:
    return DraftResponseSchema.model_validate(
        {
            "id": draft_id or uuid.uuid4(),
            "thread_id": thread_id,
            "created_at": datetime(2026, 8, 27, 14, 6, tzinfo=UTC),
            "subject_line": "Re: Need docs",
            "reply_body": body,
            "teaching_note": LEARNED_NOTE,
            "urgency": "NORMAL",
            "urgency_reason": "Human already replied",
            "suggested_recipients": [],
            "forward_to": None,
            "suggested_actions": [],
            "approved_at": datetime(2026, 8, 27, 14, 6, tzinfo=UTC),
            "feedback_action": "approve",
            "approval_scope": "similar",
            "approval_note": LEARNED_NOTE,
            "routing_category": "general",
        }
    )


@pytest.mark.asyncio
async def test_thread_needs_catchup_when_sent_reply_inbound_and_no_triage() -> None:
    thread_id = uuid.uuid4()
    session = AsyncMock()
    with (
        patch(
            "app.services.sent_reply_learning_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=_sent_reply(thread_id=thread_id)),
        ),
        patch(
            "app.services.sent_reply_learning_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.sent_reply_learning_service.message_repo.list_by_thread",
            AsyncMock(
                return_value=[
                    SimpleNamespace(direction="inbound"),
                    SimpleNamespace(direction="outbound"),
                ]
            ),
        ),
    ):
        needs = await sent_reply_learning_service.thread_needs_catchup_triage(
            session,
            thread_id=thread_id,
            mailbox="inquiries@example.com",
            conversation_id="conv-1",
        )
    assert needs is True


@pytest.mark.asyncio
async def test_thread_needs_catchup_false_when_triage_flags_exist() -> None:
    thread_id = uuid.uuid4()
    session = AsyncMock()
    with (
        patch(
            "app.services.sent_reply_learning_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=_sent_reply(thread_id=thread_id)),
        ),
        patch(
            "app.services.sent_reply_learning_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=SimpleNamespace(is_spam=False, has_action_items=True)),
        ),
        patch(
            "app.services.sent_reply_learning_service.message_repo.list_by_thread",
            AsyncMock(return_value=[SimpleNamespace(direction="inbound")]),
        ),
    ):
        needs = await sent_reply_learning_service.thread_needs_catchup_triage(
            session,
            thread_id=thread_id,
            mailbox="inquiries@example.com",
            conversation_id="conv-1",
        )
    assert needs is False


@pytest.mark.asyncio
async def test_promote_sent_reply_creates_approved_similar_and_stores_memory() -> None:
    """Sent body becomes an approved similar draft; reply memory stores that literal body."""
    thread_id = uuid.uuid4()
    sent = _sent_reply(thread_id=thread_id)
    outbound_graph_id = "AAMkAG-elise-sent"
    created = _approved_draft(thread_id=thread_id)
    # create returns unapproved; approve returns approved
    created_raw = created.model_copy(
        update={"approved_at": None, "feedback_action": None, "approval_scope": None}
    )
    approved = created
    session = AsyncMock()
    settings = _settings()
    openai = AsyncMock()

    with (
        patch(
            "app.services.sent_reply_learning_service.message_repo.get_by_id_trusted",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=sent.message_id,
                    graph_message_id=outbound_graph_id,
                    thread_id=thread_id,
                )
            ),
        ),
        patch(
            "app.services.sent_reply_learning_service.thread_repo.get_by_id_trusted",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=thread_id,
                    subject="Need docs",
                    mailbox="inquiries@example.com",
                    conversation_id="conv-1",
                )
            ),
        ),
        patch(
            "app.services.sent_reply_learning_service.draft_repo.create_draft",
            AsyncMock(return_value=created_raw),
        ) as create_draft,
        patch(
            "app.services.sent_reply_learning_service.draft_feedback_service.approve_draft",
            AsyncMock(return_value=approved),
        ) as approve,
        patch(
            "app.services.sent_reply_learning_service.sent_reply_repo.link_draft",
            AsyncMock(return_value=sent.model_copy(update={"draft_id": approved.id})),
        ) as link,
        patch(
            "app.services.sent_reply_learning_service.draft_feedback_service.store_approved_reply_memory",
            AsyncMock(),
        ) as store_memory,
    ):
        result = await sent_reply_learning_service.promote_sent_reply_as_approved(
            session,
            sent_reply=sent,
            settings=settings,
            openai_client=openai,
            routing_category="general",
        )
        await sent_reply_learning_service.store_promoted_reply_memory(
            draft=result,  # type: ignore[arg-type]
            settings=settings,
            openai_client=openai,
        )

    assert result is not None
    assert result.approval_scope == "similar"
    assert result.approved_at is not None
    draft_arg: DraftSchema = create_draft.await_args.kwargs["draft"]
    assert draft_arg.reply_body == SENT_BODY
    assert draft_arg.teaching_note == LEARNED_NOTE
    assert create_draft.await_args.kwargs["message_id"] == outbound_graph_id
    assert create_draft.await_args.kwargs["routing_category"] == "general"
    assert approve.await_args.kwargs["approval_scope"] == "similar"
    assert approve.await_args.kwargs["approval_note"] == LEARNED_NOTE
    assert approve.await_args.kwargs["actor"] == "system"
    assert link.await_args.kwargs["draft_id"] == approved.id
    store_memory.assert_awaited_once()
    assert store_memory.await_args.kwargs["draft"].id == approved.id


@pytest.mark.asyncio
async def test_promote_idempotent_when_already_linked_approved_draft() -> None:
    thread_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    sent = _sent_reply(thread_id=thread_id, draft_id=draft_id)
    approved = _approved_draft(thread_id=thread_id, draft_id=draft_id)
    session = AsyncMock()

    with (
        patch(
            "app.services.sent_reply_learning_service.draft_repo.get_draft_by_id",
            AsyncMock(return_value=approved),
        ),
        patch(
            "app.services.sent_reply_learning_service.draft_repo.create_draft",
            AsyncMock(),
        ) as create_draft,
        patch(
            "app.services.sent_reply_learning_service.draft_feedback_service.store_approved_reply_memory",
            AsyncMock(),
        ) as store_memory,
    ):
        result = await sent_reply_learning_service.promote_sent_reply_as_approved(
            session,
            sent_reply=sent,
            settings=_settings(),
            openai_client=AsyncMock(),
            routing_category="general",
        )

    assert result is not None
    assert result.id == draft_id
    create_draft.assert_not_awaited()
    store_memory.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_catchup_triages_inbound_skips_sonnet_promotes_and_keeps_resolved() -> None:
    """Quick Elise reply: Haiku runs, Sonnet does not, synthetic approve, RESOLVED."""
    thread_id = uuid.uuid4()
    inbound = _inbound()
    outbound = _outbound()
    context = ThreadContextSchema(
        conversation_id="conv-1",
        mailbox="inquiries@example.com",
        subject="Need docs",
        messages=[inbound, outbound],
    )
    sent = _sent_reply(thread_id=thread_id)
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        action_items_summary="Send packet",
        needs_context=False,
        routing_category="general",
    )
    approved = _approved_draft(thread_id=thread_id)

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = triage
        state.draft_status = "PENDING"
        return state

    settings = _settings()
    redis = AsyncMock()
    factory = MagicMock()
    session = MagicMock()
    session.in_transaction = MagicMock(return_value=False)
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    factory.return_value = session_cm

    with (
        patch(
            "app.services.sent_reply_learning_service.thread_needs_catchup_triage",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.sent_reply_learning_service.thread_repo.get_by_id_trusted",
            AsyncMock(
                return_value=SimpleNamespace(
                    id=thread_id,
                    mailbox="inquiries@example.com",
                    subject="Need docs",
                    conversation_id="conv-1",
                )
            ),
        ),
        patch(
            "app.services.ingestion_service.build_thread_context_from_db",
            AsyncMock(
                return_value=IngestResultSchema(
                    message_id=outbound.message_id,
                    status="retry_triage",
                    thread_id=str(thread_id),
                    conversation_id="conv-1",
                    thread_context=context,
                )
            ),
        ),
        patch(
            "app.services.sent_reply_learning_service.sent_reply_repo.get_by_thread",
            AsyncMock(return_value=sent),
        ),
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ) as triage_mock,
        patch(
            "app.services.pipeline.service.draft_llm.generate_draft",
            new=AsyncMock(),
        ) as draft_llm,
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._refresh_thread_summary_safe",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._allowlisted_senders",
            new=AsyncMock(return_value=frozenset()),
        ),
        patch(
            "app.services.pipeline.service._safe_audit",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._apply_triage_outcome_state",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.embedding_service.embed_and_store_safe",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.sent_reply_learning_service.promote_sent_reply_as_approved",
            AsyncMock(return_value=approved),
        ) as promote,
        patch(
            "app.services.sent_reply_learning_service.store_promoted_reply_memory",
            AsyncMock(),
        ),
        patch(
            "app.services.sent_reply_learning_service.thread_repo.set_thread_outcome",
            AsyncMock(),
        ) as set_outcome,
    ):
        state = await sent_reply_learning_service.run_catchup_after_outbound(
            redis=redis,
            settings=settings,
            thread_id=thread_id,
            mailbox="inquiries@example.com",
            conversation_id="conv-1",
            outbound_graph_message_id=outbound.message_id,
            session_factory=factory,
            openai_client=AsyncMock(),
            anthropic_client=AsyncMock(),
        )

    assert state is not None
    assert state.triage == triage
    assert state.draft_status == "SKIPPED"
    triage_mock.assert_awaited_once()
    assert triage_mock.await_args.args[0].original_email.message_id == inbound.message_id
    draft_llm.assert_not_awaited()
    promote.assert_awaited_once()
    assert set_outcome.await_args.kwargs["state"] == "RESOLVED"


@pytest.mark.asyncio
async def test_run_catchup_noop_when_not_needed() -> None:
    with patch(
        "app.services.sent_reply_learning_service.thread_needs_catchup_triage",
        AsyncMock(return_value=False),
    ):
        state = await sent_reply_learning_service.run_catchup_after_outbound(
            redis=AsyncMock(),
            settings=_settings(),
            thread_id=uuid.uuid4(),
            mailbox="inquiries@example.com",
            conversation_id="conv-1",
            outbound_graph_message_id="out-1",
            session_factory=MagicMock(),
        )
    assert state is None


@pytest.mark.asyncio
async def test_phased_pipeline_skips_sonnet_when_sent_reply_already_exists() -> None:
    """Inbound arrives after Elise replied: triage yes, draft LLM no, stay out of DRAFTED."""
    from app.services import pipeline_service

    thread_id = uuid.uuid4()
    inbound = _inbound()
    context = ThreadContextSchema(
        conversation_id="conv-1",
        mailbox=inbound.mailbox,
        subject=inbound.subject,
        messages=[inbound],
    )
    ingest = IngestResultSchema(
        message_id=inbound.message_id,
        status="ingested",
        thread_id=str(thread_id),
        conversation_id="conv-1",
        thread_context=context,
    )
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        action_items_summary="Send packet",
        needs_context=False,
        routing_category="general",
    )

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = triage
        state.draft_status = "PENDING"
        return state

    class _SessionCM:
        async def __aenter__(self) -> _SessionCM:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        def begin(self) -> _SessionCM:
            return self

        async def commit(self) -> None:
            return None

        def in_transaction(self) -> bool:
            return False

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline.service.sent_reply_repo.get_by_thread",
            new=AsyncMock(return_value=_sent_reply(thread_id=thread_id)),
        ),
        patch(
            "app.services.pipeline.service.draft_llm.generate_draft",
            new=AsyncMock(),
        ) as draft_llm,
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._refresh_thread_summary_safe",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._allowlisted_senders",
            new=AsyncMock(return_value=frozenset()),
        ),
        patch(
            "app.services.pipeline.service._safe_audit",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._apply_triage_outcome_state",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._invalidate_chat_cache_mailbox",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.embedding_service.embed_and_store_safe",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.sent_reply_learning_service.promote_sent_reply_as_approved",
            new=AsyncMock(return_value=_approved_draft(thread_id=thread_id)),
        ) as promote,
        patch(
            "app.services.pipeline.service.sent_reply_learning_service.store_promoted_reply_memory",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.thread_repo.set_thread_outcome",
            new=AsyncMock(),
        ) as set_outcome,
        patch(
            "app.core.dependencies.anthropic_client_from_settings",
            return_value=AsyncMock(),
        ),
        patch(
            "app.core.dependencies.openai_client_from_settings",
            return_value=AsyncMock(),
        ),
    ):
        state = await pipeline_service.run_phased_after_ingest(
            redis=AsyncMock(),
            settings=_settings(),
            ingest_result=ingest,
            session_factory=lambda: _SessionCM(),  # type: ignore[arg-type,return-value]
        )

    assert state.draft_status == "SKIPPED"
    assert state.triage == triage
    draft_llm.assert_not_awaited()
    promote.assert_awaited_once()
    assert set_outcome.await_args.kwargs["state"] == "RESOLVED"
