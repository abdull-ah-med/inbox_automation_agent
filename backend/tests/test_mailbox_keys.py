"""Mailbox key helpers: UI key → allowed email, scoped allowlists."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.exceptions import UnknownMailboxError
from app.core.mailbox_keys import resolve_allowed_mailbox, scoped_mailboxes

_SALES = "sales@example.com"
_CR = "cr@example.com"


def _settings() -> Settings:
    return Settings(
        environment="local",
        target_mailboxes=f"{_SALES},{_CR}",
    )


def test_resolve_allowed_mailbox_accepts_ui_key() -> None:
    assert resolve_allowed_mailbox(_settings(), "sales") == _SALES


def test_resolve_allowed_mailbox_accepts_email() -> None:
    assert resolve_allowed_mailbox(_settings(), _SALES) == _SALES


def test_resolve_allowed_mailbox_unknown_raises() -> None:
    with pytest.raises(UnknownMailboxError, match="Mailbox not found"):
        resolve_allowed_mailbox(_settings(), "other@example.com")


def test_scoped_mailboxes_none_returns_all_allowed() -> None:
    assert scoped_mailboxes(_settings(), None) == [_SALES, _CR]


def test_scoped_mailboxes_blank_returns_all_allowed() -> None:
    assert scoped_mailboxes(_settings(), "  ") == [_SALES, _CR]


def test_scoped_mailboxes_filters_to_one() -> None:
    assert scoped_mailboxes(_settings(), "sales") == [_SALES]


def test_scoped_mailboxes_strips_control_chars_from_key() -> None:
    assert scoped_mailboxes(_settings(), "sales\x00") == [_SALES]
