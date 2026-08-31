"""Chat session persistence: compact history and resume across refresh."""

from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn


def test_compact_history_keeps_last_sixteen_and_folds_older() -> None:
    from app.services.chat_session_service import CHAT_SESSION_WINDOW, compact_history

    turns: list[ChatHistoryTurn] = []
    for i in range(20):
        turns.append(ChatHistoryTurn(role="user", content=f"user turn {i}"))
        turns.append(
            ChatHistoryTurn(
                role="assistant",
                content=f"assistant turn {i}",
                citations=[
                    ChatCitedThread(
                        thread_id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                        subject=f"Subject {i}",
                    )
                ]
                if i % 2 == 0
                else [],
            )
        )
    assert len(turns) == 40

    kept, summary = compact_history(turns, prior_summary="")
    assert len(kept) == CHAT_SESSION_WINDOW
    assert kept[0].content == "user turn 12"
    assert "user turn 0" in summary
    assert "assistant turn 0" in summary
    assert "user turn 11" in summary
    assert "user turn 12" not in summary


@pytest.mark.db
@pytest.mark.asyncio
async def test_chat_session_create_get_and_append_turn(db_session) -> None:
    from app.models.db.user import User
    from app.repositories import chat_session_repo
    from app.services import chat_session_service

    user = User(
        id=uuid.uuid4(),
        email=f"chat-session-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role="user",
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()

    session = await chat_session_service.create_session(
        db_session,
        user_id=user.id,
        mailbox="sales@example.com",
        settings=Settings(
            environment="local",
            target_mailboxes="sales@example.com",
        ),
    )
    await db_session.commit()

    loaded = await chat_session_repo.get_for_user(db_session, session.id, user.id)
    assert loaded is not None
    assert loaded.mailbox == "sales@example.com"
    assert loaded.messages == []

    await chat_session_service.append_turn(
        db_session,
        session_id=session.id,
        user_id=user.id,
        user_message="billing disputes waiting on review",
        assistant_message="The overdue billing dispute is waiting on review.",
        citations=[
            {
                "thread_id": str(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
                "subject": "Invoice dispute",
            }
        ],
    )
    await db_session.commit()

    again = await chat_session_repo.get_for_user(db_session, session.id, user.id)
    assert again is not None
    assert len(again.messages) == 2
    assert again.messages[0]["role"] == "user"
    assert again.messages[0]["content"] == "billing disputes waiting on review"
    assert again.messages[1]["role"] == "assistant"
    assert again.last_message_at is not None


@pytest.mark.db
@pytest.mark.asyncio
async def test_create_session_route_persists_after_request_ends(
    migrated_test_database: str,
) -> None:
    """POST /api/chat/session must commit — get_db_session rolls back otherwise.

    Spec: the returned session_id is loadable from a fresh DB connection after
    the request-scoped session closes (same contract as chat cache B5).
    """
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, patch

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import Settings, get_settings
    from app.core.dependencies import get_db
    from app.core.dependencies_auth import get_current_user
    from app.main import create_app
    from app.models.db.user import User
    from app.models.schemas.auth import UserMe

    user_id = uuid.uuid4()
    engine = create_async_engine(migrated_test_database, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as setup:
            setup.add(
                User(
                    id=user_id,
                    email=f"chat-route-{uuid.uuid4().hex[:8]}@example.com",
                    password_hash="hashed",
                    role="user",
                    is_active=True,
                )
            )
            await setup.commit()

        settings = Settings(
            environment="local",
            jwt_secret="c" * 64,
            frontend_origin="http://localhost:3000",
            cookie_secure=False,
            enable_dev_routes=False,
            target_mailboxes="sales@example.com",
            database_url=migrated_test_database,
            redis_url="redis://localhost:6379/15",
        )
        get_settings.cache_clear()
        with (
            patch("app.main.get_settings", return_value=settings),
            patch("app.main._ping_redis", AsyncMock()),
            patch("app.main.get_slack_app", return_value=None),
            patch("app.main.run_subscription_reconcile", AsyncMock()),
            patch("app.main.AsyncIOScheduler") as sched,
        ):
            sched.return_value.start = lambda: None
            sched.return_value.shutdown = lambda wait=False: None
            application = create_app()
            application.dependency_overrides[get_settings] = lambda: settings
            application.dependency_overrides[get_current_user] = lambda: UserMe(
                id=user_id,
                email="elise@example.com",
                role="user",
                created_at=datetime.now(UTC),
            )

            async def request_scoped_db():
                async with factory() as session:
                    yield session

            application.dependency_overrides[get_db] = request_scoped_db

            async with AsyncClient(
                transport=ASGITransport(app=application),
                base_url="http://test",
            ) as client:
                created = await client.post(
                    "/api/chat/session",
                    json={"mailbox": "sales@example.com"},
                )
            application.dependency_overrides.clear()
        get_settings.cache_clear()

        assert created.status_code == 201, created.text
        session_id = created.json()["session_id"]

        async with factory() as other:
            row = (
                await other.execute(
                    text("SELECT mailbox FROM chat_sessions WHERE id = :id AND user_id = :uid"),
                    {"id": session_id, "uid": str(user_id)},
                )
            ).one_or_none()
        assert row is not None
        assert row[0] == "sales@example.com"
    finally:
        await engine.dispose()


def test_create_session_rejects_mailbox_outside_allowlist() -> None:
    """SPEC: non-empty mailbox must be on TARGET_MAILBOXES (or resolve key→email)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    import pytest

    from app.core.config import Settings
    from app.core.exceptions import UnknownMailboxError
    from app.services import chat_session_service

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com,ops@example.com",
    )
    db = AsyncMock()

    async def _run() -> None:
        with pytest.raises(UnknownMailboxError, match="Mailbox not found"):
            await chat_session_service.create_session(
                db,
                user_id=uuid.uuid4(),
                mailbox="attacker@evil.example",
                settings=settings,
            )

    asyncio.run(_run())
    # Must fail before any repo write.
    assert not getattr(db, "add", MagicMock()).called


def test_create_session_allows_null_and_empty_mailbox() -> None:
    """SPEC: null/empty mailbox means all mailboxes — always allowed."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.core.config import Settings
    from app.services import chat_session_service

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
    )
    sentinel = object()

    async def _run() -> None:
        with patch(
            "app.services.chat_session_service.chat_session_repo.create",
            new_callable=AsyncMock,
            return_value=sentinel,
        ) as create:
            db = AsyncMock()
            for mailbox in (None, "", "   "):
                row = await chat_session_service.create_session(
                    db,
                    user_id=uuid.uuid4(),
                    mailbox=mailbox,
                    settings=settings,
                )
                assert row is sentinel
            assert create.await_count == 3

    asyncio.run(_run())


def test_create_session_resolves_allowed_mailbox_key() -> None:
    """Oracle: key 'sales' resolves to sales@example.com when that email is allowlisted."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from app.core.config import Settings
    from app.services import chat_session_service

    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
    )
    captured: dict[str, object] = {}

    async def fake_create(session, *, user_id, mailbox):  # type: ignore[no-untyped-def]
        captured["mailbox"] = mailbox
        return object()

    async def _run() -> None:
        with patch(
            "app.services.chat_session_service.chat_session_repo.create",
            side_effect=fake_create,
        ):
            await chat_session_service.create_session(
                AsyncMock(),
                user_id=uuid.uuid4(),
                mailbox="sales",
                settings=settings,
            )

    asyncio.run(_run())
    assert captured["mailbox"] == "sales@example.com"
