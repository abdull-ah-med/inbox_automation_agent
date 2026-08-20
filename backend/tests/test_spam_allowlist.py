"""Reviewer 'not spam' corrections allowlist the sender for later triage.

Oracles are hand-checked address pairs. A production change that still
discards mail from an allowlisted sender, or applies another mailbox's
allowlist, must fail these tests.
"""

from __future__ import annotations

from app.core.spam_allowlist import apply_spam_allowlist_policy, is_allowlisted_sender
from app.models.schemas.classification import TriageResultSchema


def _spam_triage() -> TriageResultSchema:
    return TriageResultSchema.model_validate(
        {
            "is_spam": True,
            "spam_reason": "Looks like marketing",
            "has_action_items": True,
            "action_items_summary": "Confirm SampleLab billing",
            "needs_context": False,
            "context_reason": None,
            "routing_category": "vendor",
        }
    )


def test_exact_allowlisted_sender_matches() -> None:
    assert is_allowlisted_sender(
        "orders@sample-lab.example.com",
        frozenset({"orders@sample-lab.example.com"}),
    )


def test_allowlist_is_case_insensitive_and_strips_display_name() -> None:
    assert is_allowlisted_sender(
        "SampleLab Billing <Orders@sample-lab.example.com>",
        frozenset({"orders@sample-lab.example.com"}),
    )


def test_different_sender_is_not_allowlisted() -> None:
    assert not is_allowlisted_sender(
        "promo@spam.example",
        frozenset({"orders@sample-lab.example.com"}),
    )


def test_allowlist_policy_clears_llm_spam_flag() -> None:
    result = apply_spam_allowlist_policy(
        _spam_triage(),
        sender="orders@sample-lab.example.com",
        allowlisted_addresses=frozenset({"orders@sample-lab.example.com"}),
    )
    assert result.is_spam is False
    assert result.spam_reason is None
    assert result.has_action_items is True
    assert result.routing_category == "vendor"


def test_allowlist_policy_leaves_unknown_sender_spam() -> None:
    result = apply_spam_allowlist_policy(
        _spam_triage(),
        sender="promo@spam.example",
        allowlisted_addresses=frozenset({"orders@sample-lab.example.com"}),
    )
    assert result.is_spam is True
    assert result.spam_reason == "Looks like marketing"


def test_empty_allowlist_does_not_clear_spam() -> None:
    result = apply_spam_allowlist_policy(
        _spam_triage(),
        sender="orders@sample-lab.example.com",
        allowlisted_addresses=frozenset(),
    )
    assert result.is_spam is True
