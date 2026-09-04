"""Workflow extraction unit tests.

Oracle (plan §W):
  - Flags off → no classify / no atomize
  - SPECIAL_CASE → no atomize
  - ROUTINE_WORKFLOW → atomize with source_kind=workflow_extraction
    and one atom_widening promotion proposal
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.services.workflow_extraction_service import maybe_extract_workflow

MAILBOX = "sales@example.com"


def _settings(*, paired: bool = True, atoms: bool = True) -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
        paired_retrieval_mailboxes=MAILBOX if paired else "",
        feedback_atoms_enabled=atoms,
    )


def _atom() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.asyncio
async def test_skips_when_paired_retrieval_disabled() -> None:
    session = AsyncMock()
    client = AsyncMock()
    with patch(
        "app.services.workflow_extraction_service._classify_thread",
        new_callable=AsyncMock,
    ) as classify:
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(paired=False),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="Vendor outage resolved",
        )
    classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_when_atoms_flag_off() -> None:
    session = AsyncMock()
    client = AsyncMock()
    with patch(
        "app.services.workflow_extraction_service._classify_thread",
        new_callable=AsyncMock,
    ) as classify:
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(atoms=False),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="Vendor outage resolved",
        )
    classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_special_case_does_not_atomize() -> None:
    session = AsyncMock()
    client = AsyncMock()
    with (
        patch(
            "app.services.workflow_extraction_service._classify_thread",
            AsyncMock(return_value="SPECIAL_CASE"),
        ),
        patch(
            "app.services.workflow_extraction_service.atomize_and_persist",
            new_callable=AsyncMock,
        ) as atomize,
    ):
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="One-off VIP request",
            thread_summary="VIP asked for a custom exception",
        )
    atomize.assert_not_awaited()


@pytest.mark.asyncio
async def test_routine_workflow_atomizes_and_proposes() -> None:
    """ROUTINE_WORKFLOW → atomize(source_kind=workflow_extraction) + atom_widening proposal."""
    session = AsyncMock()
    client = AsyncMock()
    thread_id = uuid.uuid4()
    atom = _atom()

    with (
        patch(
            "app.services.workflow_extraction_service._classify_thread",
            AsyncMock(return_value="ROUTINE_WORKFLOW"),
        ),
        patch(
            "app.services.workflow_extraction_service.atomize_and_persist",
            AsyncMock(return_value=[atom]),
        ) as atomize,
        patch(
            "app.services.workflow_extraction_service.promotion_proposal_repo.create_promotion_proposal",
            AsyncMock(),
        ) as create_proposal,
    ):
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=thread_id,
            email_text="Statuspage resolved for payment-api",
            thread_summary="Acked, waited for green, closed",
            sender_address="noreply@statuspage.io",
            sender_domain="statuspage.io",
            routing_category="vendor",
        )

    atomize.assert_awaited_once()
    kwargs = atomize.await_args.kwargs
    assert kwargs["source_kind"] == "workflow_extraction"
    assert kwargs["source_id"] == thread_id
    assert kwargs["mailbox"] == MAILBOX
    assert kwargs["text"] == "Statuspage resolved for payment-api"

    create_proposal.assert_awaited_once()
    prop_kwargs = create_proposal.await_args.kwargs
    assert prop_kwargs["kind"] == "atom_widening"
    assert prop_kwargs["mailbox"] == MAILBOX
    assert prop_kwargs["evidence_ids"] == [atom.id]
    assert isinstance(prop_kwargs["expires_at"], datetime)
    assert prop_kwargs["payload"]["person_bound"] is True
    assert prop_kwargs["impact_num"] == 1
    assert prop_kwargs["impact_den"] == 1


@pytest.mark.asyncio
async def test_admin_does_not_atomize() -> None:
    session = AsyncMock()
    client = AsyncMock()
    with (
        patch(
            "app.services.workflow_extraction_service._classify_thread",
            AsyncMock(return_value="ADMIN"),
        ),
        patch(
            "app.services.workflow_extraction_service.atomize_and_persist",
            new_callable=AsyncMock,
        ) as atomize,
    ):
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="Test ping — ignore",
        )
    atomize.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_atoms_do_not_create_proposal() -> None:
    session = AsyncMock()
    client = AsyncMock()
    with (
        patch(
            "app.services.workflow_extraction_service._classify_thread",
            AsyncMock(return_value="ROUTINE_WORKFLOW"),
        ),
        patch(
            "app.services.workflow_extraction_service.atomize_and_persist",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.workflow_extraction_service.promotion_proposal_repo.create_promotion_proposal",
            AsyncMock(),
        ) as create_proposal,
    ):
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="Statuspage resolved for payment-api",
        )
    create_proposal.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomization_text_is_the_atomize_source() -> None:
    """Resolve capture passes actions_taken as atomization_text; that is the note."""
    session = AsyncMock()
    client = AsyncMock()
    with (
        patch(
            "app.services.workflow_extraction_service._classify_thread",
            AsyncMock(return_value="ROUTINE_WORKFLOW"),
        ),
        patch(
            "app.services.workflow_extraction_service.atomize_and_persist",
            AsyncMock(return_value=[_atom()]),
        ) as atomize,
        patch(
            "app.services.workflow_extraction_service.promotion_proposal_repo.create_promotion_proposal",
            AsyncMock(),
        ),
    ):
        await maybe_extract_workflow(
            session,
            client=client,
            settings=_settings(),
            openai_client=None,
            mailbox=MAILBOX,
            thread_id=uuid.uuid4(),
            email_text="Actions taken: Checked portal\nSubject: Daily Drivers",
            atomization_text="Checked portal and confirmed no client action needed",
        )
    assert (
        atomize.await_args.kwargs["text"] == "Checked portal and confirmed no client action needed"
    )


@pytest.mark.asyncio
async def test_classify_thread_returns_routine_from_haiku_json() -> None:
    """Oracle: the prompt contract is JSON {classification: ROUTINE_WORKFLOW}."""
    from app.services.workflow_extraction_service import _classify_thread

    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=MagicMock(content=[MagicMock(text='{"classification": "ROUTINE_WORKFLOW"}')])
    )
    result = await _classify_thread(
        client,
        _settings(),
        email_text="Vendor outage resolved",
        thread_summary="Acked and closed",
    )
    assert result == "ROUTINE_WORKFLOW"
