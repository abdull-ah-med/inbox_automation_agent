"""Thread card preview uses cleaned reply text, not raw Graph bodyPreview."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.repositories import thread_repo

pytestmark = pytest.mark.db

MAILBOX = "info@sample-services.example.com"
T1 = datetime(2026, 9, 2, 23, 25, tzinfo=UTC)

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


@pytest.mark.asyncio
async def test_build_thread_summary_preview_matches_thread_view_reply_text(
    db_session,
) -> None:
    thread = Thread(
        id=uuid.uuid4(),
        mailbox=MAILBOX,
        conversation_id="zendesk-card-preview",
        subject="Re: [SampleHelpdesk] Re: Fw: Applicant Brittany Edwards",
        state=ThreadStateEnum.DRAFTED.value,
        last_message_at=T1,
    )
    db_session.add(thread)
    await db_session.flush()
    db_session.add(
        Message(
            id=uuid.uuid4(),
            thread_id=thread.id,
            graph_message_id=str(uuid.uuid4()),
            direction="inbound",
            sender="helpdesk@sample-helpdesk.example.com",
            body_text=ZENDESK_STACKED_AGENTS,
            unique_body_text=ZENDESK_STACKED_AGENTS,
            body_preview=(
                "##- Please type your reply above this line -## "
                "Your request (40145) has been updated."
            ),
            received_at=T1,
            to_recipients=[MAILBOX],
            cc_recipients=[],
        )
    )
    await db_session.commit()

    summary = await thread_repo.build_thread_summary(
        db_session, thread_repo.ThreadSchema.model_validate(thread)
    )

    assert summary.preview is not None
    assert "Please type your reply above this line" not in summary.preview
    assert "close out this ticket" in summary.preview
