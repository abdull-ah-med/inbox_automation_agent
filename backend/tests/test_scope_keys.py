"""Tests for scope_key_for — pure function, no DB required.

Oracles are the canonical format strings documented in the plan:
  thread:<uuid>, sender:<addr>, domain:<dom>, mailbox:<mb>:<cat>,
  mailbox:<mb>, global:
"""

from __future__ import annotations

import uuid

import pytest

from app.core.scope_keys import resolve_widening_scope, scope_key_for


def test_thread_scope() -> None:
    """thread_id formats as 'thread:<value>'."""
    tid = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
    result = scope_key_for("thread", thread_id=tid)
    assert result == f"thread:{tid}"


def test_sender_address_scope_lowercased() -> None:
    """sender_address scope normalises to lowercase."""
    result = scope_key_for("sender_address", sender_address="User@Example.COM")
    assert result == "sender:user@example.com"


def test_sender_domain_scope_lowercased() -> None:
    """sender_domain scope normalises to lowercase."""
    result = scope_key_for("sender_domain", sender_domain="EXAMPLE.COM")
    assert result == "domain:example.com"


def test_mailbox_routing_category_scope() -> None:
    """mailbox+routing_category scope includes both mailbox and routing_category."""
    result = scope_key_for(
        "mailbox+routing_category",
        mailbox="elise@x.com",
        routing_category="billing",
    )
    assert result == "mailbox:elise@x.com:billing"


def test_mailbox_scope() -> None:
    """mailbox scope includes only the mailbox address."""
    result = scope_key_for("mailbox", mailbox="elise@x.com")
    assert result == "mailbox:elise@x.com"


def test_global_scope() -> None:
    """global scope returns 'global:' regardless of other args."""
    result = scope_key_for("global")
    assert result == "global:"


def test_global_scope_ignores_extra_kwargs() -> None:
    """global scope returns 'global:' even when extra kwargs are passed."""
    result = scope_key_for("global", mailbox="elise@x.com", sender_domain="x.com")
    assert result == "global:"


def test_invalid_scope_raises() -> None:
    """Unknown scope raises ValueError, not silently produces wrong key."""
    with pytest.raises(ValueError, match="Unknown scope"):
        scope_key_for("unknown_scope")


def test_thread_scope_missing_id_raises() -> None:
    """Missing thread_id raises ValueError for scope='thread'."""
    with pytest.raises(ValueError, match="thread_id is required"):
        scope_key_for("thread")


def test_sender_address_missing_raises() -> None:
    """Missing sender_address raises ValueError."""
    with pytest.raises(ValueError, match="sender_address is required"):
        scope_key_for("sender_address")


def test_mailbox_routing_category_missing_category_raises() -> None:
    """mailbox+routing_category without routing_category raises ValueError."""
    with pytest.raises(ValueError, match="routing_category is required"):
        scope_key_for("mailbox+routing_category", mailbox="elise@x.com")


def test_all_scope_literals() -> None:
    """Spot-check all six ladder levels return distinct, non-empty keys."""
    tid = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
    keys = [
        scope_key_for("thread", thread_id=tid),
        scope_key_for("sender_address", sender_address="a@b.com"),
        scope_key_for("sender_domain", sender_domain="b.com"),
        scope_key_for("mailbox+routing_category", mailbox="m@c.com", routing_category="sales"),
        scope_key_for("mailbox", mailbox="m@c.com"),
        scope_key_for("global"),
    ]
    # All keys are non-empty and distinct
    assert len(keys) == len(set(keys))
    assert all(keys)
    # Literal checks for each
    assert keys[0] == f"thread:{tid}"
    assert keys[1] == "sender:a@b.com"
    assert keys[2] == "domain:b.com"
    assert keys[3] == "mailbox:m@c.com:sales"
    assert keys[4] == "mailbox:m@c.com"
    assert keys[5] == "global:"


def test_sender_address_widens_to_domain_key() -> None:
    """ops@statuspage.io at sender scope widens to domain:statuspage.io, not the sender key."""
    scope, key = resolve_widening_scope(
        "sender_domain",
        mailbox="sales@example.com",
        source_scope_key="sender:ops@statuspage.io",
    )
    assert scope == "sender_domain"
    assert key == "domain:statuspage.io"


def test_domain_without_category_falls_back_to_mailbox() -> None:
    """mailbox+routing_category with no category must not keep domain: as the key."""
    scope, key = resolve_widening_scope(
        "mailbox+routing_category",
        mailbox="sales@example.com",
        source_scope_key="domain:statuspage.io",
    )
    assert scope == "mailbox"
    assert key == "mailbox:sales@example.com"


def test_mailbox_widening_uses_proposal_mailbox() -> None:
    scope, key = resolve_widening_scope(
        "mailbox",
        mailbox="sales@example.com",
        source_scope_key="domain:statuspage.io",
    )
    assert scope == "mailbox"
    assert key == "mailbox:sales@example.com"
