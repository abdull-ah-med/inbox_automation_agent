"""directory_lookup_service.build_directory — bulk alias fetch."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.repositories import mailbox_contact_repo
from app.services import directory_lookup_service

pytestmark = pytest.mark.asyncio


def _msg(
    *,
    message_id: str,
    sender: str,
    to: list[str],
    cc: list[str] | None = None,
    mailbox: str = "elise@example.com",
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-dir",
        mailbox=mailbox,
        sender=sender,
        subject="Hello",
        body_text="Body",
        body_preview="Body",
        received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=to,
        cc_recipients=list(cc or []),
    )


async def test_build_directory_one_query_and_isolation(db_session) -> None:
    mailbox = "elise@example.com"
    for i in range(8):
        await mailbox_contact_repo.upsert(
            db_session,
            mailbox,
            f"person{i}@example.com",
            full_name=f"Person {i}",
            first_name=f"P{i}",
        )
    await mailbox_contact_repo.upsert(
        db_session,
        "bob@example.com",
        "person0@example.com",
        full_name="Other",
        first_name="Other",
    )
    await db_session.commit()

    messages = [
        _msg(
            message_id=str(i),
            sender=f"person{i}@example.com",
            to=[mailbox],
            cc=[f"person{(i + 1) % 8}@example.com"],
            mailbox=mailbox,
        )
        for i in range(8)
    ]
    thread = ThreadContextSchema(
        conversation_id="conv-dir",
        mailbox=mailbox,
        subject="Hello",
        messages=messages,
    )

    from sqlalchemy import event

    queries: list[str] = []

    def _on_cursor(conn, cursor, statement, parameters, context, executemany) -> None:
        queries.append(str(statement))

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", _on_cursor)
    try:
        directory = await directory_lookup_service.build_directory(
            db_session,
            mailbox,
            thread,
        )
    finally:
        event.remove(bind, "before_cursor_execute", _on_cursor)

    assert len(directory) == 8
    assert directory["person0@example.com"] == "P0"
    select_count = sum(1 for q in queries if "SELECT" in q.upper() and "mailbox_contacts" in q)
    assert select_count == 1

    bob_dir = await directory_lookup_service.build_directory(
        db_session,
        "bob@example.com",
        ThreadContextSchema(
            conversation_id="conv-bob",
            mailbox="bob@example.com",
            subject="Hello",
            messages=[
                _msg(
                    message_id="x",
                    sender="person0@example.com",
                    to=["bob@example.com"],
                    mailbox="bob@example.com",
                )
            ],
        ),
    )
    assert bob_dir == {"person0@example.com": "Other"}
