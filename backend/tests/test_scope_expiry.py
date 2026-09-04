"""Tests for expires_at_for_scope — plan §4.8 defaults.

Frozen clock: 2026-09-04 12:00 UTC.
  thread                    -> None until resolved; resolved_at + 30d
  sender_address            -> +90d  = 2026-12-03 12:00 UTC
  sender_domain             -> +90d  = 2026-12-03 12:00 UTC
  mailbox+routing_category  -> +180d = 2027-03-03 12:00 UTC
  mailbox / global          -> None (no auto-expiry)
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.scope_keys import expires_at_for_scope

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def test_thread_scope_does_not_expire_until_resolved() -> None:
    assert expires_at_for_scope("thread", now=NOW) is None


def test_thread_scope_expires_30_days_after_resolved() -> None:
    resolved = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    assert expires_at_for_scope("thread", thread_resolved_at=resolved) == datetime(
        2026, 10, 4, 12, 0, tzinfo=UTC
    )


def test_sender_address_scope_expires_in_90_days() -> None:
    assert expires_at_for_scope("sender_address", now=NOW) == datetime(
        2026, 12, 3, 12, 0, tzinfo=UTC
    )


def test_sender_domain_scope_expires_in_90_days() -> None:
    assert expires_at_for_scope("sender_domain", now=NOW) == datetime(
        2026, 12, 3, 12, 0, tzinfo=UTC
    )


def test_mailbox_category_scope_expires_in_180_days() -> None:
    assert expires_at_for_scope("mailbox+routing_category", now=NOW) == datetime(
        2027, 3, 3, 12, 0, tzinfo=UTC
    )


def test_mailbox_and_global_never_auto_expire() -> None:
    assert expires_at_for_scope("mailbox", now=NOW) is None
    assert expires_at_for_scope("global", now=NOW) is None
