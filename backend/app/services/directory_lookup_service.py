"""Prefetch per-mailbox contact aliases for draft salutations."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.internal_mail import extract_email_address
from app.models.schemas.email import EmailMessageSchema, ThreadContextSchema
from app.repositories import mailbox_contact_repo


def _candidate_emails(
    mailbox: str,
    thread_context: ThreadContextSchema,
    current: EmailMessageSchema | None = None,
) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()

    def _add(raw: str | None) -> None:
        if not raw:
            return
        extracted = extract_email_address(raw)
        if extracted is None:
            return
        key = extracted.lower()
        if key in seen:
            return
        seen.add(key)
        emails.append(key)

    messages = list(thread_context.messages)
    if current is not None:
        messages = [*messages, current]
    for msg in messages:
        _add(msg.sender)
        for recipient in msg.to_recipients or []:
            _add(recipient)
        for recipient in msg.cc_recipients or []:
            _add(recipient)
    _add(mailbox)
    return emails


async def build_directory(
    session: AsyncSession,
    mailbox: str,
    thread_context: ThreadContextSchema,
    *,
    current: EmailMessageSchema | None = None,
) -> dict[str, str]:
    """Return ``{email.lower(): first_name}`` for thread participants. One query."""
    candidates = _candidate_emails(mailbox, thread_context, current)
    if not candidates:
        return {}
    rows = await mailbox_contact_repo.get_many(session, mailbox, candidates)
    return {email: row.first_name for email, row in rows.items()}
