"""Triage orchestration — Haiku call + deterministic branching (SOW discard rules)."""

from __future__ import annotations

from typing import Literal

import structlog
from anthropic import AsyncAnthropic

from app.core.config import Settings
from app.core.draft_needed import resolve_draft_needed
from app.core.exceptions import TriageError
from app.core.internal_mail import apply_internal_mail_policy
from app.core.spam_allowlist import apply_spam_allowlist_policy
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
    if triage.draft_needed and not triage.has_action_items:
        triage.draft_needed = False
    if triage.is_spam:
        return "spam_discarded", "SKIPPED"
    # Teaching note + suggested_actions always run except spam. Only the letter
    # is optional (``draft_needed``). FYI / courtesy-close still brief.
    if not triage.has_action_items:
        return "no_action_discarded", "PENDING"
    return "action_needed", "PENDING"


async def run_triage(
    state: EmailTriageState,
    *,
    client: AsyncAnthropic,
    settings: Settings,
    allowlisted_senders: frozenset[str] | None = None,
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

    triage = apply_internal_mail_policy(
        result.triage,
        sender=state.original_email.sender,
        mailbox=state.original_email.mailbox,
        extra_domains=settings.internal_domain_list,
    )
    triage = apply_spam_allowlist_policy(
        triage,
        sender=state.original_email.sender,
        allowlisted_addresses=allowlisted_senders or frozenset(),
    )
    triage.draft_needed = resolve_draft_needed(
        email=state.original_email,
        triage=triage,
    )
    # Letter implies an action (reply). Lift so decide() cannot clamp a
    # personal invite down when Haiku missed has_action_items.
    if triage.draft_needed:
        triage.has_action_items = True
    state.triage = triage
    outcome, draft_status = decide_triage_outcome(triage)
    state.draft_status = draft_status
    logger.info(
        "triage_branched",
        conversation_id=state.original_email.conversation_id,
        mailbox=state.original_email.mailbox,
        outcome=outcome,
        draft_status=draft_status,
        prompt_version=result.prompt_version,
        is_spam=triage.is_spam,
        has_action_items=triage.has_action_items,
        draft_needed=triage.draft_needed,
        needs_context=triage.needs_context,
        is_automated=triage.is_automated,
    )
    return state
