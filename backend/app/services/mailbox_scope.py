"""Single mailbox-allowlist intersection used by search, chat tools, and NL scope."""

from __future__ import annotations

from collections.abc import Sequence

from app.core.config import Settings
from app.core.exceptions import UnknownMailboxError
from app.core.mailbox_keys import resolve_mailbox_email, scoped_mailboxes
from app.core.sanitize import sanitize_user_text


def resolve_scoped_mailboxes(
    settings: Settings,
    mailbox: str | None = None,
    nl_tokens: Sequence[str] | None = None,
) -> list[str]:
    """Intersect the request mailbox with explicit NL / ``mailbox:`` tokens.

    An empty intersection after explicit tokens is an error, not zero hits.
    """
    scoped = scoped_mailboxes(settings, mailbox)
    tokens = tuple(str(token).strip() for token in (nl_tokens or ()) if str(token).strip())
    if not tokens:
        return scoped
    wanted: list[str] = []
    for token in tokens:
        email = resolve_mailbox_email(sanitize_user_text(token), list(settings.mailbox_list))
        if email is None or not settings.mailbox_allowed(email):
            raise UnknownMailboxError("Mailbox not found")
        if email not in wanted:
            wanted.append(email)
    intersection = [item for item in scoped if item in set(wanted)]
    if not intersection:
        raise UnknownMailboxError("Mailbox not found")
    return intersection
