"""Mailbox allowlist guards for web API routes."""

from __future__ import annotations

from fastapi import HTTPException, status

from app.core.config import Settings


def require_allowed_mailbox(settings: Settings, mailbox: str) -> None:
    """Raise 404 when the mailbox is outside TARGET_MAILBOXES."""
    if not settings.mailbox_allowed(mailbox):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mailbox not found",
        )
