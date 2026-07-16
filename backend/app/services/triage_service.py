"""Triage orchestration — Haiku call + deterministic branching (SOW discard rules)."""

from __future__ import annotations

from typing import Literal

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.core.exceptions import TriageError
from app.llm import triage as triage_llm
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email_triage_state import DraftStatus, EmailTriageState

logger = structlog.get_logger(__name__)

TriageOutcome = Literal[
    "spam_discarded",
    "no_action_discarded",
    "action_needed",
]


def decide_triage_outcome(
    triage: TriageResultSchema,
) -> tuple[TriageOutcome, DraftStatus]:
    if triage.is_spam:
        return "spam_discarded", "SKIPPED"
    if not triage.has_action_items:
        return "no_action_discarded", "SKIPPED"
    return "action_needed", "PENDING"


async def run_triage(
    state: EmailTriageState,
    *,
    client: AsyncAnthropic,
    settings: Settings,
) -> EmailTriageState:
    try:
        result = await triage_llm.triage_email(
            client=client,
            settings=settings,
            email=state.original_email,
            thread_context=state.thread_context,
        )
    except TriageError as exc:
        logger.warning(
            "triage_failed",
            conversation_id=state.original_email.conversation_id,
            mailbox=state.original_email.mailbox,
            error_type=type(exc).__name__,
        )
        state.draft_status = "REQUIRES_HUMAN"
        state.error_logs.append(f"triage_failed:{type(exc).__name__}")
        return state

    state.triage = result.triage
    outcome, draft_status = decide_triage_outcome(result.triage)
    state.draft_status = draft_status
    logger.info(
        "triage_branched",
        conversation_id=state.original_email.conversation_id,
        mailbox=state.original_email.mailbox,
        outcome=outcome,
        draft_status=draft_status,
        prompt_version=result.prompt_version,
        is_spam=result.triage.is_spam,
        has_action_items=result.triage.has_action_items,
        needs_context=result.triage.needs_context,
    )
    return state
