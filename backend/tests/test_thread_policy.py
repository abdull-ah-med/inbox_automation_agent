"""Independent oracles for thread presentation policy.

Worked example (Elise Sample case):
  state=RESOLVED, urgency=HIGH, triage.has_action_items=True, is_internal=True
  → open_work False; Now badges exclude Action needed; urgency_active False;
    assessed urgency still HIGH; history may still show action needed.
"""

from __future__ import annotations

from app.core.thread_policy import (
    BadgeKind,
    DispositionKind,
    ThreadPolicyInput,
    derive_presentation,
    recurrence_urgency_floor,
)


def test_resolved_with_stale_action_needed_is_not_open_work() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="RESOLVED",
            urgency="HIGH",
            has_action_items=True,
            needs_context=True,
            is_spam=False,
            is_internal=True,
            is_automated=False,
        )
    )
    assert view.open_work is False
    assert view.is_finished is True
    assert view.in_needs_attention is False
    assert view.urgency_assessed == "HIGH"
    assert view.urgency_active is False
    labels = [b.label for b in view.badges_now]
    assert view.disposition == DispositionKind.RESOLVED_DRAFTASSISTANT
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Resolved by DraftAssistant"
    assert "Resolved by DraftAssistant" in labels
    assert "Drafted" not in labels
    assert "Action needed" not in labels
    assert "Needs context" not in labels
    assert "Internal" in labels
    assert view.triage_history.has_action_items is True
    assert view.triage_history.needs_context is True


def test_drafted_action_needed_is_open_work_with_urgency_active() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="HIGH",
            has_action_items=True,
            needs_context=True,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.open_work is True
    assert view.is_finished is False
    assert view.in_needs_attention is True
    assert view.urgency_active is True
    labels = [b.label for b in view.badges_now]
    assert view.disposition == DispositionKind.ACTION_NO_DRAFT
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Action needed"
    assert "Drafted" not in labels
    assert "Action needed" in labels
    assert "Needs context" in labels
    assert "HIGH" in labels


def test_approved_draft_drops_needs_attention_while_still_drafted() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            draft_review_finished=True,
        )
    )
    assert view.in_needs_attention is False
    assert view.open_work is False


def test_body_internal_keyword_does_not_affect_policy() -> None:
    """Body text is not an input; only explicit is_internal flag matters."""
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert "Internal" not in [b.label for b in view.badges_now]


def test_automated_no_action_finished_path() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="NO_ACTION",
            urgency="LOW",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=True,
        )
    )
    assert view.is_finished is True
    assert view.in_needs_attention is False
    assert view.open_work is False
    labels = [b.label for b in view.badges_now]
    assert "Automated" in labels
    assert "Action needed" not in labels


def test_automated_with_open_action_shows_combined_now_badge() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=True,
        )
    )
    assert view.open_work is True
    kinds = [b.kind for b in view.badges_now]
    assert BadgeKind.AUTOMATED_ACTION in kinds
    labels = [b.label for b in view.badges_now]
    assert "Automated · action needed" in labels


def test_suggest_resolve_default_true_when_closing_signal() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            closing_signal=True,
        )
    )
    assert view.suggest_resolve_default is True


def test_suggest_resolve_default_false_when_waiting_on_them() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="AWAITING_CLIENT",
            urgency="NORMAL",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            closing_signal=False,
        )
    )
    assert view.suggest_resolve_default is False
    assert view.in_needs_attention is False


def test_resolved_shows_resolution_banner() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="RESOLVED",
            urgency="LOW",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.show_resolution_banner is True


def test_resolved_hides_resolution_banner_after_wrong_reason_feedback() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="RESOLVED",
            urgency="LOW",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            resolution_reason_corrected=True,
        )
    )
    assert view.show_resolution_banner is False
    assert view.is_finished is True


def test_letter_draft_is_reply_ready_not_drafted() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            has_letter=True,
            draft_needed=True,
        )
    )
    assert view.disposition == DispositionKind.REPLY_REVIEW
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Reply ready"
    assert view.in_needs_attention is True
    assert "Drafted" not in [b.label for b in view.badges_now]


def test_fyi_briefing_stays_open_out_of_needs_attention() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="LOW",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            has_letter=False,
            draft_needed=False,
            has_teaching_note=True,
        )
    )
    assert view.disposition == DispositionKind.FYI_BRIEFING
    assert view.primary_badge is not None
    assert view.primary_badge.label == "FYI"
    assert view.is_finished is False
    assert view.open_work is True
    assert view.in_needs_attention is False
    assert view.urgency_active is False
    assert "Drafted" not in [b.label for b in view.badges_now]


def test_automated_alert_without_letter_is_action_needed() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="NORMAL",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=True,
            has_letter=False,
            draft_needed=False,
            has_teaching_note=True,
        )
    )
    assert view.disposition == DispositionKind.ACTION_NO_DRAFT
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Action needed"
    assert view.in_needs_attention is True
    assert "Drafted" not in [b.label for b in view.badges_now]


def test_informational_automated_is_fyi_with_inactive_urgency() -> None:
    """Domain-auth style: automated + no action items. CRITICAL may be stored
    from recurrence history, but Now is FYI / Open FYI — not live CRITICAL.
    """
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="CRITICAL",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=True,
            has_letter=False,
            draft_needed=False,
            has_teaching_note=True,
        )
    )
    assert view.disposition == DispositionKind.FYI_BRIEFING
    assert view.primary_badge is not None
    assert view.primary_badge.label == "FYI"
    assert view.in_needs_attention is False
    assert view.open_work is True
    assert view.urgency_assessed == "CRITICAL"
    assert view.urgency_active is False
    assert "CRITICAL" not in [b.label for b in view.badges_now]
    assert "Automated · action needed" not in [b.label for b in view.badges_now]
    assert "Automated" in [b.label for b in view.badges_now]


def test_no_action_state_is_resolved_by_draftassistant_with_banner() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="NO_ACTION",
            urgency="LOW",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.disposition == DispositionKind.RESOLVED_DRAFTASSISTANT
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Resolved by DraftAssistant"
    assert view.show_resolution_banner is True
    assert view.resolution_mode == "auto"
    assert "No action" not in [b.label for b in view.badges_now]


def test_elise_manual_resolve_badge_and_banner() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="RESOLVED",
            urgency="HIGH",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            resolved_by="elise",
        )
    )
    assert view.disposition == DispositionKind.RESOLVED_ELISE
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Resolved by Elise"
    assert view.show_resolution_banner is True
    assert view.resolution_mode == "manual"


def test_requires_human_is_needs_human_in_queue() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="REQUIRES_HUMAN",
            urgency="HIGH",
            has_action_items=True,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.disposition == DispositionKind.NEEDS_HUMAN
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Needs human"
    assert view.in_needs_attention is True


def test_awaiting_client_is_waiting_on_them() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="AWAITING_CLIENT",
            urgency="NORMAL",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.disposition == DispositionKind.WAITING_ON_THEM
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Waiting on them"
    assert view.in_needs_attention is False


def test_new_thread_is_processing() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="NEW",
            urgency=None,
            has_action_items=None,
            needs_context=None,
            is_spam=None,
            is_internal=False,
            is_automated=False,
        )
    )
    assert view.disposition == DispositionKind.PROCESSING
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Processing"
    assert view.in_needs_attention is False


def test_courtesy_reopen_forced_action_needed() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="DRAFTED",
            urgency="LOW",
            has_action_items=False,
            needs_context=False,
            is_spam=False,
            is_internal=False,
            is_automated=False,
            has_letter=False,
            draft_needed=False,
            forced_disposition="action_no_draft",
        )
    )
    assert view.disposition == DispositionKind.ACTION_NO_DRAFT
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Action needed"
    assert view.in_needs_attention is True


def test_spam_disposition_badge() -> None:
    view = derive_presentation(
        ThreadPolicyInput(
            state="SPAM",
            urgency=None,
            has_action_items=False,
            needs_context=False,
            is_spam=True,
            is_internal=False,
            is_automated=True,
        )
    )
    assert view.disposition == DispositionKind.SPAM
    assert view.primary_badge is not None
    assert view.primary_badge.label == "Spam"
    assert view.show_resolution_banner is False


def test_recurrence_floor_second_alert_in_48h_is_high() -> None:
    assert recurrence_urgency_floor(automated_inbound_count_48h=2) == "HIGH"


def test_recurrence_floor_third_alert_in_48h_is_critical() -> None:
    assert recurrence_urgency_floor(automated_inbound_count_48h=3) == "CRITICAL"


def test_recurrence_floor_single_alert_is_none() -> None:
    assert recurrence_urgency_floor(automated_inbound_count_48h=1) is None


def test_recurrence_floor_ignored_when_finished() -> None:
    assert (
        recurrence_urgency_floor(
            automated_inbound_count_48h=5,
            is_finished=True,
        )
        is None
    )
