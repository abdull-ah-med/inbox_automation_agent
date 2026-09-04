"""Preference-pair source document must hash the email, not the subject.

Oracle: subject "Invoice 99" + body "Please send the POD" produces a hash
that is not sha256("Invoice 99") and whose source text contains the body.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema
from app.services.embedding_service import preference_pair_source_text

_SUBJECT = "Invoice 99"
_BODY = "Please send the POD"


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m-invoice-99",
        conversation_id="c-invoice-99",
        mailbox="sales@example.com",
        sender="vendor@acme.com",
        subject=_SUBJECT,
        body_text=_BODY,
        body_clean=_BODY,
        received_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["sales@example.com"],
    )


def test_preference_pair_source_contains_body_not_subject_only() -> None:
    text = preference_pair_source_text(_email())
    assert _BODY in text
    assert _SUBJECT in text
    digest = hashlib.sha256(text.encode()).hexdigest()
    subject_only = hashlib.sha256(_SUBJECT.encode()).hexdigest()
    assert digest != subject_only
    # 64-char sha256 hex
    assert len(digest) == 64
