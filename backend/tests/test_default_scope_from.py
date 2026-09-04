"""Tests for default_scope_from — pure function, no DB required.

Oracle source: plan §2 spec + invariant table:
  - NEVER returns scope == "global"
  - person_bound=True for thread and sender_address scopes
  - "global" input falls back to "mailbox"
  - Missing context (no sender_address for sender_address scope) widens one step
"""

from __future__ import annotations

import uuid

from app.services.feedback_atom_service import default_scope_from

MAILBOX = "ops@example.com"
THREAD_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
SENDER = "vendor@acme.com"
DOMAIN = "acme.com"


def test_thread_scope_returns_thread_key_person_bound() -> None:
    """Thread scope → scope_key prefixed 'thread:', person_bound=True."""
    scope, key, person_bound = default_scope_from(
        "Always address by first name",
        "thread",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="billing",
    )
    assert scope == "thread"
    assert key == f"thread:{THREAD_ID}"
    assert person_bound is True


def test_sender_address_scope_person_bound() -> None:
    """sender_address scope → 'sender:<addr>', person_bound=True."""
    scope, key, person_bound = default_scope_from(
        "Use informal tone with this sender",
        "sender_address",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert scope == "sender_address"
    assert key == "sender:vendor@acme.com"
    assert person_bound is True


def test_sender_domain_scope_not_person_bound() -> None:
    """sender_domain scope → 'domain:<dom>', person_bound=False."""
    scope, key, person_bound = default_scope_from(
        "Reply within 1 business day to all acme emails",
        "sender_domain",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="billing",
    )
    assert scope == "sender_domain"
    assert key == "domain:acme.com"
    assert person_bound is False


def test_mailbox_routing_category_scope_not_person_bound() -> None:
    """mailbox+routing_category scope → 'mailbox:<mb>:<cat>', person_bound=False."""
    scope, key, person_bound = default_scope_from(
        "Always attach the invoice PDF for billing emails",
        "mailbox+routing_category",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="billing",
    )
    assert scope == "mailbox+routing_category"
    assert key == f"mailbox:{MAILBOX}:billing"
    assert person_bound is False


def test_mailbox_scope_not_person_bound() -> None:
    """mailbox scope → 'mailbox:<mb>', person_bound=False."""
    scope, key, person_bound = default_scope_from(
        "Sign off with the team name, not a personal name",
        "mailbox",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category=None,
    )
    assert scope == "mailbox"
    assert key == f"mailbox:{MAILBOX}"
    assert person_bound is False


def test_global_input_falls_back_to_mailbox() -> None:
    """'global' suggested_scope is NEVER returned; falls back to 'mailbox'."""
    scope, key, person_bound = default_scope_from(
        "Always start with a professional greeting",
        "global",
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert scope != "global", "default_scope_from must never return 'global'"
    assert scope == "mailbox"
    assert key == f"mailbox:{MAILBOX}"
    assert person_bound is False


def test_unknown_scope_falls_back_to_mailbox() -> None:
    """Unknown/garbage suggested_scope falls back to 'mailbox'."""
    scope, key, person_bound = default_scope_from(
        "Some atom text",
        "department",  # not in ladder
        thread_id=THREAD_ID,
        sender_address=SENDER,
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="general",
    )
    assert scope == "mailbox"
    assert key == f"mailbox:{MAILBOX}"
    assert person_bound is False


def test_missing_sender_widens_one_step() -> None:
    """sender_address scope with no sender_address widens to sender_domain."""
    scope, key, _person_bound = default_scope_from(
        "Be concise with this sender",
        "sender_address",
        thread_id=THREAD_ID,
        sender_address=None,  # missing
        sender_domain=DOMAIN,
        mailbox=MAILBOX,
        routing_category="billing",
    )
    # Must widen: sender_address → sender_domain (next wider)
    assert scope != "global"
    assert scope == "sender_domain"
    assert key == "domain:acme.com"
