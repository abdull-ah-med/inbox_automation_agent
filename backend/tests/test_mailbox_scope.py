"""Mailbox-scope truth table. Oracles are hand-written, not inferred from helpers."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.core.exceptions import UnknownMailboxError
from app.services.mailbox_scope import resolve_scoped_mailboxes

SALES = "sales@example.com"
CR = "cr@example.com"


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=f"{SALES},{CR}",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        _env_file=None,
    )


@pytest.mark.parametrize(
    ("mailbox", "nl_tokens", "expected"),
    [
        (None, (), [SALES, CR]),
        ("", (), [SALES, CR]),
        (SALES, (), [SALES]),
        (None, (SALES,), [SALES]),
        (SALES, (SALES,), [SALES]),
        (None, ("sales",), [SALES]),
    ],
)
def test_resolve_scoped_mailboxes_truth_table(
    mailbox: str | None,
    nl_tokens: tuple[str, ...],
    expected: list[str],
) -> None:
    assert resolve_scoped_mailboxes(_settings(), mailbox, nl_tokens) == expected


@pytest.mark.parametrize(
    ("mailbox", "nl_tokens"),
    [
        (SALES, (CR,)),
        (None, ("unknown@example.com",)),
        (CR, ("sales",)),
        (SALES, ("cr",)),
    ],
)
def test_empty_or_unknown_intersection_raises(
    mailbox: str | None,
    nl_tokens: tuple[str, ...],
) -> None:
    with pytest.raises(UnknownMailboxError, match="Mailbox not found"):
        resolve_scoped_mailboxes(_settings(), mailbox, nl_tokens)
