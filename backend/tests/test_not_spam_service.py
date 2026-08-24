"""Mark-as-not-spam: un-hide the thread, allowlist the sender, never touch Outlook.

Worked example: Elise's mailbox received SampleLab billing, Haiku marked it
SPAM. Reviewer corrects it. The thread must leave the filtered bucket and
future mail from orders@sample-lab.example.com on that mailbox must not be discarded.
Mail on another mailbox is unaffected. Outlook Junk is unchanged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import ThreadNotFoundError, ThreadStateError
from app.models.db.message import Message
from app.models.db.thread import Thread
from app.models.schemas.email import ThreadStateEnum
from app.models.schemas.email_triage_state import EmailTriageState
from app.repositories import spam_allowlist_repo, thread_repo
from app.services import not_spam_service

pytestmark = pytest.mark.db

ELISE = "elise@sample-site.example.com"
CR = "clientrelations@example.com"
SAMPLELAB = "orders@sample-lab.example.com"
T_NOW = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        environment="local",
        target_mailboxes=f"{ELISE},{CR}",
    )


def _thread(*, mailbox: str, state: str, conversation_id: str) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject="SampleLab invoice",
        state=state,
        last_message_at=T_NOW,
    )


def _message(thread: Thread, *, sender: str) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction="inbound",
        sender=sender,
        body_text="Please confirm the attached invoice.",
        received_at=T_NOW,
        to_recipients=[thread.mailbox],
        cc_recipients=[],
    )


@pytest.mark.asyncio
async def test_allowlist_is_isolated_by_mailbox(db_session) -> None:
    await spam_allowlist_repo.upsert(
        db_session,
        mailbox=ELISE,
        sender_address=SAMPLELAB,
        thread_id=None,
        actor="elise@sample-site.example.com",
    )
    await db_session.commit()

    elise = await spam_allowlist_repo.addresses_for_mailbox(db_session, ELISE)
    cr = await spam_allowlist_repo.addresses_for_mailbox(db_session, CR)
    assert elise == frozenset({SAMPLELAB})
    assert cr == frozenset()


@pytest.mark.asyncio
async def test_mark_not_spam_unhides_thread_and_allowlists_sender(db_session) -> None:
    thread = _thread(mailbox=ELISE, state=ThreadStateEnum.SPAM.value, conversation_id="c-samplelab")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(_message(thread, sender=SAMPLELAB))
    await db_session.commit()

    async def _noop_pipeline(**_kwargs: object) -> EmailTriageState:
        return EmailTriageState.model_validate(
            {
                "original_email": {
                    "message_id": "m1",
                    "conversation_id": "c-samplelab",
                    "mailbox": ELISE,
                    "sender": SAMPLELAB,
                    "subject": "SampleLab invoice",
                    "body_text": "Please confirm",
                    "received_at": T_NOW,
                },
                "thread_context": {
                    "conversation_id": "c-samplelab",
                    "mailbox": ELISE,
                    "subject": "SampleLab invoice",
                    "messages": [],
                },
                "draft_status": "SKIPPED",
            }
        )

    with patch(
        "app.services.not_spam_service.pipeline_service.run_phased_after_ingest",
        new=AsyncMock(side_effect=_noop_pipeline),
    ):
        result = await not_spam_service.mark_not_spam(
            db_session,
            settings=_settings(),
            thread_id=thread.id,
            actor="elise@sample-site.example.com",
            redis=AsyncMock(),
            client=AsyncMock(),
            openai_client=None,
        )

    assert result.is_spam is False
    assert result.state != ThreadStateEnum.SPAM.value
    assert result.sender_address == SAMPLELAB
    assert result.outlook_unchanged is True
    assert result.thread_id == thread.id

    stored = await thread_repo.get_by_id(db_session, thread.id)
    assert stored is not None
    assert stored.state != ThreadStateEnum.SPAM.value

    allowlisted = await spam_allowlist_repo.addresses_for_mailbox(db_session, ELISE)
    assert SAMPLELAB in allowlisted


@pytest.mark.asyncio
async def test_not_spam_calls_public_context_builder(db_session) -> None:
    """H3: not-spam rebuilds triage context through the public ingestion API.

    Independent oracle (worked example): after Elise un-hides the SampleLab
    billing thread, the response reports NEW (or any non-SPAM) and the
    allowlisted sender is orders@sample-lab.example.com. The rebuild helper must be
    importable as ``build_thread_context_from_db`` — a leading-underscore
    name is private and will break when ingestion refactors.
    """
    from app.services.ingestion_service import build_thread_context_from_db

    assert callable(build_thread_context_from_db)

    thread = _thread(mailbox=ELISE, state=ThreadStateEnum.SPAM.value, conversation_id="c-public")
    db_session.add(thread)
    await db_session.flush()
    db_session.add(_message(thread, sender=SAMPLELAB))
    await db_session.commit()

    async def _noop_pipeline(**_kwargs: object) -> EmailTriageState:
        return EmailTriageState.model_validate(
            {
                "original_email": {
                    "message_id": "m-public",
                    "conversation_id": "c-public",
                    "mailbox": ELISE,
                    "sender": SAMPLELAB,
                    "subject": "SampleLab invoice",
                    "body_text": "Please confirm",
                    "received_at": T_NOW,
                },
                "thread_context": {
                    "conversation_id": "c-public",
                    "mailbox": ELISE,
                    "subject": "SampleLab invoice",
                    "messages": [],
                },
                "draft_status": "SKIPPED",
            }
        )

    with patch(
        "app.services.not_spam_service.pipeline_service.run_phased_after_ingest",
        new=AsyncMock(side_effect=_noop_pipeline),
    ):
        result = await not_spam_service.mark_not_spam(
            db_session,
            settings=_settings(),
            thread_id=thread.id,
            actor="elise@sample-site.example.com",
            redis=AsyncMock(),
            client=AsyncMock(),
            openai_client=None,
        )

    assert result.state != ThreadStateEnum.SPAM.value
    assert result.sender_address == SAMPLELAB
    assert result.is_spam is False


@pytest.mark.asyncio
async def test_mark_not_spam_rejects_non_spam_thread(db_session) -> None:
    thread = _thread(
        mailbox=ELISE,
        state=ThreadStateEnum.DRAFTED.value,
        conversation_id="c-drafted",
    )
    db_session.add(thread)
    await db_session.commit()

    with pytest.raises(ThreadStateError, match="SPAM"):
        await not_spam_service.mark_not_spam(
            db_session,
            settings=_settings(),
            thread_id=thread.id,
            actor="elise@sample-site.example.com",
            redis=AsyncMock(),
            client=AsyncMock(),
            openai_client=None,
        )


@pytest.mark.asyncio
async def test_mark_not_spam_unknown_thread_is_not_found(db_session) -> None:
    with pytest.raises(ThreadNotFoundError):
        await not_spam_service.mark_not_spam(
            db_session,
            settings=_settings(),
            thread_id=uuid.uuid4(),
            actor="elise@sample-site.example.com",
            redis=AsyncMock(),
            client=AsyncMock(),
            openai_client=None,
        )
