"""Tests for ApprovalScope richer enum — oracle: plan §C + Pydantic type contract.

sender_address and mailbox must be accepted.
global must never be a valid scope (it is absent from the Literal).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas.feedback import DraftApproveSchema


@pytest.mark.parametrize("scope", ["once", "similar", "sender_address", "mailbox"])
def test_valid_approval_scopes_accepted(scope: str) -> None:
    """All four plan-defined scopes are accepted by the schema."""
    body = DraftApproveSchema(
        approval_note="Use formal tone",
        approval_scope=scope,
    )
    assert body.approval_scope == scope


def test_global_scope_rejected() -> None:
    """'global' is not a valid ApprovalScope — Pydantic must raise."""
    with pytest.raises(ValidationError):
        DraftApproveSchema(
            approval_note="Use formal tone",
            approval_scope="global",  # type: ignore[arg-type]
        )


def test_unknown_scope_rejected() -> None:
    """Arbitrary strings are not valid scopes."""
    with pytest.raises(ValidationError):
        DraftApproveSchema(
            approval_note="Use formal tone",
            approval_scope="organisation",  # type: ignore[arg-type]
        )


def test_scope_none_allowed_without_note() -> None:
    """No scope needed when there is no approval_note."""
    body = DraftApproveSchema()
    assert body.approval_scope is None
    assert body.approval_note is None


def test_note_without_scope_raises() -> None:
    """approval_note present without approval_scope is a validation error."""
    with pytest.raises(ValidationError):
        DraftApproveSchema(approval_note="Soften tone")


def test_sender_address_with_note_accepted() -> None:
    """sender_address + note passes schema validation end-to-end."""
    body = DraftApproveSchema(
        approval_note="Address the driver by name",
        approval_scope="sender_address",
    )
    assert body.approval_scope == "sender_address"
    assert body.approval_note == "Address the driver by name"
