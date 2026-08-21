"""Chat session persistence: compact history and resume across refresh."""

from __future__ import annotations

import uuid

import pytest

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
