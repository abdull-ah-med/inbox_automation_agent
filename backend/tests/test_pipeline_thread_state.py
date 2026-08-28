"""Unit tests for thread state/urgency persistence during the pipeline.

Covers the write-back added so ``threads.state``/``threads.urgency`` reflect
real triage/draft outcomes instead of staying at ``NEW``/``None`` forever —
the data the frontend needs to show State/Urgency/Teaching note and to
filter SPAM/NO_ACTION out of the default views.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.models.schemas.graph import IngestResultSchema
from app.services import pipeline_service

_EMBED_SAFE = "app.services.pipeline.service.embedding_service.embed_and_store_safe"
_SET_OUTCOME = "app.services.pipeline.service.thread_repo.set_thread_outcome"
_THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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


async def _run(*, run_triage: object, run_draft: object) -> tuple[object, AsyncMock]:
    with (
        patch("app.services.pipeline.service.triage_service.run_triage", new=run_triage),
        patch("app.services.pipeline.service.draft_service.run_draft", new=run_draft),
        patch("app.services.pipeline.service.audit_service.log_event", new=AsyncMock()),
        patch(_SET_OUTCOME, new=AsyncMock(return_value=None)) as set_outcome,
        _patch_embed(),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            ingest_result=_ingest(),
        )
    return state, set_outcome


@pytest.mark.asyncio
async def test_spam_outcome_persists_thread_state_spam() -> None:
    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=True,
            has_action_items=False,
            action_items_summary=None,
            needs_context=False,
        )
        state.draft_status = "SKIPPED"
        return state

    async def _run_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        return state

    _state, set_outcome = await _run(
        run_triage=AsyncMock(side_effect=_run_triage),
        run_draft=AsyncMock(side_effect=_run_draft),
    )

    set_outcome.assert_awaited_once()
    args, kwargs = set_outcome.await_args
    assert str(args[1]) == _THREAD_ID
    assert kwargs["state"] == "SPAM"
    assert "urgency" not in kwargs or kwargs["urgency"] is None


@pytest.mark.asyncio
async def test_no_action_outcome_still_persists_drafted_briefing() -> None:
    """FYI mail still gets a briefing row (empty letter) so teaching notes exist."""

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=False,
            action_items_summary=None,
            needs_context=False,
            draft_needed=False,
        )
        state.draft_status = "PENDING"
        return state

    async def _run_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.draft_status = "DRAFTED"
        state.draft = DraftSchema(
            subject_line="FYI",
            reply_body="",
            teaching_note="Listserv — no reply.",
            urgency="LOW",
            urgency_reason="FYI blast",
            suggested_actions=[],
        )
        return state

    _state, set_outcome = await _run(
        run_triage=AsyncMock(side_effect=_run_triage),
        run_draft=AsyncMock(side_effect=_run_draft),
    )

    assert _state.draft_status == "DRAFTED"
    states = [call.kwargs["state"] for call in set_outcome.await_args_list]
    assert "NO_ACTION" not in states
    assert "DRAFTED" in states


@pytest.mark.asyncio
async def test_drafted_outcome_persists_state_and_urgency() -> None:
    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Send packet",
            needs_context=False,
        )
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

    _state, set_outcome = await _run(
        run_triage=AsyncMock(side_effect=_run_triage),
        run_draft=AsyncMock(side_effect=_run_draft),
    )

    # Called twice: once for triage (no-op, action_needed has no direct state)
    # would be skipped entirely, so only the draft step calls set_thread_outcome.
    set_outcome.assert_awaited_once()
    _args, kwargs = set_outcome.await_args
    assert kwargs["state"] == "DRAFTED"
    assert kwargs["urgency"] == "HIGH"


@pytest.mark.asyncio
async def test_draft_requires_human_persists_state_without_urgency() -> None:
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

    _state, set_outcome = await _run(
        run_triage=AsyncMock(side_effect=_run_triage),
        run_draft=AsyncMock(side_effect=_fail_draft),
    )

    set_outcome.assert_awaited_once()
    _args, kwargs = set_outcome.await_args
    assert kwargs["state"] == "REQUIRES_HUMAN"
    assert kwargs.get("urgency") is None


@pytest.mark.asyncio
async def test_thread_state_update_failure_does_not_crash_pipeline() -> None:
    """A thread lookup miss (e.g. concurrent delete) must not fail the pipeline."""

    async def _run_triage(state: EmailTriageState, **_: object) -> EmailTriageState:
        state.triage = TriageResultSchema(
            is_spam=True,
            has_action_items=False,
            action_items_summary=None,
            needs_context=False,
        )
        state.draft_status = "SKIPPED"
        return state

    async def _run_draft(state: EmailTriageState, **_: object) -> EmailTriageState:
        return state

    with (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.pipeline.service.draft_service.run_draft",
            new=AsyncMock(side_effect=_run_draft),
        ),
        patch("app.services.pipeline.service.audit_service.log_event", new=AsyncMock()),
        patch(_SET_OUTCOME, new=AsyncMock(side_effect=RuntimeError("db down"))),
        _patch_embed(),
    ):
        state = await pipeline_service.run_after_ingest(
            session=AsyncMock(),
            redis=AsyncMock(),
            settings=Settings(environment="local"),
            client=AsyncMock(),
            openai_client=AsyncMock(),
            ingest_result=_ingest(),
        )

    assert state.draft_status == "SKIPPED"
