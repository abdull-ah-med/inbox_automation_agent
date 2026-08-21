"""Independent oracles for thread presentation policy.

Worked example (Elise Sample case):
  state=RESOLVED, urgency=HIGH, triage.has_action_items=True, is_internal=True
  → open_work False; Now badges exclude Action needed; urgency_active False;
    assessed urgency still HIGH; history may still show action needed.
"""

from __future__ import annotations

from app.core.thread_policy import (
    BadgeKind,
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
    assert "Resolved" in labels
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
