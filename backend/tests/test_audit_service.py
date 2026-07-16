"""Unit tests for audit_service (mocked repo — no live DB)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import AuditError
from app.models.schemas.audit import AuditEventSchema
from app.services import audit_service


@pytest.mark.asyncio
async def test_log_event_persists_metadata() -> None:
    event = AuditEventSchema(
        id=uuid.uuid4(),
        event_type="triage.action_needed",
        conversation_id="c1",
        mailbox="elise@example.com",
        payload={"is_spam": False, "confidence": 0.9},
        actor="system",
        created_at=datetime.now(UTC),
    )
    with patch(
        "app.services.audit_service.audit_repo.create_audit_event",
        new=AsyncMock(return_value=event),
    ) as create:
        result = await audit_service.log_event(
            AsyncMock(),
            event_type="triage.action_needed",
            conversation_id="c1",
            mailbox="elise@example.com",
            payload={"is_spam": False, "confidence": 0.9},
        )
    assert result.event_type == "triage.action_needed"
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["event_type"] == "triage.action_needed"
    assert "is_spam" in kwargs["payload"]
    assert "body" not in kwargs["payload"]


@pytest.mark.asyncio
async def test_log_event_wraps_failures() -> None:
    with (
        patch(
            "app.services.audit_service.audit_repo.create_audit_event",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ),
        pytest.raises(AuditError, match="Failed to write audit event"),
    ):
        await audit_service.log_event(
            AsyncMock(),
            event_type="triage.failed",
            conversation_id="c1",
            mailbox="elise@example.com",
        )
