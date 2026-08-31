"""Deterministic thread presentation: one Now story from orthogonal axes.

Lifecycle (threads.state) is authoritative for finished vs open. Frozen triage
flags are history. Urgency assessed is retained; urgency_active drives queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.schemas.email import ThreadStateEnum

_FINISHED_STATES = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
        ThreadStateEnum.SPAM.value,
    }
)
_NEEDS_ATTENTION_STATES = frozenset(
    {
        ThreadStateEnum.DRAFTED.value,
        ThreadStateEnum.REQUIRES_HUMAN.value,
    }
)
_WAITING_STATES = frozenset(
    {
        ThreadStateEnum.AWAITING_CLIENT.value,
        ThreadStateEnum.AWAITING_VENDOR.value,
        ThreadStateEnum.AWAITING_PARTNER.value,
    }
)

_STATE_LABELS = {
    ThreadStateEnum.NEW.value: "New",
    ThreadStateEnum.SPAM.value: "Spam",
    ThreadStateEnum.NO_ACTION.value: "No action",
    ThreadStateEnum.REQUIRES_HUMAN.value: "Needs human",
    ThreadStateEnum.DRAFTED.value: "Drafted",
    ThreadStateEnum.AWAITING_CLIENT.value: "Awaiting client",
    ThreadStateEnum.AWAITING_VENDOR.value: "Awaiting vendor",
    ThreadStateEnum.AWAITING_PARTNER.value: "Awaiting partner",
    ThreadStateEnum.RESOLVED.value: "Resolved",
}


class BadgeKind(StrEnum):
    STATE = "state"
    URGENCY = "urgency"
    INTERNAL = "internal"
    AUTOMATED = "automated"
    AUTOMATED_ACTION = "automated_action"
    ACTION_NEEDED = "action_needed"
    NEEDS_CONTEXT = "needs_context"
    CATEGORY = "category"
    NOT_SPAM = "not_spam"


@dataclass(frozen=True, slots=True)
class BadgeNow:
    kind: BadgeKind
    label: str


@dataclass(frozen=True, slots=True)
class TriageHistoryView:
    has_action_items: bool | None
    needs_context: bool | None
    is_spam: bool | None
    action_items_summary: str | None = None
    context_reason: str | None = None
    spam_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ThreadPolicyInput:
    state: str
    urgency: str | None
    has_action_items: bool | None
    needs_context: bool | None
    is_spam: bool | None
    is_internal: bool
    is_automated: bool
    draft_review_finished: bool = False
    closing_signal: bool = False
    category: str | None = None
    action_items_summary: str | None = None
    context_reason: str | None = None
    spam_reason: str | None = None
    resolution_reason_corrected: bool = False


@dataclass(frozen=True, slots=True)
class ThreadPresentation:
    is_finished: bool
    open_work: bool
    in_needs_attention: bool
    urgency_assessed: str | None
    urgency_active: bool
    badges_now: tuple[BadgeNow, ...]
    triage_history: TriageHistoryView
    suggest_resolve_default: bool
    show_resolution_banner: bool


def derive_presentation(inp: ThreadPolicyInput) -> ThreadPresentation:
    state = (inp.state or "").strip().upper()
    is_finished = state in _FINISHED_STATES
    in_needs_attention = (
        state in _NEEDS_ATTENTION_STATES and not inp.draft_review_finished and not is_finished
    )
    # Finished always means no open work, even if frozen triage said action needed.
    if is_finished or inp.draft_review_finished:
        open_work = False
    elif inp.has_action_items is True:
        open_work = True
    elif inp.has_action_items is False:
        open_work = False
    else:
        open_work = in_needs_attention

    urgency_assessed = inp.urgency
    urgency_active = bool(urgency_assessed) and not is_finished and open_work

    badges: list[BadgeNow] = [
        BadgeNow(BadgeKind.STATE, _STATE_LABELS.get(state, state.title() or "Unknown"))
    ]
    if urgency_active and urgency_assessed:
        badges.append(BadgeNow(BadgeKind.URGENCY, urgency_assessed))
    if inp.is_internal:
        badges.append(BadgeNow(BadgeKind.INTERNAL, "Internal"))
    if inp.is_automated and open_work:
        badges.append(BadgeNow(BadgeKind.AUTOMATED_ACTION, "Automated · action needed"))
    elif inp.is_automated:
        badges.append(BadgeNow(BadgeKind.AUTOMATED, "Automated"))
    if open_work and inp.has_action_items is True and not inp.is_automated:
        badges.append(BadgeNow(BadgeKind.ACTION_NEEDED, "Action needed"))
    if open_work and inp.needs_context is True:
        badges.append(BadgeNow(BadgeKind.NEEDS_CONTEXT, "Needs context"))
    if inp.category and inp.category.strip():
        badges.append(BadgeNow(BadgeKind.CATEGORY, inp.category.strip()))

    waiting = state in _WAITING_STATES
    suggest_resolve = False
    if not is_finished and not waiting:
        suggest_resolve = bool(inp.closing_signal)

    return ThreadPresentation(
        is_finished=is_finished,
        open_work=open_work,
        in_needs_attention=in_needs_attention,
        urgency_assessed=urgency_assessed,
        urgency_active=urgency_active,
        badges_now=tuple(badges),
        triage_history=TriageHistoryView(
            has_action_items=inp.has_action_items,
            needs_context=inp.needs_context,
            is_spam=inp.is_spam,
            action_items_summary=inp.action_items_summary,
            context_reason=inp.context_reason,
            spam_reason=inp.spam_reason,
        ),
        suggest_resolve_default=suggest_resolve,
        show_resolution_banner=(
            is_finished
            and state == ThreadStateEnum.RESOLVED.value
            and not inp.resolution_reason_corrected
        ),
    )


def recurrence_urgency_floor(
    *,
    automated_inbound_count_48h: int,
    is_finished: bool = False,
) -> str | None:
    """Hard floor for same-thread recurring automated alerts while open."""
    if is_finished or automated_inbound_count_48h < 2:
        return None
    if automated_inbound_count_48h >= 3:
        return "CRITICAL"
    return "HIGH"


_URGENCY_RANK = {"LOW": 0, "NORMAL": 1, "HIGH": 2, "CRITICAL": 3}


def max_urgency(assessed: str | None, floor: str | None) -> str | None:
    if floor is None:
        return assessed
    if assessed is None:
        return floor
    left = _URGENCY_RANK.get(assessed.upper(), -1)
    right = _URGENCY_RANK.get(floor.upper(), -1)
    return floor if right >= left else assessed


def presentation_from_flags(
    *,
    state: str,
    urgency: str | None,
    triage: object | None,
    draft_review_finished: bool = False,
    closing_signal: bool = False,
    category: str | None = None,
    resolution_reason_corrected: bool = False,
) -> ThreadPresentation:
    """Build presentation from thread row + optional TriageFlags-like object."""
    has_action = getattr(triage, "has_action_items", None) if triage is not None else None
    needs_context = getattr(triage, "needs_context", None) if triage is not None else None
    is_spam = getattr(triage, "is_spam", None) if triage is not None else None
    is_internal = bool(getattr(triage, "is_internal", False)) if triage is not None else False
    is_automated = bool(getattr(triage, "is_automated", False)) if triage is not None else False
    return derive_presentation(
        ThreadPolicyInput(
            state=state,
            urgency=urgency,
            has_action_items=has_action,
            needs_context=needs_context,
            is_spam=is_spam,
            is_internal=is_internal,
            is_automated=is_automated,
            draft_review_finished=draft_review_finished,
            closing_signal=closing_signal,
            category=category,
            action_items_summary=(
                getattr(triage, "action_items_summary", None) if triage is not None else None
            ),
            context_reason=(
                getattr(triage, "context_reason", None) if triage is not None else None
            ),
            spam_reason=getattr(triage, "spam_reason", None) if triage is not None else None,
            resolution_reason_corrected=resolution_reason_corrected,
        )
    )
