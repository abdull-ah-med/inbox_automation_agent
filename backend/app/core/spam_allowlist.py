"""Deterministic spam-allowlist policy after a reviewer marks mail as not spam."""

from __future__ import annotations

from app.core.internal_mail import extract_email_address
from app.models.schemas.classification import TriageResultSchema


def is_allowlisted_sender(sender: str | None, allowlisted_addresses: frozenset[str]) -> bool:
    """True when the sender address is in the reviewer allowlist (exact address)."""
    address = extract_email_address(sender)
    if address is None:
        return False
    return address in allowlisted_addresses


def apply_spam_allowlist_policy(
    triage: TriageResultSchema,
    *,
    sender: str,
    allowlisted_addresses: frozenset[str],
) -> TriageResultSchema:
    """Allowlisted senders are never spam; other flags stay as Haiku returned them."""
    if not is_allowlisted_sender(sender, allowlisted_addresses):
        return triage
    if not triage.is_spam:
        return triage
    return triage.model_copy(update={"is_spam": False, "spam_reason": None})
