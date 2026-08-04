"""Unit tests for triage_service branching (no Anthropic)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm.triage import TriageCallResult
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.services.triage_service import decide_triage_outcome, run_triage


def _triage(**kwargs: object) -> TriageResultSchema:
    base = {
        "is_spam": False,
        "spam_reason": None,
        "has_action_items": True,
        "action_items_summary": "Do thing",
        "needs_context": False,
        "context_reason": None,
    }
    base.update(kwargs)
    return TriageResultSchema.model_validate(base)


def test_decide_spam_discarded() -> None:
    outcome, status = decide_triage_outcome(_triage(is_spam=True, has_action_items=False))
    assert outcome == "spam_discarded"
    assert status == "SKIPPED"


def test_decide_spam_discards_even_with_action_items() -> None:
    """Spam flag wins — we never proceed spam to draft."""
    outcome, status = decide_triage_outcome(_triage(is_spam=True, has_action_items=True))
    assert outcome == "spam_discarded"
    assert status == "SKIPPED"


def test_decide_no_action_discarded() -> None:
    outcome, status = decide_triage_outcome(
        _triage(has_action_items=False, action_items_summary=None),
    )
    assert outcome == "no_action_discarded"
    assert status == "SKIPPED"


def test_decide_action_needed_preserves_needs_context() -> None:
    triage = _triage(needs_context=True, context_reason="prior thread")
    outcome, status = decide_triage_outcome(triage)
    assert outcome == "action_needed"
    assert status == "PENDING"
    assert triage.needs_context is True


def _state() -> EmailTriageState:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Hi",
        body_text="Please reply",
        received_at=datetime(2026, 7, 10, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )
    return EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id="c1",
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
    )


@pytest.mark.asyncio
async def test_run_triage_sets_pending_for_action_needed() -> None:
    call = TriageCallResult(
        triage=_triage(),
        prompt_version="v",
        model="claude-haiku-4-5",
        input_tokens=1,
        output_tokens=1,
        latency_ms=5,
    )
    with patch(
        "app.services.triage_service.triage_llm.triage_email",
        new=AsyncMock(return_value=call),
    ):
        state = await run_triage(
            _state(),
            client=AsyncMock(),
            settings=Settings(),
        )
    assert state.draft_status == "PENDING"
    assert state.triage is not None
    assert state.triage.has_action_items is True


@pytest.mark.asyncio
async def test_run_triage_failure_sets_requires_human() -> None:
    with patch(
        "app.services.triage_service.triage_llm.triage_email",
        new=AsyncMock(side_effect=TriageError("boom")),
    ):
        state = await run_triage(
            _state(),
            client=AsyncMock(),
            settings=Settings(),
        )
    assert state.draft_status == "REQUIRES_HUMAN"
    assert state.triage is None
    assert any(e.startswith("triage_failed:") for e in state.error_logs)
