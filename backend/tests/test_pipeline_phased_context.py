"""Unit tests for cross-thread context wiring in the phased (webhook/poll) path.

Covers the same "skip Graph + embedding search when OPENAI_API_KEY is unset"
behavior as ``test_flow_b_pipeline.py``, but for ``_run_phased_post_ingest`` —
the production webhook/poll entry point (``run_after_ingest`` is only used by
tests and the simulate/in-session callers).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.graph import IngestResultSchema
from app.services import pipeline_service

_GRAPH = "app.services.pipeline.service._resolve_graph_client"
_RESOLVE = "app.services.pipeline.service.context_service.resolve_cross_thread_context"
_GENERATE = "app.services.pipeline.service.draft_llm.generate_draft"
_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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
        thread_id=_THREAD_ID,
        conversation_id="c1",
        thread_context=context,
    )


def _session_factory() -> MagicMock:
    """``async with factory() as session, session.begin():`` compatible mock."""
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)

    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=factory_cm)


@pytest.mark.asyncio
async def test_phased_skips_graph_and_context_when_openai_unconfigured() -> None:
    """No OPENAI_API_KEY: never touch Graph/embedding search; draft plain."""

    async def _run_triage(state: object, **_: object) -> object:
        state.triage = TriageResultSchema(  # type: ignore[attr-defined]
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=True,
            context_reason="maybe prior thread",
        )
        state.draft_status = "PENDING"  # type: ignore[attr-defined]
        return state

    generated = MagicMock()
    generated.draft = DraftSchema(
        subject_line="Re: As discussed — need docs",
        reply_body="Happy to help — could you clarify which packet?",
        teaching_note="No OpenAI key configured; drafted without context search.",
        urgency="NORMAL",
        urgency_reason="Routine follow-up",
    )
    generated.prompt_version = "v1"
    generated.model = "claude-test"
    generated.latency_ms = 10

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
            "app.services.pipeline.service.audit_service.log_event",
            new=AsyncMock(),
        ) as audit,
        patch(_GRAPH, new=AsyncMock()) as resolve_graph,
        patch(_RESOLVE, new=AsyncMock()) as resolve_context,
        patch(_GENERATE, new=AsyncMock(return_value=generated)) as generate,
        patch(
            "app.services.pipeline.service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.skill_repo.list_active_for_selection",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.pipeline.service.skill_selection_service.select_from_active",
            new=AsyncMock(
                return_value=MagicMock(
                    blocks=[],
                    skill_ids=[],
                    applied=[],
                    candidate_ids=[],
                    selected_pool_ids=[],
                    always_ids=[],
                )
            ),
        ),
        patch(
            "app.services.pipeline.service.skill_selection_service.log_skills_selected",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.tone_profile_service.load_for_draft",
            new=AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.pipeline.service.rejection_memory_service.find_negative_constraints",
            new=AsyncMock(return_value=[]),
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
            "app.services.pipeline.service.draft_repo.create_draft",
            new=AsyncMock(
                return_value=MagicMock(model_dump=lambda **_: generated.draft.model_dump())
            ),
        ),
        patch(
            "app.services.pipeline.service.thread_repo.set_thread_outcome",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.core.dependencies.openai_client_from_settings",
            return_value=None,
        ),
    ):
        state = await pipeline_service._run_phased_post_ingest(
            redis=AsyncMock(),
            settings=Settings(environment="local", openai_api_key=""),
            client=AsyncMock(),
            openai_client=None,
            ingest_result=_ingest(),
            session_factory=_session_factory(),
            graph_client=MagicMock(),
            post_slack=False,
        )

    assert state.draft_status == "DRAFTED"
    assert state.cross_thread_context is None
    resolve_graph.assert_not_awaited()
    resolve_context.assert_not_awaited()
    generate.assert_awaited_once()
    await_args = generate.await_args
    assert await_args is not None
    assert await_args.kwargs["cross_thread_context"] is None
    event_types = [c.kwargs["event_type"] for c in audit.await_args_list]
    assert "context.match" not in event_types
    assert "context.no_match" not in event_types
