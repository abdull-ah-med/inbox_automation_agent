"""Unit tests for Flow B pipeline wiring (needs_context → context → draft)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.services import pipeline_service

_EMBED_SAFE = "app.services.pipeline_service.embedding_service.embed_and_store_safe"
_RESOLVE = "app.services.pipeline_service.context_service.resolve_cross_thread_context"
_GRAPH = "app.services.pipeline_service._resolve_graph_client"


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="As discussed — need docs",
        body_text="Following up on our earlier thread",
        received_at=datetime(2026, 7, 23, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )


def _ingest() -> IngestResultSchema:
    email = _email()
    context = ThreadContextSchema(
        conversation_id="c1",
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )
    return IngestResultSchema(
        message_id="m1",
        status="ingested",
        thread_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        conversation_id="c1",
        thread_context=context,
    )


def _cross() -> CrossThreadContextSchema:
    prior = EmailMessageSchema(
        message_id="prior-1",
        conversation_id="conv-matched",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Intake kickoff",
        body_text="Let's start intake.",
        body_preview="Let's start intake.",
        received_at=datetime(2026, 7, 1, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    return CrossThreadContextSchema(
        matched_conversation_id="conv-matched",
        similarity_score=0.87,
        thread_messages=[prior],
    )


@pytest.mark.asyncio
async def test_flow_b_match_audits_and_passes_context_to_draft() -> None:
    ingest = _ingest()
    cross = _cross()

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=True,
            context_reason="refers to prior thread",
        )
        state.draft_status = "PENDING"
        return state

    async def _run_draft(state: EmailTriageState, **kwargs: object) -> EmailTriageState:
        assert kwargs.get("cross_thread_context") is cross
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: As discussed — need docs",
            reply_body="Attached is the packet from our earlier thread.",
            teaching_note="Used prior intake thread.",
            urgency="HIGH",
            urgency_reason="Follow-up on open intake",
        )
        return state

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline_service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ) as draft_mock,
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(_RESOLVE, new=AsyncMock(return_value=cross)) as resolve,
        patch(_GRAPH, new=AsyncMock(return_value=MagicMock())),
        patch(_EMBED_SAFE, new=AsyncMock(return_value=None)) as embed_mock,
        patch(
            "app.db.session.get_session_factory",
            return_value=MagicMock(),
        ),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            graph_client=MagicMock(),
            ingest_result=ingest,
        )

    assert state.draft_status == "DRAFTED"
    assert state.cross_thread_context is cross
    resolve.assert_awaited_once()
    draft_mock.assert_awaited_once()
    embed_mock.assert_not_awaited()
    event_types = [c.kwargs["event_type"] for c in audit.await_args_list]
    assert "context.match" in event_types
    assert "draft.generated" in event_types
    match_call = next(c for c in audit.await_args_list if c.kwargs["event_type"] == "context.match")
    assert match_call.kwargs["payload"]["similarity_score"] == 0.87
    assert match_call.kwargs["payload"]["matched_conversation_id"] == "conv-matched"


@pytest.mark.asyncio
async def test_flow_b_no_match_falls_back_to_flow_a() -> None:
    ingest = _ingest()

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=True,
            context_reason="maybe prior thread",
        )
        state.draft_status = "PENDING"
        return state

    async def _run_draft(state: EmailTriageState, **kwargs: object) -> EmailTriageState:
        assert kwargs.get("cross_thread_context") is None
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: As discussed — need docs",
            reply_body="Happy to help — could you clarify which packet?",
            teaching_note="No prior match; ask clarifying question.",
            urgency="NORMAL",
            urgency_reason="Routine follow-up",
        )
        return state

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline_service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ),
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(_RESOLVE, new=AsyncMock(return_value=None)),
        patch(_GRAPH, new=AsyncMock(return_value=MagicMock())),
        patch(_EMBED_SAFE, new=AsyncMock(return_value=None)) as embed_mock,
        patch(
            "app.db.session.get_session_factory",
            return_value=MagicMock(),
        ),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            graph_client=MagicMock(),
            ingest_result=ingest,
        )

    assert state.draft_status == "DRAFTED"
    assert state.cross_thread_context is None
    embed_mock.assert_awaited_once()
    event_types = [c.kwargs["event_type"] for c in audit.await_args_list]
    assert "context.no_match" in event_types
    assert "draft.generated" in event_types


@pytest.mark.asyncio
async def test_flow_b_graph_unavailable_audits_no_match_and_drafts() -> None:
    ingest = _ingest()

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=True,
        )
        state.draft_status = "PENDING"
        return state

    async def _run_draft(state: EmailTriageState, **kwargs: object) -> EmailTriageState:
        assert kwargs.get("cross_thread_context") is None
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: docs",
            reply_body="Will follow up.",
            teaching_note="Graph unavailable; drafted without prior thread.",
            urgency="NORMAL",
            urgency_reason="Routine",
        )
        return state

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline_service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ),
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(_RESOLVE, new=AsyncMock()) as resolve,
        patch(_GRAPH, new=AsyncMock(return_value=None)),
        patch(_EMBED_SAFE, new=AsyncMock(return_value=None)),
        patch(
            "app.db.session.get_session_factory",
            return_value=MagicMock(),
        ),
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
    resolve.assert_not_awaited()
    event_types = [c.kwargs["event_type"] for c in audit.await_args_list]
    assert "context.no_match" in event_types


@pytest.mark.asyncio
async def test_flow_a_skips_context_resolution() -> None:
    ingest = _ingest()

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
        )
        state.draft_status = "PENDING"
        return state

    async def _run_draft(state: EmailTriageState, **kwargs: object) -> EmailTriageState:
        assert kwargs.get("cross_thread_context") is None
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="Re: docs",
            reply_body="Here you go.",
            teaching_note="Straightforward request.",
            urgency="NORMAL",
            urgency_reason="Routine",
        )
        return state

    with (
        patch(
            "app.services.pipeline_service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline_service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ),
        patch(
            "app.services.pipeline_service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(_RESOLVE, new=AsyncMock()) as resolve,
        patch(_EMBED_SAFE, new=AsyncMock(return_value=None)),
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
    resolve.assert_not_awaited()
    event_types = [c.kwargs["event_type"] for c in audit.await_args_list]
    assert "context.match" not in event_types
    assert "context.no_match" not in event_types


@pytest.mark.asyncio
async def test_resolve_graph_client_awaits_auth_with_redis() -> None:
    """Regression: sync call to async get_graph_auth broke Flow B in production."""
    settings = Settings(environment="local")
    auth = MagicMock(name="graph_auth")
    client = MagicMock(name="graph_client")
    redis = AsyncMock()

    with (
        patch(
            "app.core.dependencies.get_redis",
            new=AsyncMock(return_value=redis),
        ),
        patch(
            "app.core.dependencies.get_graph_auth",
            new=AsyncMock(return_value=auth),
        ) as get_auth,
        patch(
            "app.core.dependencies.get_graph_client",
            return_value=client,
        ) as get_client,
    ):
        resolved = await pipeline_service._resolve_graph_client(settings, None)

    assert resolved is client
    get_auth.assert_awaited_once_with(settings, redis)
    get_client.assert_called_once_with(auth)


@pytest.mark.asyncio
async def test_resolve_graph_client_returns_injected() -> None:
    injected = MagicMock()
    resolved = await pipeline_service._resolve_graph_client(
        Settings(environment="local"),
        injected,
    )
    assert resolved is injected
