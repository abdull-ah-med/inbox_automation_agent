"""Plan 1: never generate a letter when the tip is outbound and Graph agrees."""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.graph import GraphMessageSchema, IngestResultSchema
from app.repositories.message_repo import MessageSchema
from app.services import pipeline_service

_THREAD_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
T1 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 1, 13, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
LETTER_BODY = "Dear client, here is a draft."
BRIEFING_BODY = ""


def _session_factory() -> MagicMock:
    session = AsyncMock()
    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=None)
    session.begin = MagicMock(return_value=begin_cm)
    session.in_transaction = MagicMock(return_value=False)
    factory_cm = AsyncMock()
    factory_cm.__aenter__ = AsyncMock(return_value=session)
    factory_cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=factory_cm)


def _email(
    message_id: str,
    *,
    direction: EmailDirectionEnum,
    sender: str,
    received_at: datetime,
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id="conv-1",
        mailbox="elise@example.com",
        sender=sender,
        subject="Need docs",
        body_text="body",
        received_at=received_at,
        direction=direction,
    )


def _local_row(
    graph_message_id: str,
    *,
    direction: str,
    sender: str,
    received_at: datetime,
) -> MessageSchema:
    return MessageSchema(
        id=uuid.uuid4(),
        thread_id=_THREAD_ID,
        graph_message_id=graph_message_id,
        direction=direction,
        sender=sender,
        body_text="body",
        received_at=received_at,
        to_recipients=["other@example.com"],
        cc_recipients=[],
    )


def _graph_msg(message_id: str, received: str, sender: str) -> GraphMessageSchema:
    return GraphMessageSchema.model_validate(
        {
            "id": message_id,
            "subject": "Need docs",
            "bodyPreview": "body",
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"address": sender}},
            "receivedDateTime": received,
            "conversationId": "conv-1",
        }
    )


def _ingest(*, trigger: str, messages: list[EmailMessageSchema]) -> IngestResultSchema:
    return IngestResultSchema(
        message_id=trigger,
        status="ingested",
        thread_id=str(_THREAD_ID),
        conversation_id="conv-1",
        thread_context=ThreadContextSchema(
            conversation_id="conv-1",
            mailbox="elise@example.com",
            subject="Need docs",
            messages=messages,
        ),
    )


async def _run_triage(state: object, **_: object) -> object:
    state.triage = TriageResultSchema(  # type: ignore[attr-defined]
        is_spam=False,
        has_action_items=True,
        action_items_summary="Send packet",
        needs_context=False,
        draft_needed=True,
        routing_category="general",
    )
    state.draft_status = "PENDING"  # type: ignore[attr-defined]
    return state


def _generated(reply_body: str) -> SimpleNamespace:
    return SimpleNamespace(
        draft=DraftSchema(
            subject_line="Re: Need docs",
            reply_body=reply_body,
            teaching_note="Briefing" if reply_body == "" else "Letter",
            urgency="NORMAL",
            urgency_reason="Routine",
            suggested_actions=[],
        ),
        prompt_version="v1",
        tool_calls=[],
        model="claude-test",
        latency_ms=10,
    )


async def _generate(email: object, ctx: object, triage: TriageResultSchema, **_: object) -> object:
    if triage.draft_needed:
        return _generated(LETTER_BODY)
    return _generated(BRIEFING_BODY)


def _pipeline_patches(
    *,
    local_rows: list[MessageSchema],
    graph_messages: list[GraphMessageSchema],
    graph_client: MagicMock,
):
    skill_sel = MagicMock(
        blocks=[],
        skill_ids=[],
        applied=[],
        candidate_ids=[],
        selected_pool_ids=[],
        always_ids=[],
    )
    return (
        patch(
            "app.services.pipeline.service.triage_service.run_triage",
            new=AsyncMock(side_effect=_run_triage),
        ),
        patch(
            "app.services.sent_reply_service.message_repo.list_by_thread",
            new=AsyncMock(return_value=local_rows),
        ),
        patch(
            "app.services.pipeline.service.message_repo.list_by_thread",
            new=AsyncMock(return_value=local_rows),
        ),
        patch(
            "app.services.pipeline.already_replied.message_repo.list_by_thread",
            new=AsyncMock(return_value=local_rows),
        ),
        patch(
            "app.services.pipeline.already_replied.sent_reply_service.resolve_thread_from_outbound",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.sent_reply_service.sent_reply_repo.get_by_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.sent_reply_repo.get_by_thread",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.thread_repo.get_by_id_trusted",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.latest_proposed_has_teaching_note",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.services.pipeline.service.audit_service.log_event",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.draft_llm.generate_draft",
            new=AsyncMock(side_effect=_generate),
        ),
        patch(
            "app.services.pipeline.service.draft_repo.get_draft_by_message",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service.skill_repo.list_active_for_selection",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.pipeline.service.skill_selection_service.select_from_active",
            new=AsyncMock(return_value=skill_sel),
        ),
        patch(
            "app.services.pipeline.service.skill_selection_service.log_skills_selected",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.tone_profile_service.load_for_draft",
            new=AsyncMock(return_value=(None, [])),
        ),
        patch(
            "app.services.pipeline.service.rejection_memory_service.find_negative_constraints",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.pipeline.service.urgency_feedback_service.find_urgency_hints",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.related_thread_service.load_confirmed_contexts",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.recurrence_service.count_automated_inbound_48h",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "app.services.pipeline.service.draft_repo.create_draft",
            new=AsyncMock(
                side_effect=lambda session, **kwargs: SimpleNamespace(
                    id=uuid.uuid4(),
                    model_dump=lambda **_: kwargs["draft"].model_dump(),
                )
            ),
        ),
        patch(
            "app.services.pipeline.service.thread_repo.set_thread_outcome",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.service._summarize_non_spam",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service._refresh_thread_summary_safe",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.service.embedding_service.embed_and_store_safe",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.already_replied.sent_reply_learning_service.promote_sent_reply_as_approved",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.pipeline.already_replied.sent_reply_learning_service.store_promoted_reply_memory",
            new=AsyncMock(),
        ),
        patch(
            "app.services.pipeline.already_replied.thread_repo.set_thread_outcome",
            new=AsyncMock(),
        ),
        patch(
            "app.core.dependencies.openai_client_from_settings",
            return_value=None,
        ),
    )


@pytest.mark.asyncio
async def test_phased_pipeline_no_letter_when_outbound_tip_in_sync() -> None:
    inbound = _email(
        "msg-in", direction=EmailDirectionEnum.INBOUND, sender="client@example.com", received_at=T1
    )
    outbound = _email(
        "msg-out",
        direction=EmailDirectionEnum.OUTBOUND,
        sender="elise@example.com",
        received_at=T2,
    )
    local = [
        _local_row("msg-in", direction="inbound", sender="client@example.com", received_at=T1),
        _local_row("msg-out", direction="outbound", sender="elise@example.com", received_at=T2),
    ]
    graph_msgs = [
        _graph_msg("msg-in", "2026-09-01T12:00:00Z", "client@example.com"),
        _graph_msg("msg-out", "2026-09-01T13:00:00Z", "elise@example.com"),
    ]
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock(return_value=graph_msgs)
    graph_client.get_message = AsyncMock(return_value=graph_msgs[-1])

    with ExitStack() as stack:
        for p in _pipeline_patches(
            local_rows=local, graph_messages=graph_msgs, graph_client=graph_client
        ):
            stack.enter_context(p)
        state = await pipeline_service._run_phased_post_ingest(
            redis=AsyncMock(),
            settings=Settings(
                environment="local",
                openai_api_key="",
                salute_directory_enabled=False,
            ),
            client=AsyncMock(),
            openai_client=None,
            ingest_result=_ingest(trigger="msg-out", messages=[inbound, outbound]),
            session_factory=_session_factory(),
            graph_client=graph_client,
            post_slack=False,
        )

    assert max(local, key=lambda m: m.received_at).graph_message_id == "msg-out"
    assert graph_msgs[-1].id == "msg-out"
    assert state.triage is not None
    assert state.triage.draft_needed is False
    assert state.draft is not None
    assert state.draft.reply_body == BRIEFING_BODY
    assert state.draft.reply_body != LETTER_BODY


@pytest.mark.asyncio
async def test_phased_pipeline_letter_when_graph_has_newer_inbound() -> None:
    inbound = _email(
        "msg-in", direction=EmailDirectionEnum.INBOUND, sender="client@example.com", received_at=T1
    )
    outbound = _email(
        "msg-out",
        direction=EmailDirectionEnum.OUTBOUND,
        sender="elise@example.com",
        received_at=T2,
    )
    local = [
        _local_row("msg-in", direction="inbound", sender="client@example.com", received_at=T1),
        _local_row("msg-out", direction="outbound", sender="elise@example.com", received_at=T2),
    ]
    graph_msgs = [
        _graph_msg("msg-in", "2026-09-01T12:00:00Z", "client@example.com"),
        _graph_msg("msg-out", "2026-09-01T13:00:00Z", "elise@example.com"),
        _graph_msg("msg-followup", "2026-09-01T14:00:00Z", "client@example.com"),
    ]
    graph_client = MagicMock()
    graph_client.list_thread_messages = AsyncMock(return_value=graph_msgs)

    with ExitStack() as stack:
        for p in _pipeline_patches(
            local_rows=local, graph_messages=graph_msgs, graph_client=graph_client
        ):
            stack.enter_context(p)
        state = await pipeline_service._run_phased_post_ingest(
            redis=AsyncMock(),
            settings=Settings(
                environment="local",
                openai_api_key="",
                salute_directory_enabled=False,
            ),
            client=AsyncMock(),
            openai_client=None,
            ingest_result=_ingest(trigger="msg-out", messages=[inbound, outbound]),
            session_factory=_session_factory(),
            graph_client=graph_client,
            post_slack=False,
        )

    assert graph_msgs[-1].id == "msg-followup"
    assert state.triage is not None
    assert state.triage.draft_needed is True
    assert state.draft is not None
    assert state.draft.reply_body == LETTER_BODY
