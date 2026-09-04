"""Promotion service — accept, revert, and global-barrier for proposals.

All proposal state changes go through here. Includes chat-cache invalidation
on revert so stale urgency-rule-influenced responses are evicted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ProposalConflictError, ProposalNotFoundError
from app.core.scope_keys import expires_at_for_scope, resolve_widening_scope, scope_key_for
from app.repositories import (
    chat_cache_repo,
    feedback_atom_repo,
    promotion_proposal_repo,
    teaching_note_repo,
    urgency_rule_repo,
)
from app.repositories.promotion_proposal_repo import PromotionProposalSchema
from app.services import audit_service
from app.services.promotion_gate import (
    can_promote_to_global,
    evaluate_against_golden_set,
    replay_recent_traces,
)

logger = structlog.get_logger(__name__)

_REPLAY_CHANGE_RATE_THRESHOLD = 0.20


class HighChangeRateError(ValueError):
    """Replay change rate exceeds the canary threshold; requires a second click."""

    def __init__(self, proposal_id: uuid.UUID, change_rate: float) -> None:
        self.proposal_id = proposal_id
        self.change_rate = change_rate
        super().__init__(
            f"Proposal {proposal_id} has change_rate={change_rate:.2f} > "
            f"{_REPLAY_CHANGE_RATE_THRESHOLD} — requires admin second-click confirm."
        )


_SCOPE_LADDER = (
    "thread",
    "sender_address",
    "sender_domain",
    "mailbox+routing_category",
    "mailbox",
    "global",
)


def _resolved_target_scope(payload: dict, current_scope: str | None) -> str:
    requested = payload.get("requested_scope") or payload.get("target_scope")
    if requested:
        return str(requested)
    if current_scope:
        return _next_wider_scope(current_scope)
    return "mailbox"


async def accept_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    actor: str = "system",
    skip_gate: bool = False,
    confirm_high_change_rate: bool = False,
) -> PromotionProposalSchema:
    """Accept a pending proposal after running golden-set gate and trace replay."""
    proposal = await _get_pending(session, proposal_id)
    payload = dict(proposal.payload)

    current_scope = payload.get("from_scope") or payload.get("current_scope")
    target_scope = _resolved_target_scope(payload, current_scope)
    if (
        target_scope == "global"
        and not skip_gate
        and not await can_promote_to_global(session, proposal)
    ):
        raise ValueError(
            f"Proposal {proposal_id} cannot promote to global "
            "(need ≥2 mailboxes with ≥30 hits and person_bound=False)"
        )

    if not skip_gate:
        gate_ok = await evaluate_against_golden_set(session, proposal)
        if not gate_ok:
            raise ValueError(
                f"Proposal {proposal_id} failed the golden-set gate — "
                "it would regress at least one golden case."
            )
        change_rate = await replay_recent_traces(session, proposal)
        if change_rate > _REPLAY_CHANGE_RATE_THRESHOLD and not confirm_high_change_rate:
            raise HighChangeRateError(proposal_id, change_rate)

    async with session.begin_nested():
        updated = await promotion_proposal_repo.set_proposal_status(
            session,
            proposal_id,
            status="accepted",
            payload=payload,
            expected_from="pending",
        )
        if updated is None:
            raise ProposalConflictError(f"Proposal {proposal_id} is not pending (status conflict)")

        if proposal.kind == "urgency_rule":
            rule = await _create_urgency_rule_from_proposal(session, proposal)
            if rule is not None:
                payload["created_rule_id"] = str(rule.id)
        elif proposal.kind == "atom_widening":
            atom = await _create_widened_atom(session, proposal)
            if atom is not None:
                payload["created_atom_id"] = str(atom.id)
        elif proposal.kind == "note_widening":
            note_id = await _widen_teaching_note(session, proposal, payload)
            if note_id is not None:
                payload["note_id"] = str(note_id)
        updated = await promotion_proposal_repo.set_proposal_status(
            session,
            proposal_id,
            status="accepted",
            payload=payload,
        )
        if updated is None:
            raise ProposalNotFoundError(f"Proposal {proposal_id} not found after accept")

    try:
        await audit_service.log_event(
            session,
            event_type="promotion.accepted",
            conversation_id=f"promotion:{proposal_id}",
            mailbox=proposal.mailbox,
            payload={"proposal_id": str(proposal_id), "kind": proposal.kind},
            actor=actor,
        )
    except Exception:
        logger.warning("promotion_accept_audit_failed", proposal_id=str(proposal_id))

    return updated


async def dismiss_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    actor: str = "system",
) -> PromotionProposalSchema:
    """Dismiss a pending proposal. Refuses accepted/reverted rows."""
    proposal = await _get_pending(session, proposal_id)
    updated = await promotion_proposal_repo.set_proposal_status(
        session,
        proposal_id,
        status="dismissed",
        expected_from="pending",
    )
    if updated is None:
        raise ProposalConflictError(f"Proposal {proposal_id} is not pending (status conflict)")
    try:
        await audit_service.log_event(
            session,
            event_type="promotion.dismissed",
            conversation_id=f"promotion:{proposal_id}",
            mailbox=proposal.mailbox,
            payload={"proposal_id": str(proposal_id), "kind": proposal.kind},
            actor=actor,
        )
    except Exception:
        logger.warning("promotion_dismiss_audit_failed", proposal_id=str(proposal_id))
    return updated


async def revert_promotion(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    *,
    actor: str = "system",
) -> PromotionProposalSchema:
    """Revert an accepted proposal: undo only the created target; status → reverted."""
    proposal = await _get_accepted(session, proposal_id)
    payload = proposal.payload

    if proposal.kind == "urgency_rule":
        await _archive_created_rule(session, payload)
    elif proposal.kind == "atom_widening":
        await _deactivate_created_atom(session, payload)
    elif proposal.kind == "note_widening":
        await _restore_note_scope(session, payload)

    updated = await promotion_proposal_repo.set_proposal_status(
        session, proposal_id, status="reverted"
    )
    if updated is None:
        raise ProposalNotFoundError(f"Proposal {proposal_id} not found after revert")

    try:
        await chat_cache_repo.invalidate_for_mailbox(session, proposal.mailbox)
        logger.info(
            "promotion_revert_cache_invalidated",
            mailbox=proposal.mailbox,
            proposal_id=str(proposal_id),
        )
    except Exception:
        logger.warning("promotion_revert_cache_invalidate_failed", proposal_id=str(proposal_id))

    try:
        await audit_service.log_event(
            session,
            event_type="promotion.reverted",
            conversation_id=f"promotion:{proposal_id}",
            mailbox=proposal.mailbox,
            payload={"proposal_id": str(proposal_id), "kind": proposal.kind},
            actor=actor,
        )
    except Exception:
        logger.warning("promotion_revert_audit_failed", proposal_id=str(proposal_id))

    logger.info(
        "promotion_reverted",
        proposal_id=str(proposal_id),
        mailbox=proposal.mailbox,
        kind=proposal.kind,
    )
    return updated


async def _get_pending(
    session: AsyncSession,
    proposal_id: uuid.UUID,
) -> PromotionProposalSchema:
    from app.models.db.promotion_proposal import PromotionProposal

    row = (
        await session.execute(
            select(PromotionProposal).where(PromotionProposal.id == proposal_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
    if row.status != "pending":
        raise ProposalConflictError(
            f"Proposal {proposal_id} is not pending (status={row.status!r})"
        )
    return PromotionProposalSchema.model_validate(row)


async def _get_accepted(
    session: AsyncSession,
    proposal_id: uuid.UUID,
) -> PromotionProposalSchema:
    from app.models.db.promotion_proposal import PromotionProposal

    row = (
        await session.execute(
            select(PromotionProposal).where(PromotionProposal.id == proposal_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise ProposalNotFoundError(f"Proposal {proposal_id} not found")
    if row.status != "accepted":
        raise ProposalConflictError(
            f"Proposal {proposal_id} is not accepted (status={row.status!r})"
        )
    return PromotionProposalSchema.model_validate(row)


def _next_wider_scope(scope: str) -> str:
    try:
        idx = _SCOPE_LADDER.index(scope)
    except ValueError:
        return "mailbox"
    # Auto-widen never jumps to global — that requires an explicit requested_scope.
    next_scope = _SCOPE_LADDER[min(idx + 1, len(_SCOPE_LADDER) - 1)]
    if next_scope == "global":
        return "mailbox"
    return next_scope


async def _create_urgency_rule_from_proposal(
    session: AsyncSession,
    proposal: PromotionProposalSchema,
):
    payload = proposal.payload
    condition = payload.get("condition", {})
    action = payload.get("action", {})
    if not condition or not action:
        logger.warning(
            "urgency_rule_create_skipped_empty_payload",
            proposal_id=str(proposal.id),
        )
        return None

    from app.services.urgency_rule_service import sanitize_rule_action, sanitize_rule_condition

    condition = sanitize_rule_condition(condition)
    action = sanitize_rule_action(action)
    if not condition or not action:
        logger.warning(
            "urgency_rule_create_skipped_empty_payload",
            proposal_id=str(proposal.id),
        )
        return None
    domain = condition.get("sender_domain", "unknown")
    rule = await urgency_rule_repo.create_urgency_rule(
        session,
        mailbox=proposal.mailbox,
        scope="sender_domain",
        scope_key=scope_key_for("sender_domain", sender_domain=str(domain)),
        condition=condition,
        action=action,
        status="canary",
        canary_until=datetime.now(UTC) + timedelta(hours=48),
        impact_num=proposal.impact_num,
        impact_den=proposal.impact_den,
    )
    logger.info(
        "urgency_rule_created_from_proposal",
        proposal_id=str(proposal.id),
        mailbox=proposal.mailbox,
        rule_id=str(rule.id),
    )
    return rule


async def _create_widened_atom(session: AsyncSession, proposal: PromotionProposalSchema):
    payload = proposal.payload
    source_id = None
    if proposal.evidence_ids:
        source_id = proposal.evidence_ids[0]
    raw = payload.get("atom_id") or payload.get("from_atom_id")
    if source_id is None and raw:
        source_id = uuid.UUID(str(raw))
    if source_id is None:
        logger.warning("atom_widening_missing_source", proposal_id=str(proposal.id))
        return None
    source = await feedback_atom_repo.get_atom_by_id(session, source_id)
    if source is None:
        logger.warning("atom_widening_source_missing", atom_id=str(source_id))
        return None

    requested = _resolved_target_scope(payload, source.scope)
    target_scope, target_key = resolve_widening_scope(
        requested,
        mailbox=proposal.mailbox,
        source_scope_key=source.scope_key,
        payload=payload,
    )

    from app.models.db.feedback_atom import FeedbackAtom

    row = (
        await session.execute(select(FeedbackAtom).where(FeedbackAtom.id == source.id))
    ).scalar_one_or_none()
    if row is None or row.atom_embedding is None:
        logger.warning("atom_widening_missing_embedding", atom_id=str(source.id))
        return None
    embedding = list(row.atom_embedding)

    return await feedback_atom_repo.insert_atom(
        session,
        source_kind=source.source_kind,
        source_id=source.source_id,
        mailbox=proposal.mailbox,
        atom_text=source.atom_text,
        atom_embedding=embedding,
        role="Fix",
        scope=target_scope,
        scope_key=target_key,
        applies_when=source.applies_when,
        person_bound=source.person_bound,
        promoted_from_atom_id=source.id,
        expires_at=expires_at_for_scope(target_scope),
    )


async def _widen_teaching_note(
    session: AsyncSession,
    proposal: PromotionProposalSchema,
    payload: dict,
) -> uuid.UUID | None:
    raw = payload.get("note_id")
    if not raw and proposal.evidence_ids:
        raw = proposal.evidence_ids[0]
    if raw is None:
        return None
    note_id = uuid.UUID(str(raw))
    note = await teaching_note_repo.get_teaching_note_by_id(session, note_id)
    if note is None:
        return None
    payload["previous_scope"] = note.scope
    payload["previous_scope_key"] = note.scope_key
    payload["previous_expires_at"] = note.expires_at.isoformat() if note.expires_at else None
    requested = _resolved_target_scope(payload, note.scope)
    scope, key = resolve_widening_scope(
        requested,
        mailbox=proposal.mailbox,
        source_scope_key=note.scope_key,
        payload=payload,
    )
    await teaching_note_repo.update_teaching_note_scope(
        session,
        note_id,
        scope=scope,
        scope_key=key,
        expires_at=expires_at_for_scope(scope),
    )
    return note_id


def _parse_uuid(raw: object) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


async def _archive_created_rule(session: AsyncSession, payload: dict) -> None:
    rule_id = _parse_uuid(payload.get("created_rule_id"))
    if rule_id is None:
        return
    await urgency_rule_repo.set_urgency_rule_status(session, rule_id, "archived")


async def _deactivate_created_atom(session: AsyncSession, payload: dict) -> None:
    atom_id = _parse_uuid(payload.get("created_atom_id"))
    if atom_id is None:
        return
    await feedback_atom_repo.deactivate_atom(session, atom_id)


async def _restore_note_scope(session: AsyncSession, payload: dict) -> None:
    note_id = _parse_uuid(payload.get("note_id"))
    prev_scope = payload.get("previous_scope")
    prev_key = payload.get("previous_scope_key")
    if note_id is None or not prev_scope or not prev_key:
        return
    raw_exp = payload.get("previous_expires_at")
    expires_at = None
    if raw_exp:
        expires_at = datetime.fromisoformat(str(raw_exp))
    await teaching_note_repo.update_teaching_note_scope(
        session,
        note_id,
        scope=prev_scope,
        scope_key=prev_key,
        expires_at=expires_at,
    )
