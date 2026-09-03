"""DraftAssistant auto-close decisions: courtesy close, CC observer, inbound reopen."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.closing_mail import looks_like_closing_mail
from app.core.mailbox_keys import infer_mailbox_key
from app.models.schemas.email import EmailMessageSchema, ThreadStateEnum
from app.models.schemas.email_triage_state import EmailTriageState

_FINISHED_REOPEN_STATES = frozenset(
    {
        ThreadStateEnum.RESOLVED.value,
        ThreadStateEnum.NO_ACTION.value,
    }
)

REOPEN_ACTION_FINGERPRINT = "reopened-needs-action"

RESOLUTION_SUMMARIES = {
    "courtesy_close": "Courtesy close — sender thanked you; no reply needed.",
    "cc_observer": "CC observer — you were copied; no reply needed.",
    "manual": "You marked this thread resolved.",
    "wrong_action": "Marked no reply needed.",
}


@dataclass(frozen=True, slots=True)
class DraftAssistantAutoClose:
    reason: str
    confidence_tier: str
    summary: str


def is_inquiries_mailbox(mailbox: str) -> bool:
    local = mailbox.split("@", 1)[0].lower()
    key = str(infer_mailbox_key(mailbox))
    return key == "inquiries" or "inquir" in local


def is_cc_observer(
    email: EmailMessageSchema,
    *,
    owner_name: str | None = None,
) -> bool:
    mailbox = email.mailbox.strip().lower()
    to_set = {addr.strip().lower() for addr in email.to_recipients if addr and addr.strip()}
    cc_set = {addr.strip().lower() for addr in email.cc_recipients if addr and addr.strip()}
    if mailbox in to_set:
        return False
    if mailbox not in cc_set:
        return False
    if is_inquiries_mailbox(email.mailbox):
        return False
    body = (email.body_clean or email.body_text or "").lower()
    local = mailbox.split("@", 1)[0]
    if local and local in body:
        return False
    return not (owner_name and owner_name.strip() and owner_name.strip().lower() in body)


def draftassistant_auto_close_decision(
    state: EmailTriageState,
    *,
    owner_name: str | None = None,
) -> DraftAssistantAutoClose | None:
    triage = state.triage
    if triage is None or triage.is_spam or triage.has_action_items:
        return None
    email = state.original_email
    body = email.body_clean or email.body_text
    if looks_like_closing_mail(body):
        return DraftAssistantAutoClose(
            reason="courtesy_close",
            confidence_tier="high",
            summary=RESOLUTION_SUMMARIES["courtesy_close"],
        )
    if is_cc_observer(email, owner_name=owner_name):
        return DraftAssistantAutoClose(
            reason="cc_observer",
            confidence_tier="high",
            summary=RESOLUTION_SUMMARIES["cc_observer"],
        )
    return None


def should_reopen_finished_thread(*, prior_state: str, has_action_items: bool) -> bool:
    if prior_state not in _FINISHED_REOPEN_STATES:
        return False
    return has_action_items is True


def restore_disposition_from_snapshot(snapshot: dict[str, object] | None) -> str | None:
    if not snapshot:
        return None
    reason = str(snapshot.get("resolution_reason") or "")
    if reason == "courtesy_close":
        return "action_no_draft"
    if snapshot.get("had_draft") or snapshot.get("had_letter"):
        return "reply_review"
    disposition = snapshot.get("disposition_at_resolve")
    if disposition == "fyi_briefing":
        return "fyi_briefing"
    return None


def build_resolve_snapshot(
    *,
    resolved_by: str,
    resolution_reason: str,
    disposition_at_resolve: str | None = None,
    had_draft: bool = False,
    had_letter: bool = False,
    has_action_items: bool | None = None,
    draft_needed: bool | None = None,
    actions_taken: str | None = None,
    involved: str | None = None,
    confidence_tier: str | None = None,
) -> dict[str, object]:
    return {
        "resolved_by": resolved_by,
        "resolution_reason": resolution_reason,
        "disposition_at_resolve": disposition_at_resolve,
        "had_draft": had_draft,
        "had_letter": had_letter,
        "has_action_items": has_action_items,
        "draft_needed": draft_needed,
        "actions_taken": actions_taken,
        "involved": involved,
        "confidence_tier": confidence_tier,
    }
