"""Tests for draft regeneration service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.core.exceptions import DraftGenerationError, ThreadNotFoundError
from app.llm.draft_generator import DraftCallResult
from app.models.schemas.dashboard import TriageFlags
from app.models.schemas.draft import DraftResponseSchema, DraftSchema
from app.repositories.message_repo import MessageSchema
from app.services import draft_regeneration_service
from app.services.feedback_draft_context import PairedDraftContext


def _settings() -> Settings:
    return Settings(
        draft_model="claude-sonnet-4-6",
        anthropic_api_key="test-key",
        target_mailboxes="elise@example.com",
        salute_directory_enabled=False,
        thread_context_enabled=False,
    )


def _thread():
    return type(
        "T",
        (),
        {
            "id": uuid.uuid4(),
            "mailbox": "elise@example.com",
            "conversation_id": "c1",
            "subject": "Need docs",
        },
    )()


def _message(thread_id: uuid.UUID) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="graph-m1",
        direction="inbound",
        sender="vendor@example.com",
        body_text="Please send docs",
        body_preview="Please send",
        received_at=datetime.now(UTC),
        to_recipients=["elise@example.com"],
        cc_recipients=[],
        has_attachments=False,
    )


def _draft_schema() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need docs",
        reply_body="Acknowledged.",
        teaching_note="Simple acknowledge",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )


@pytest.mark.asyncio
async def test_regenerate_creates_new_draft() -> None:
    thread = _thread()
    message = _message(thread.id)
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Need docs",
        reply_body="Acknowledged only.",
        teaching_note="Acknowledge path",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)
    client = AsyncMock()

    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(
                return_value=TriageFlags(
                    is_spam=False,
                    has_action_items=True,
                    draft_needed=True,
                    needs_context=False,
                    action_items_summary="Send docs",
                    routing_category="billing",
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_selection_service.select_skills",
            AsyncMock(
                return_value=MagicMock(blocks=[], skill_ids=[], applied=[]),
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.tone_profile_service.load_for_draft",
            AsyncMock(return_value=(None, ["Thanks — sending the packet now."])),
        ),
        patch(
            "app.services.draft_regeneration_service.load_legacy_negative_constraints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.urgency_feedback_service.find_urgency_hints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=_draft_schema(),
                    prompt_version="2026-07-29.1",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=50,
                )
            ),
        ) as gen_mock,
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ) as create_mock,
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ) as audit_mock,
    ):
        result = await draft_regeneration_service.regenerate_draft(
            session,
            client=client,
            settings=_settings(),
            thread_id=thread.id,
            instruction="just acknowledge, no action items",
            actor="elise@example.com",
        )

    assert result.id == persisted.id
    assert gen_mock.await_args.kwargs["instruction"] == "just acknowledge, no action items"
    assert gen_mock.await_args.kwargs["tone_references"] == ["Thanks — sending the packet now."]
    create_mock.assert_awaited_once()
    assert audit_mock.await_args.kwargs["event_type"] == "draft.regenerated"


@pytest.mark.asyncio
async def test_force_letter_overrides_briefing_triage() -> None:
    thread = _thread()
    message = _message(thread.id)
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Update",
        reply_body="Thanks for the update.",
        teaching_note="Ack",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)

    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(
                return_value=TriageFlags(
                    is_spam=False,
                    has_action_items=False,
                    draft_needed=False,
                    needs_context=False,
                    routing_category="general",
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_selection_service.select_skills",
            AsyncMock(return_value=MagicMock(blocks=[], skill_ids=[], applied=[])),
        ),
        patch(
            "app.services.draft_regeneration_service.tone_profile_service.load_for_draft",
            AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_regeneration_service.load_legacy_negative_constraints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.urgency_feedback_service.find_urgency_hints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=_draft_schema(),
                    prompt_version="2026-08-31.1",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=50,
                )
            ),
        ) as gen_mock,
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        await draft_regeneration_service.generate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
            actor="elise@example.com",
        )

    triage_passed = gen_mock.await_args.args[2]
    assert triage_passed.draft_needed is True
    assert triage_passed.has_action_items is True


@pytest.mark.asyncio
async def test_generate_draft_rejects_spam_thread() -> None:
    thread = _thread()
    message = _message(thread.id)
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(
                return_value=TriageFlags(
                    is_spam=True,
                    has_action_items=False,
                    draft_needed=False,
                    needs_context=False,
                    routing_category="general",
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        pytest.raises(DraftGenerationError, match="spam"),
    ):
        await draft_regeneration_service.generate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
        )


@pytest.mark.asyncio
async def test_regenerate_thread_not_found() -> None:
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=None),
        ),
        pytest.raises(ThreadNotFoundError),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=uuid.uuid4(),
            instruction="acknowledge",
        )


@pytest.mark.asyncio
async def test_regenerate_no_messages() -> None:
    thread = _thread()
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[]),
        ),
        pytest.raises(DraftGenerationError),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
            instruction="acknowledge",
        )


def _outbound_message(thread_id: uuid.UUID) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=thread_id,
        graph_message_id="msg-out",
        direction="outbound",
        sender="elise@example.com",
        body_text="Already sent",
        body_preview="Already sent",
        received_at=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        to_recipients=["client@example.com"],
        cc_recipients=[],
        has_attachments=False,
    )


@pytest.mark.asyncio
async def test_regenerate_refuses_letter_when_outbound_tip_in_sync() -> None:
    from app.core.exceptions import ThreadStateError
    from app.models.schemas.graph import GraphMessageSchema

    thread = _thread()
    outbound = _outbound_message(thread.id)
    inbound = _message(thread.id)
    inbound.graph_message_id = "msg-in"
    inbound.direction = "inbound"
    inbound.received_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    graph_client = AsyncMock()
    graph_client.list_thread_messages = AsyncMock(
        return_value=[
            GraphMessageSchema.model_validate(
                {
                    "id": "msg-in",
                    "subject": "Need docs",
                    "from": {"emailAddress": {"address": "vendor@example.com"}},
                    "receivedDateTime": "2026-09-01T12:00:00Z",
                    "conversationId": "c1",
                }
            ),
            GraphMessageSchema.model_validate(
                {
                    "id": "msg-out",
                    "subject": "Need docs",
                    "from": {"emailAddress": {"address": "elise@example.com"}},
                    "receivedDateTime": "2026-09-01T13:00:00Z",
                    "conversationId": "c1",
                }
            ),
        ]
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[inbound, outbound]),
        ),
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            AsyncMock(return_value=[inbound, outbound]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(),
        ) as gen_mock,
        pytest.raises(ThreadStateError),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
            instruction="write a letter",
            force_letter=True,
            graph_client=graph_client,
        )
    gen_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_regenerate_letter_when_graph_has_newer_inbound() -> None:
    from app.models.schemas.graph import GraphMessageSchema

    thread = _thread()
    outbound = _outbound_message(thread.id)
    inbound = _message(thread.id)
    inbound.graph_message_id = "msg-in"
    inbound.direction = "inbound"
    inbound.received_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    graph_client = AsyncMock()
    graph_client.list_thread_messages = AsyncMock(
        return_value=[
            GraphMessageSchema.model_validate(
                {
                    "id": "msg-in",
                    "subject": "Need docs",
                    "from": {"emailAddress": {"address": "vendor@example.com"}},
                    "receivedDateTime": "2026-09-01T12:00:00Z",
                    "conversationId": "c1",
                }
            ),
            GraphMessageSchema.model_validate(
                {
                    "id": "msg-out",
                    "subject": "Need docs",
                    "from": {"emailAddress": {"address": "elise@example.com"}},
                    "receivedDateTime": "2026-09-01T13:00:00Z",
                    "conversationId": "c1",
                }
            ),
            GraphMessageSchema.model_validate(
                {
                    "id": "msg-followup",
                    "subject": "Need docs",
                    "from": {"emailAddress": {"address": "vendor@example.com"}},
                    "receivedDateTime": "2026-09-01T14:00:00Z",
                    "conversationId": "c1",
                }
            ),
        ]
    )
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Need docs",
        reply_body="Dear client, here is a draft.",
        teaching_note="Follow up",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)
    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[inbound, outbound]),
        ),
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            AsyncMock(return_value=[inbound, outbound]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(
                return_value=TriageFlags(
                    is_spam=False,
                    has_action_items=True,
                    draft_needed=True,
                    needs_context=False,
                    routing_category="general",
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_selection_service.select_skills",
            AsyncMock(return_value=MagicMock(blocks=[], skill_ids=[], applied=[])),
        ),
        patch(
            "app.services.draft_regeneration_service.tone_profile_service.load_for_draft",
            AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_regeneration_service.load_legacy_negative_constraints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.urgency_feedback_service.find_urgency_hints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=DraftSchema(
                        subject_line="Re: Need docs",
                        reply_body="Dear client, here is a draft.",
                        teaching_note="Follow up",
                        urgency="NORMAL",
                        urgency_reason="Routine",
                        suggested_actions=[],
                    ),
                    prompt_version="v1",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=10,
                )
            ),
        ) as gen_mock,
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        result = await draft_regeneration_service.regenerate_draft(
            session,
            client=AsyncMock(),
            settings=_settings(),
            thread_id=thread.id,
            instruction="write a letter",
            force_letter=True,
            graph_client=graph_client,
        )
    assert result.reply_body == "Dear client, here is a draft."
    gen_mock.assert_awaited()


@pytest.mark.asyncio
async def test_regenerate_merges_paired_constraints_and_persists_retrieved_ids() -> None:
    """Paired retrieval constraints prepend negatives; atom/note ids persist on draft.

    Oracle: fixed UUID literals and constraint text from the mock — not recomputed.
    """
    atom_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    note_id = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    thread = _thread()
    message = _message(thread.id)
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Need docs",
        reply_body="Acknowledged only.",
        teaching_note="Acknowledge path",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
        retrieved_atom_ids=[atom_id],
        retrieved_note_ids=[note_id],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)
    client = AsyncMock()
    instruction = "tighten the salutation"

    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_selection_service.select_skills",
            AsyncMock(return_value=MagicMock(blocks=[], skill_ids=[], applied=[])),
        ),
        patch(
            "app.services.draft_regeneration_service.tone_profile_service.load_for_draft",
            AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_regeneration_service.load_legacy_negative_constraints",
            AsyncMock(return_value=["legacy rejection constraint"]),
        ),
        patch(
            "app.services.draft_regeneration_service.urgency_feedback_service.find_urgency_hints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.load_paired_constraints",
            AsyncMock(
                return_value=PairedDraftContext(
                    fix_constraints=["Never promise an SLA in writing"],
                    pair_blocks=[],
                    atom_ids=[atom_id],
                    note_ids=[note_id],
                )
            ),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=_draft_schema(),
                    prompt_version="2026-09-01.3",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=50,
                )
            ),
        ) as gen_mock,
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ) as create_mock,
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ),
        patch(
            "app.services.draft_regeneration_service.atomize_and_persist",
            AsyncMock(return_value=[]),
        ) as atomize_mock,
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=client,
            settings=_settings(),
            thread_id=thread.id,
            instruction=instruction,
            actor="elise@example.com",
        )

    negatives = gen_mock.await_args.kwargs["negative_constraints"]
    assert negatives[0] == "Never promise an SLA in writing"
    assert "legacy rejection constraint" in negatives
    assert create_mock.await_args.kwargs["retrieved_atom_ids"] == [atom_id]
    assert create_mock.await_args.kwargs["retrieved_note_ids"] == [note_id]
    assert atomize_mock.await_args.kwargs["source_kind"] == "regenerate_instruction"
    assert atomize_mock.await_args.kwargs["text"] == instruction


@pytest.mark.asyncio
async def test_regen_persists_validator_retry_body() -> None:
    """draft_validator_enabled + Haiku violation → persisted body is the Sonnet retry."""
    import json

    retry_body = "Hi,\n\nPlease see attached invoice #2026-001.\n\nRegards"
    atom_id = uuid.UUID("cccccccc-0000-0000-0000-000000000001")
    thread = _thread()
    message = _message(thread.id)
    persisted = DraftResponseSchema(
        id=uuid.uuid4(),
        thread_id=thread.id,
        created_at=datetime.now(UTC),
        subject_line="Re: Need docs",
        reply_body=retry_body,
        teaching_note="Acknowledge path",
        urgency="NORMAL",
        urgency_reason="Routine",
        suggested_actions=[],
    )
    session = AsyncMock()
    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)

    haiku = MagicMock()
    haiku.content = [
        MagicMock(
            text=json.dumps(
                {"violations": [{"atom_id": str(atom_id), "reason": "invoice number missing"}]}
            )
        )
    ]
    sonnet = MagicMock()
    sonnet.content = [MagicMock(text=retry_body)]
    client = AsyncMock()
    client.messages.create = AsyncMock(side_effect=[haiku, sonnet])

    settings = Settings(
        draft_model="claude-sonnet-4-6",
        classification_model="claude-haiku-4-5",
        anthropic_api_key="test-key",
        target_mailboxes="elise@example.com",
        salute_directory_enabled=False,
        draft_validator_enabled=True,
        thread_context_enabled=False,
    )

    with (
        patch(
            "app.services.draft_regeneration_service.thread_repo.get_by_id",
            AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.draft_regeneration_service.message_repo.list_by_thread",
            AsyncMock(return_value=[message]),
        ),
        patch(
            "app.services.draft_regeneration_service.audit_repo.get_latest_triage_flags",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.get_latest_by_thread",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.draft_regeneration_service.skill_selection_service.select_skills",
            AsyncMock(return_value=MagicMock(blocks=[], skill_ids=[], applied=[])),
        ),
        patch(
            "app.services.draft_regeneration_service.tone_profile_service.load_for_draft",
            AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.draft_regeneration_service.load_legacy_negative_constraints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.urgency_feedback_service.find_urgency_hints",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.load_paired_constraints",
            AsyncMock(
                return_value=PairedDraftContext(
                    fix_constraints=["Always include the invoice number"],
                    pair_blocks=[],
                    atom_ids=[atom_id],
                    note_ids=[],
                    fix_atom_payloads=[
                        {
                            "id": str(atom_id),
                            "atom_text": "Always include the invoice number explicitly",
                            "applies_when": None,
                        }
                    ],
                )
            ),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_llm.generate_draft",
            AsyncMock(
                return_value=DraftCallResult(
                    draft=DraftSchema(
                        subject_line="Re: Need docs",
                        reply_body="Hi there,\n\nPlease see attached.\n\nRegards",
                        teaching_note="Follow up",
                        urgency="NORMAL",
                        urgency_reason="Routine",
                        suggested_actions=[],
                    ),
                    prompt_version="2026-09-04.2",
                    model="claude-sonnet-4-6",
                    input_tokens=10,
                    output_tokens=20,
                    latency_ms=50,
                )
            ),
        ),
        patch(
            "app.services.draft_regeneration_service.draft_repo.create_regenerated_draft",
            AsyncMock(return_value=persisted),
        ) as create_mock,
        patch(
            "app.services.draft_regeneration_service.audit_service.log_event",
            AsyncMock(),
        ),
    ):
        await draft_regeneration_service.regenerate_draft(
            session,
            client=client,
            settings=settings,
            thread_id=thread.id,
            actor="elise@example.com",
        )

    persisted_draft = create_mock.await_args.kwargs["draft"]
    assert persisted_draft.reply_body == retry_body
