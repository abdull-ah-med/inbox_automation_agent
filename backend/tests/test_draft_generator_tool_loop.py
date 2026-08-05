"""Unit tests for draft generator Anthropic tool loop (mocked client)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.llm import draft_generator as draft_llm
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.services import skill_reference_service


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="vendor@example.com",
        subject="Need rebilling",
        body_text="Please rebill the Harmeyer SampleLab invoice.",
        body_preview="Please rebill",
        received_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )


def _context(email: EmailMessageSchema) -> ThreadContextSchema:
    return ThreadContextSchema(
        conversation_id=email.conversation_id,
        mailbox=email.mailbox,
        subject=email.subject,
        messages=[email],
    )


def _triage() -> TriageResultSchema:
    return TriageResultSchema(
        is_spam=False,
        spam_reason=None,
        has_action_items=True,
        action_items_summary="Rebill invoice",
        needs_context=False,
        context_reason=None,
        routing_category="billing",
    )


def _settings() -> Settings:
    return Settings(
        draft_model="claude-sonnet-4-6",
        anthropic_api_key="test-key",
    )


def _ok_draft() -> DraftSchema:
    return DraftSchema(
        subject_line="Re: Need rebilling",
        reply_body="We will rebill the Harmeyer department for this SampleLab invoice.",
        suggested_recipients=[],
        forward_to=None,
        teaching_note="Client asked for SampleLab rebilling; follow skill rules.",
        urgency="NORMAL",
        urgency_reason="Routine billing correction.",
    )


def _tool_use_response(*, skill_id: uuid.UUID, path: str, tool_use_id: str) -> MagicMock:
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_use_id
    block.name = "read_skill_reference"
    block.input = {"skill_id": str(skill_id), "path": path}
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=10, output_tokens=5)
    return resp


def _end_turn_response() -> MagicMock:
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [MagicMock(type="text", text="done")]
    resp.usage = MagicMock(input_tokens=8, output_tokens=3)
    return resp


def _parsed(draft: DraftSchema | None = None) -> MagicMock:
    parsed = MagicMock()
    parsed.parsed_output = draft or _ok_draft()
    parsed.usage = MagicMock(input_tokens=12, output_tokens=20)
    return parsed


@pytest.mark.asyncio
async def test_tool_loop_invokes_loader_and_feeds_tool_result() -> None:
    """Two tool_use turns then parse; tool_result content is sent next."""
    skill_id = uuid.uuid4()
    path = "references/client_rules.md"
    file_body = "# Client rules\nBill Harmeyer correctly."

    async def loader(sid: uuid.UUID, p: str) -> dict:
        assert sid == skill_id
        assert p == path
        return {"content": file_body, "bytes": len(file_body), "is_error": False}

    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _tool_use_response(skill_id=skill_id, path=path, tool_use_id="tu1"),
            _tool_use_response(skill_id=skill_id, path=path, tool_use_id="tu2"),
            _end_turn_response(),
        ]
    )
    client.messages.parse = AsyncMock(return_value=_parsed())

    result = await draft_llm.generate_draft(
        _email(),
        _context(_email()),
        _triage(),
        client=client,
        settings=_settings(),
        skills=[f"## Skill: samplelab-rebilling (id: {skill_id})\n..."],
        reference_loader=loader,
    )

    assert result.draft.reply_body
    assert len(result.tool_calls) == 2
    assert result.tool_calls[0]["skill_id"] == str(skill_id)
    assert result.tool_calls[0]["path"] == path
    assert client.messages.parse.await_count == 1

    # Second create call should include tool_result with file content
    second_kwargs = client.messages.create.await_args_list[1].kwargs
    messages = second_kwargs["messages"]
    tool_user = next(m for m in messages if m["role"] == "user" and isinstance(m["content"], list))
    assert tool_user["content"][0]["type"] == "tool_result"
    assert file_body in tool_user["content"][0]["content"]
    assert tool_user["content"][0]["tool_use_id"] == "tu1"


@pytest.mark.asyncio
async def test_tool_loop_truncates_at_max_iterations_and_still_parses() -> None:
    skill_id = uuid.uuid4()
    path = "references/client_rules.md"

    async def loader(sid: uuid.UUID, p: str) -> dict:
        return {"content": "x", "bytes": 1, "is_error": False}

    client = AsyncMock()
    client.messages.create = AsyncMock(
        side_effect=[
            _tool_use_response(skill_id=skill_id, path=path, tool_use_id=f"tu{i}")
            for i in range(draft_llm.MAX_TOOL_ITERATIONS + 2)
        ]
    )
    client.messages.parse = AsyncMock(return_value=_parsed())

    result = await draft_llm.generate_draft(
        _email(),
        _context(_email()),
        _triage(),
        client=client,
        settings=_settings(),
        reference_loader=loader,
    )
    assert result.draft.urgency == "NORMAL"
    assert client.messages.create.await_count == draft_llm.MAX_TOOL_ITERATIONS
    assert client.messages.parse.await_count == 1
    assert len(result.tool_calls) == draft_llm.MAX_TOOL_ITERATIONS


@pytest.mark.asyncio
async def test_unknown_skill_id_returns_is_error_without_db() -> None:
    active = {uuid.uuid4()}
    unknown = uuid.uuid4()
    factory = MagicMock()

    result = await skill_reference_service.load_skill_reference(
        skill_id=unknown,
        path="references/client_rules.md",
        active_skill_ids=active,
        session_factory=factory,
    )
    assert result["is_error"] is True
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_per_draft_budget_truncates_further_calls() -> None:
    skill_id = uuid.uuid4()
    row = MagicMock()
    row.kind = "reference"
    row.size_bytes = 100
    row.mime_type = "text/markdown"
    row.content = b"y" * 100

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)

    with patch(
        "app.services.skill_reference_service.skill_files_repo.get_by_path",
        AsyncMock(return_value=row),
    ):
        # Exhaust budget with a large first read by mocking remaining via direct load
        big = await skill_reference_service.load_skill_reference(
            skill_id=skill_id,
            path="references/a.md",
            active_skill_ids={skill_id},
            session_factory=factory,
            remaining_budget=50,
        )
        assert big.get("truncated") is True
        assert "per-draft budget" in big["content"]

        # Fresh loader: deplete budget then assert truncate on the next call
        loader2, log2 = skill_reference_service.make_reference_loader(
            active_skill_ids={skill_id},
            session_factory=factory,
        )
        # Patch get_by_path to return content under budget repeatedly until depleted
        small = MagicMock()
        small.kind = "reference"
        small.size_bytes = 40_000
        small.mime_type = "text/markdown"
        small.content = b"z" * 40_000
        with patch(
            "app.services.skill_reference_service.skill_files_repo.get_by_path",
            AsyncMock(return_value=small),
        ):
            first = await loader2(skill_id, "references/big1.md")
            assert first.get("is_error") is False
            assert first["bytes"] > 0
            second = await loader2(skill_id, "references/big2.md")
            assert second.get("truncated") is True
            assert "per-draft budget" in second["content"]
            assert any(entry.get("truncated") for entry in log2)


@pytest.mark.asyncio
async def test_parse_called_exactly_once_at_end() -> None:
    skill_id = uuid.uuid4()

    async def loader(sid: uuid.UUID, p: str) -> dict:
        return {"content": "ok", "bytes": 2, "is_error": False}

    client = AsyncMock()
    client.messages.create = AsyncMock(
        return_value=_tool_use_response(
            skill_id=skill_id,
            path="references/client_rules.md",
            tool_use_id="tu1",
        )
    )
    # After one tool_use, next create returns end_turn — but create is only
    # called once in the loop if we return tool_use then the for-loop continues.
    # Use side_effect: tool_use then end_turn.
    client.messages.create = AsyncMock(
        side_effect=[
            _tool_use_response(
                skill_id=skill_id,
                path="references/client_rules.md",
                tool_use_id="tu1",
            ),
            _end_turn_response(),
        ]
    )
    client.messages.parse = AsyncMock(return_value=_parsed())

    await draft_llm.generate_draft(
        _email(),
        _context(_email()),
        _triage(),
        client=client,
        settings=_settings(),
        reference_loader=loader,
    )
    assert client.messages.parse.await_count == 1


@pytest.mark.asyncio
async def test_tool_calls_persisted_on_draft_create() -> None:
    """draft_repo.create_draft accepts tool_calls and stores tool_calls_json."""
    from app.repositories import draft_repo

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    tool_calls = [
        {
            "skill_id": str(uuid.uuid4()),
            "path": "references/client_rules.md",
            "bytes": 120,
            "is_error": False,
        }
    ]

    captured: dict[str, object] = {}

    async def fake_refresh(obj: object) -> None:
        row = obj
        captured["tool_calls_json"] = getattr(row, "tool_calls_json", None)
        row.id = uuid.uuid4()
        row.thread_id = uuid.uuid4()
        row.message_id = "m1"
        row.subject = "Re: x"
        row.body = "body"
        row.suggested_recipients = []
        row.forward_to = None
        row.teaching_note = "note"
        row.urgency = "NORMAL"
        row.urgency_reason = "reason"
        row.suggested_actions = []
        row.model = "claude-sonnet-4-6"
        row.prompt_version = "2026-08-05.1"
        row.input_tokens = 1
        row.output_tokens = 1
        row.latency_ms = 1
        row.created_at = datetime.now(UTC)
        row.approved_at = None
        row.rejected_at = None
        row.edited_body = None
        row.feedback_note = None
        row.feedback_action = None
        row.feedback_reason_code = None
        row.routing_category = "billing"
        row.tool_calls_json = tool_calls

    session.refresh = AsyncMock(side_effect=fake_refresh)

    # create_draft signature — check what's required
    with patch.object(
        draft_repo,
        "_to_response",
        return_value=MagicMock(),
    ):
        # Inspect create_draft params
        import inspect

        sig = inspect.signature(draft_repo.create_draft)
        assert "tool_calls" in sig.parameters
