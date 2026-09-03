"""Resolve thread with actions-taken training capture."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_anthropic_client,
    get_db,
    get_graph_client,
    get_openai_client,
    get_redis,
)
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.resolution import ResolveThreadSchema
from app.repositories import thread_repo
from app.services import resolution_service

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"


def _settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def test_resolve_schema_requires_actions_taken() -> None:
    with pytest.raises(ValidationError):
        ResolveThreadSchema.model_validate({})


def test_resolve_schema_accepts_legacy_note_alias() -> None:
    body = ResolveThreadSchema.model_validate({"note": "Checked the portal"})
    assert body.actions_taken == "Checked the portal"


def test_resolve_schema_accepts_actions_taken_and_involved() -> None:
    body = ResolveThreadSchema.model_validate(
        {
            "actions_taken": "Verified pending driver change in portal",
            "involved": "accounting team",
        }
    )
    assert body.actions_taken == "Verified pending driver change in portal"
    assert body.involved == "accounting team"


@pytest.mark.asyncio
async def test_resolve_api_rejects_missing_actions_taken(db_session) -> None:
    settings = _settings()
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings

    async def fake_user():
        from app.models.schemas.auth import UserMe

        return UserMe(
            id=uuid.uuid4(),
            email="elise@example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: object()
    application.dependency_overrides[get_openai_client] = lambda: object()
    application.dependency_overrides[get_graph_client] = lambda: None

    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-api-thread",
        subject="Daily Drivers changes update",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.post(f"/api/threads/{thread.id}/resolve", json={})

    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resolve_api_persists_actions_taken_and_involved(db_session) -> None:
    settings = _settings()
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: settings

    async def fake_user():
        from app.models.schemas.auth import UserMe

        return UserMe(
            id=uuid.uuid4(),
            email="elise@example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: object()
    application.dependency_overrides[get_openai_client] = lambda: object()
    application.dependency_overrides[get_graph_client] = lambda: None

    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-api-audit",
        subject="Daily Drivers changes update",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=datetime.now(UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/api/threads/{thread.id}/resolve",
            json={
                "actions_taken": "Reviewed pending driver change for SUMMITX",
                "involved": "accounting@sample-services.example.com",
            },
        )

    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == ThreadStateEnum.RESOLVED.value

    audit_rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.conversation_id == thread.conversation_id,
                    AuditEvent.event_type == "thread.resolved.reviewer",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    payload = audit_rows[0].payload
    assert payload["actions_taken"] == "Reviewed pending driver change for SUMMITX"
    assert payload["involved"] == "accounting@sample-services.example.com"


@pytest.mark.asyncio
async def test_resolve_removes_thread_from_needs_attention(db_session) -> None:
    settings = _settings()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-queue",
        subject="Notice",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    draft = Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="draft",
        recipients={},
        teaching_note="note",
        created_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(draft)
    await db_session.commit()

    before = await thread_repo.list_needs_attention(db_session, [MAILBOX], limit=20)
    assert thread.id in {row.id for row in before}

    with patch(
        "app.services.resolution_service.maybe_extract_workflow",
        new=AsyncMock(),
    ):
        state = await resolution_service.resolve_thread_manual(
            db_session,
            settings,
            thread.id,
            actor="elise@example.com",
            actions_taken="No reply needed; logged in portal",
            involved="n/a",
        )
    await db_session.commit()

    assert state == ThreadStateEnum.RESOLVED.value
    after = await thread_repo.list_needs_attention(db_session, [MAILBOX], limit=20)
    assert thread.id not in {row.id for row in after}


@pytest.mark.asyncio
async def test_resolve_already_resolved_still_records_capture(db_session) -> None:
    settings = _settings()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-already",
        subject="Daily Drivers changes update",
        state=ThreadStateEnum.RESOLVED.value,
        urgency="NORMAL",
        last_message_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    extract_mock = AsyncMock()
    with patch(
        "app.services.resolution_service.maybe_extract_workflow",
        new=extract_mock,
    ):
        state = await resolution_service.resolve_thread_manual(
            db_session,
            settings,
            thread.id,
            actor="elise@example.com",
            actions_taken="Logged the driver change in portal",
            involved="accounting team",
            anthropic_client=object(),
        )
    await db_session.commit()

    assert state == ThreadStateEnum.RESOLVED.value
    audit_rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.conversation_id == thread.conversation_id,
                    AuditEvent.event_type == "thread.resolved.reviewer",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    payload = audit_rows[0].payload
    assert payload["actions_taken"] == "Logged the driver change in portal"
    assert payload["involved"] == "accounting team"
    assert payload["already_resolved"] is True
    extract_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_calls_workflow_extraction_with_learning_text(db_session) -> None:
    settings = _settings()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-extract",
        subject="Daily Drivers changes update",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    message = Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=f"msg-{uuid.uuid4()}",
        direction="inbound",
        sender="accounting@sample-services.example.com",
        body_text="Total customers with Pending Driver Changes are 1",
        body_preview="Pending driver changes",
        received_at=datetime(2026, 8, 18, 10, 0, tzinfo=UTC),
        to_recipients=[MAILBOX],
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(message)
    await db_session.commit()

    extract_mock = AsyncMock()
    anthropic = object()

    with patch(
        "app.services.resolution_service.maybe_extract_workflow",
        new=extract_mock,
    ):
        await resolution_service.resolve_thread_manual(
            db_session,
            settings,
            thread.id,
            actor="elise@example.com",
            actions_taken="Checked portal and confirmed no client action needed",
            involved="accounting team",
            anthropic_client=anthropic,
        )

    extract_mock.assert_awaited_once()
    kwargs = extract_mock.await_args.kwargs
    assert kwargs["atomization_text"] == "Checked portal and confirmed no client action needed"
    assert (
        "Actions taken: Checked portal and confirmed no client action needed"
        in kwargs["email_text"]
    )
    assert "Involved: accounting team" in kwargs["email_text"]
    assert "Daily Drivers changes update" in kwargs["email_text"]
    assert "Pending Driver Changes are 1" in kwargs["email_text"]
    assert kwargs["client"] is anthropic


@pytest.mark.asyncio
async def test_manual_resolve_stores_resolve_snapshot(db_session) -> None:
    settings = _settings()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="resolve-snapshot",
        subject="Notice",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        last_message_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.commit()

    with patch(
        "app.services.resolution_service.maybe_extract_workflow",
        new=AsyncMock(),
    ):
        await resolution_service.resolve_thread_manual(
            db_session,
            settings,
            thread.id,
            actor="elise@example.com",
            actions_taken="Logged in portal",
            involved="accounting",
        )
    await db_session.commit()

    payload = (
        await db_session.execute(
            select(AuditEvent).where(
                AuditEvent.conversation_id == thread.conversation_id,
                AuditEvent.event_type == "thread.resolved.reviewer",
            )
        )
    ).scalar_one().payload
    snapshot = payload["resolve_snapshot"]
    assert snapshot["resolved_by"] == "elise"
    assert snapshot["resolution_reason"] == "manual"
    assert snapshot["had_draft"] is False
    assert snapshot["actions_taken"] == "Logged in portal"
    assert snapshot["involved"] == "accounting"


@pytest.mark.asyncio
async def test_reopen_courtesy_close_returns_to_needs_attention(db_session) -> None:
    """Still open? on an DraftAssistant courtesy close must land in Needs Attention, not FYI."""
    settings = _settings()
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="reopen-courtesy",
        subject="Re: Friday walkthrough",
        state=ThreadStateEnum.RESOLVED.value,
        urgency="LOW",
        last_message_at=datetime(2026, 8, 18, 11, 0, tzinfo=UTC),
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        AuditEvent(
            event_type="thread.resolved.draftassistant",
            conversation_id=thread.conversation_id,
            mailbox=MAILBOX,
            payload={
                "resolve_snapshot": {
                    "resolved_by": "draftassistant",
                    "resolution_reason": "courtesy_close",
                    "disposition_at_resolve": "fyi_briefing",
                    "had_draft": False,
                    "has_action_items": False,
                    "draft_needed": False,
                }
            },
            actor="system",
        )
    )
    await db_session.commit()

    new_state = await resolution_service.apply_resolution_feedback(
        db_session,
        settings,
        thread.id,
        action="reopen",
        actor="elise@example.com",
    )
    await db_session.commit()

    assert new_state == ThreadStateEnum.DRAFTED.value
    rows = await thread_repo.list_needs_attention(db_session, [MAILBOX], limit=20)
    assert [row.id for row in rows] == [thread.id]
    assert rows[0].presentation is not None
    assert rows[0].presentation.disposition == "action_no_draft"
