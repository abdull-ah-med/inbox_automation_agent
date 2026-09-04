"""Promotion gate — golden-set check, trace replay, and cross-mailbox barrier.

Every proposal accept goes through these three gates before any state is written.
This module is read-only: it never creates rows or modifies urgency rules.
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.db.feedback_atom import FeedbackAtom
from app.models.db.message import Message
from app.models.db.urgency_rule import UrgencyRule
from app.repositories import urgency_prediction_repo
from app.repositories.golden_set_repo import list_golden_cases_by_mailbox
from app.repositories.promotion_proposal_repo import PromotionProposalSchema

logger = structlog.get_logger(__name__)

_MIN_MAILBOX_HITS = 30
_MIN_MAILBOXES = 2


def _condition_would_fire(condition: dict, case) -> bool:
    """Return True when the proposed rule's condition would match *case*."""
    if not condition:
        return False
    domain = condition.get("sender_domain")
    case_domain = getattr(case, "sender_domain", None)
    if domain:
        if case_domain:
            if str(case_domain).lower() != str(domain).lower():
                return False
        elif str(domain).lower() not in (case.email_text or "").lower():
            return False
    body_contains = condition.get("body_contains_any")
    if body_contains:
        body_lower = (case.email_text or "").lower()
        if not any(str(kw).lower() in body_lower for kw in body_contains):
            return False
    return True


def _action_changes_urgency(action: dict, expected_urgency: str) -> bool:
    target = action.get("set_urgency_floor") or action.get("set_urgency")
    if target is None:
        return False
    return str(target) != expected_urgency


async def evaluate_against_golden_set(
    session: AsyncSession,
    proposal: PromotionProposalSchema,
    *,
    settings: Settings | None = None,
) -> bool:
    """Return True if the proposal passes the golden-set gate (no regressions).

    Empty set: fail closed unless ``allow_empty_golden_set`` is on (dev only).
    Global target scope evaluates every configured mailbox's cases.
    Non-urgency kinds fail closed when any case has draft-body criteria (no LLM-judge).
    """
    cfg = settings or get_settings()
    payload = proposal.payload or {}
    target_scope = payload.get("requested_scope") or payload.get("target_scope")
    mailboxes = (
        list(cfg.mailbox_list)
        if target_scope == "global" and cfg.mailbox_list
        else [proposal.mailbox]
    )

    cases = []
    for mailbox in mailboxes:
        cases.extend(await list_golden_cases_by_mailbox(session, mailbox=mailbox))

    if not cases:
        logger.warning(
            "promotion_gate_no_golden_cases",
            proposal_id=str(proposal.id),
            mailbox=proposal.mailbox,
        )
        return bool(cfg.allow_empty_golden_set)

    if proposal.kind != "urgency_rule":
        logger.warning(
            "promotion_gate_unsupported_kind_needs_llm_judge",
            proposal_id=str(proposal.id),
            kind=proposal.kind,
        )
        return False

    action = payload.get("action", {})
    condition = payload.get("condition", {})

    for case in cases:
        if case.expected_urgency is None:
            continue
        if not _condition_would_fire(condition, case):
            continue
        if _action_changes_urgency(action, case.expected_urgency):
            logger.warning(
                "promotion_gate_golden_regression",
                proposal_id=str(proposal.id),
                mailbox=proposal.mailbox,
                case_id=str(case.id),
                expected=case.expected_urgency,
            )
            return False

    logger.info(
        "promotion_gate_golden_passed",
        proposal_id=str(proposal.id),
        mailbox=proposal.mailbox,
        cases_checked=len(cases),
    )
    return True


def _replay_applies(condition: dict, sender_domain: str, body_text: str | None) -> bool:
    domain = condition.get("sender_domain")
    if domain and str(domain).lower() != (sender_domain or "").lower():
        return False
    keywords = condition.get("body_contains_any")
    if keywords:
        if not body_text:
            return False
        body_lower = body_text.lower()
        if not any(str(kw).lower() in body_lower for kw in keywords):
            return False
    return bool(condition)


async def _inbound_bodies_by_thread(
    session: AsyncSession,
    thread_ids: list,
) -> dict:
    if not thread_ids:
        return {}
    stmt = (
        select(Message.thread_id, Message.body_text)
        .where(
            Message.thread_id.in_(thread_ids),
            Message.direction == "inbound",
        )
        .distinct(Message.thread_id)
        .order_by(Message.thread_id, Message.received_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    return {row[0]: row[1] or "" for row in rows}


async def replay_recent_traces(
    session: AsyncSession,
    proposal: PromotionProposalSchema,
    *,
    window_days: int = 30,
    limit: int = 200,
) -> float:
    """Side-effect-free replay against the last 200 urgency_predictions.

    Returns the fraction of traces whose final urgency would change.
    Raises ValueError when a body-gated rule cannot be evaluated (no inbound text).
    """
    _ = window_days
    if proposal.kind != "urgency_rule":
        return 0.0
    traces = await urgency_prediction_repo.list_recent_for_mailbox(
        session, proposal.mailbox, limit=limit
    )
    if not traces:
        return 0.0
    condition = proposal.payload.get("condition", {})
    action = proposal.payload.get("action", {})
    if condition.get("body_contains_any"):
        bodies = await _inbound_bodies_by_thread(session, [pred.thread_id for pred in traces])
        if not any(bodies.get(pred.thread_id) for pred in traces):
            raise ValueError(
                f"Proposal {proposal.id} body_contains_any cannot be replayed — "
                "no inbound message bodies for recent traces"
            )
    else:
        bodies = {}
    changed = 0
    for pred in traces:
        body = bodies.get(pred.thread_id)
        if not _replay_applies(condition, pred.sender_domain, body):
            continue
        target = action.get("set_urgency_floor") or action.get("set_urgency")
        if target is None:
            continue
        if str(target) != pred.final_urgency:
            changed += 1
    return changed / len(traces)


async def can_promote_to_global(
    session: AsyncSession,
    proposal: PromotionProposalSchema,
    *,
    mailbox_list: list[str] | None = None,
) -> bool:
    """True only with person_bound=False and ≥2 mailboxes each with ≥30 hits.

    Evidence mailboxes come from persisted atoms/rules matching this proposal's
    sender_domain (or evidence_ids). Unrelated 30-hit rows do not count.
    """
    _ = mailbox_list
    if proposal.payload.get("person_bound") is True:
        logger.info(
            "promote_to_global_refused_person_bound",
            proposal_id=str(proposal.id),
        )
        return False

    if proposal.evidence_ids:
        atom_bound = (
            await session.execute(
                select(func.bool_or(FeedbackAtom.person_bound)).where(
                    FeedbackAtom.id.in_(proposal.evidence_ids)
                )
            )
        ).scalar()
        if atom_bound:
            return False
        rule_bound = (
            await session.execute(
                select(func.bool_or(UrgencyRule.person_bound)).where(
                    UrgencyRule.id.in_(proposal.evidence_ids)
                )
            )
        ).scalar()
        if rule_bound:
            return False

    domain = (proposal.payload.get("condition") or {}).get("sender_domain")
    if not domain:
        logger.info(
            "promote_to_global_refused_no_shared_signal",
            proposal_id=str(proposal.id),
        )
        return False

    domain_key = f"domain:{str(domain).lower()}"
    qualifying: set[str] = set()

    atom_stmt = (
        select(FeedbackAtom.mailbox)
        .where(
            FeedbackAtom.person_bound.is_(False),
            FeedbackAtom.hit_count >= _MIN_MAILBOX_HITS,
            FeedbackAtom.is_active.is_(True),
            FeedbackAtom.scope_key == domain_key,
        )
        .group_by(FeedbackAtom.mailbox)
        .having(func.sum(FeedbackAtom.hit_count) >= _MIN_MAILBOX_HITS)
    )
    atom_rows = (await session.execute(atom_stmt)).all()
    qualifying.update(row[0] for row in atom_rows)

    rule_stmt = (
        select(UrgencyRule.mailbox)
        .where(
            UrgencyRule.person_bound.is_(False),
            UrgencyRule.hit_count >= _MIN_MAILBOX_HITS,
            UrgencyRule.status.in_(["active", "canary"]),
            UrgencyRule.scope_key == domain_key,
        )
        .group_by(UrgencyRule.mailbox)
        .having(func.sum(UrgencyRule.hit_count) >= _MIN_MAILBOX_HITS)
    )
    rule_rows = (await session.execute(rule_stmt)).all()
    qualifying.update(row[0] for row in rule_rows)

    if len(qualifying) < _MIN_MAILBOXES:
        logger.info(
            "promote_to_global_refused_insufficient_mailboxes",
            proposal_id=str(proposal.id),
            mailbox_count=len(qualifying),
        )
        return False
    return True
