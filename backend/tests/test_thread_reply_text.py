"""GET /api/threads/{id} exposes reply_text (unique new content) per message.

Worked example: Alice inbound, mailbox sent a quote-only reply, Alice inbound
again. Oracles are the literals seeded below, not Graph uniqueBody recomputed
in the assertion.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.dependencies import get_anthropic_client, get_db, get_openai_client, get_redis
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.auth import UserMe
from app.models.schemas.email import ThreadStateEnum
from app.services.thread_view_service import preview_text_for_message, reply_text_for_message

pytestmark = pytest.mark.db

MAILBOX = "sales@example.com"
ALICE = "alice@example.com"
T1 = datetime(2026, 8, 22, 14, 41, tzinfo=UTC)
T2 = datetime(2026, 8, 22, 14, 43, tzinfo=UTC)
T3 = datetime(2026, 8, 22, 15, 2, tzinfo=UTC)

INBOUND_1 = "Can you check the Vercel deploy?"
QUOTE_ONLY = (
    "From: Alice <alice@example.com>\n"
    "Sent: Monday, August 22, 2022 10:43 AM\n"
    "To: sales@example.com\n"
    "Subject: Re: Vercel deploy\n\n"
    "Can you check the Vercel deploy?"
)
INBOUND_3_UNIQUE = "The GitHub action is failing on main."
INBOUND_3_FULL = (
    f"{INBOUND_3_UNIQUE}\n\n"
    "From: Sales <sales@example.com>\n"
    "Sent: Monday, August 22, 2022 10:43 AM\n"
    "To: alice@example.com\n"
    "Subject: Re: Vercel deploy\n\n"
    "Will do."
)

# Stored unique_body still contains prior Zendesk comment (pre-fix ingest).
ZENDESK_UNIQUE_WALL = (
    "Your request (40197) has been updated. To add additional comments, "
    "reply to this email.\n\n"
    "Alex Taylor (SampleHelpdesk)\n\n"
    "Aug 25, 2026, 8:55 AM MDT\n\n"
    "Elise,\n\n"
    "Can you provide a search ID that I can look into?\n\n"
    "Alex Taylor\n"
    "Customer Support\n\n"
    "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n"
    "info\n\n"
    "Aug 25, 2026, 8:19 AM MDT\n\n"
    "This is a follow-up to your previous request #40155\n\n"
    "Hi there.\n\n"
    "I updated the method for delivery last week.\n\n"
    "Thanks,\n"
    "Elise\n"
)


def test_reply_text_quote_splits_stored_zendesk_unique_wall() -> None:
    """Existing rows: unique_body embeds prior ticket comments — display tip only."""
    reply = reply_text_for_message(
        body_text=ZENDESK_UNIQUE_WALL,
        unique_body_text=ZENDESK_UNIQUE_WALL,
        body_preview="Your request (40197) has been updated.",
    )
    assert "search ID that I can look into" in reply
    assert "Alex Taylor" in reply
    assert "follow-up to your previous request" not in reply
    assert "I updated the method for delivery" not in reply
    assert "Thanks,\nElise" not in reply


# Worked example: SampleHelpdesk “solved” dump with three agent comments in a row.
ZENDESK_STACKED_AGENTS = (
    "Your request (40145) has been solved. To add additional comments, reply to this email.\n\n"
    "[https://sample-helpdesk.example.com/system/photos/25898357572372/purple_flower_5.jpg]\n\n"
    "Alex Taylor (SampleHelpdesk)\n\n"
    "Aug 26, 2026, 3:48 PM MDT\n\n"
    "Elise,\n\n"
    "I am going to go ahead and close out this ticket.\n\n"
    "Alex Taylor\n"
    "Customer Support\n\n"
    "[https://sample-helpdesk.example.com/system/photos/25898357572372/purple_flower_5.jpg]\n\n"
    "Alex Taylor (SampleHelpdesk)\n\n"
    "Aug 25, 2026, 10:11 AM MDT\n\n"
    "Elise,\n\n"
    "I wanted to check in with you on this to see if you have further questions.\n\n"
    "Alex Taylor\n"
    "Customer Support\n\n"
    "[https://sample-helpdesk.example.com/images/2016/default-avatar-80.png]\n\n"
    "info\n\n"
    "Aug 20, 2026, 1:18 PM MDT\n\n"
    "If the public link is enabled, are we still able to send invitations?\n"
)


def test_reply_text_splits_zendesk_stacked_agent_photo_wall() -> None:
    """Agent-to-agent system/photos separators must not leave a multi-tip wall."""
    reply = reply_text_for_message(
        body_text=ZENDESK_STACKED_AGENTS,
        unique_body_text=ZENDESK_STACKED_AGENTS,
        body_preview="Your request (40145) has been solved.",
    )
    assert "close out this ticket" in reply
    assert reply.count("Alex Taylor (SampleHelpdesk)") == 1
    assert "further questions" not in reply
    assert "are we still able to send invitations" not in reply


def test_preview_text_for_card_uses_reply_pipeline_not_raw_graph_preview() -> None:
    """Mailbox cards must not show Zendesk delimiter junk from bodyPreview."""
    preview = preview_text_for_message(
        body_text=ZENDESK_STACKED_AGENTS,
        unique_body_text=ZENDESK_STACKED_AGENTS,
        body_preview=(
            "##- Please type your reply above this line -## Your request (40145) has been updated."
        ),
    )
    assert preview is not None
    assert "Please type your reply above this line" not in preview
    assert "close out this ticket" in preview


@pytest.fixture
def local_settings() -> Settings:
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


@pytest.mark.asyncio
async def test_thread_detail_reply_text_is_unique_content_not_quoted_wall(
    local_settings: Settings,
    db_session: AsyncSession,
) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="conv-vercel-1",
        subject="Re: Vercel deploy",
        state=ThreadStateEnum.DRAFTED.value,
        last_message_at=T3,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add_all(
        [
            Message(
                id=uuid.uuid4(),
                thread_id=thread.id,
                graph_message_id="AAMk-in-1",
                direction="inbound",
                sender=ALICE,
                body_text=INBOUND_1,
                body_preview=INBOUND_1,
                unique_body_text=INBOUND_1,
                received_at=T1,
                to_recipients=[MAILBOX],
            ),
            Message(
                id=uuid.uuid4(),
                thread_id=thread.id,
                graph_message_id="AAMk-out-2",
                direction="outbound",
                sender=MAILBOX,
                body_text=QUOTE_ONLY,
                body_preview=QUOTE_ONLY[:80],
                unique_body_text="",
                received_at=T2,
                to_recipients=[ALICE],
            ),
            Message(
                id=uuid.uuid4(),
                thread_id=thread.id,
                graph_message_id="AAMk-in-3",
                direction="inbound",
                sender=ALICE,
                body_text=INBOUND_3_FULL,
                body_preview=INBOUND_3_UNIQUE,
                unique_body_text=None,
                received_at=T3,
                to_recipients=[MAILBOX],
            ),
        ]
    )
    await db_session.commit()

    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: local_settings

    async def fake_user() -> UserMe:
        return UserMe(
            id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            email="elise@example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: None
    application.dependency_overrides[get_openai_client] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.get(f"/api/threads/{thread.id}")

    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 200, resp.text
    messages = resp.json()["messages"]
    assert [m["sender"] for m in messages] == [ALICE, MAILBOX, ALICE]
    assert messages[0]["reply_text"] == INBOUND_1
    assert messages[1]["reply_text"] == ""
    assert messages[1]["body_text"] == QUOTE_ONLY
    assert messages[2]["reply_text"] == INBOUND_3_UNIQUE
    assert "From: Sales" not in messages[2]["reply_text"]
    assert "From: Sales" in messages[2]["body_text"]
