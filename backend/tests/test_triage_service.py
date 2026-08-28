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


def test_decide_action_without_email_reply_still_pending() -> None:
    """Calendar RSVP: Elise must act, but Sonnet still runs a briefing (no letter)."""
    outcome, status = decide_triage_outcome(
        _triage(has_action_items=True, draft_needed=False),
    )
    assert outcome == "action_needed"
    assert status == "PENDING"


def test_decide_clamps_draft_needed_when_no_action_items() -> None:
    triage = _triage(has_action_items=False, draft_needed=True)
    outcome, status = decide_triage_outcome(triage)
    assert outcome == "no_action_discarded"
    assert status == "SKIPPED"
    assert triage.draft_needed is False


def test_legacy_triage_json_without_draft_needed_defaults_to_true() -> None:
    triage = TriageResultSchema.model_validate(
        {
            "is_spam": False,
            "has_action_items": True,
            "action_items_summary": "Reply to the client",
            "needs_context": False,
        }
    )
    assert triage.draft_needed is True


def _state(
    *,
    mailbox: str = "elise@example.com",
    sender: str = "vendor@example.com",
) -> EmailTriageState:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox=mailbox,
        sender=sender,
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


@pytest.mark.asyncio
async def test_run_triage_never_discards_internal_mail_as_spam() -> None:
    """Haiku calling same-domain mail spam must not skip the thread."""
    call = TriageCallResult(
        triage=_triage(
            is_spam=True,
            spam_reason="Looks automated",
            has_action_items=True,
            action_items_summary="Review the attached policy",
            routing_category="general",
        ),
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
            _state(mailbox="elise@sample-site.example.com", sender="hr@sample-site.example.com"),
            client=AsyncMock(),
            settings=Settings(),
        )
    assert state.triage is not None
    assert state.triage.is_spam is False
    assert state.triage.spam_reason is None
    assert state.draft_status == "PENDING"


@pytest.mark.asyncio
async def test_run_triage_never_discards_allowlisted_sender_as_spam() -> None:
    """A reviewer-corrected sender must not be discarded even if Haiku says spam."""
    call = TriageCallResult(
        triage=_triage(
            is_spam=True,
            spam_reason="Automated vendor mail",
            has_action_items=True,
            action_items_summary="Confirm SampleLab rebilling",
            routing_category="vendor",
        ),
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
            _state(mailbox="elise@sample-site.example.com", sender="orders@sample-lab.example.com"),
            client=AsyncMock(),
            settings=Settings(),
            allowlisted_senders=frozenset({"orders@sample-lab.example.com"}),
        )
    assert state.triage is not None
    assert state.triage.is_spam is False
    assert state.triage.spam_reason is None
    assert state.draft_status == "PENDING"
    assert state.triage.has_action_items is True
