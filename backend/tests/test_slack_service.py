"""Tests for Slack review-card builder + idempotent poster."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.core.redis_keys import SLACK_POSTED_TTL_SECONDS, slack_posted_key
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.draft import DraftSchema, SuggestedRecipientSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import EmailTriageState
from app.services import slack_service
from app.services.slack_service import (
    ACTION_ID_APPROVE,
    ACTION_ID_EDIT,
    ACTION_ID_REJECT,
    build_review_card,
    post_review_card,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "slack_review_card_snapshot.json"
_GRAPH_WRITE_TOKENS = (
    "sendMail",
    "Mail.Send",
    "Mail.ReadWrite",
    "/send",
    "/reply",
    "/forward",
    "/move",
    "/copy",
)


def _email() -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id="AAMkAG-msg-001",
        conversation_id="AAQkAG-conv-001",
        mailbox="elise@sample-company.example.com",
        sender="vendor@example.com",
        subject="Need screening packet",
        body_text="Please send the drug screen packet for County X.",
        body_preview="Please send the drug screen packet for County X.",
        received_at=datetime(2026, 7, 24, 14, 0, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
    )


def _drafted_state(*, urgency: str = "HIGH") -> EmailTriageState:
    email = _email()
    return EmailTriageState(
        original_email=email,
        thread_context=ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage=TriageResultSchema(
            is_spam=False,
            has_action_items=True,
            action_items_summary="Vendor needs the screening packet for County X.",
            needs_context=False,
        ),
        draft=DraftSchema(
            subject_line="Re: Need screening packet",
            reply_body=(
                "Hi — attaching the screening packet. Let me know if you need anything else."
            ),
            suggested_recipients=[
                SuggestedRecipientSchema(role="vendor", rationale="Original sender")
            ],
            forward_to=None,
            teaching_note=("Vendor asked for the screening packet; reply with the file attached."),
            urgency=urgency,  # type: ignore[arg-type]
            urgency_reason="Client waiting on screening docs",
        ),
        draft_status="DRAFTED",
    )


def _configured_settings() -> Settings:
    return Settings(
        slack_enabled=True,
        slack_bot_token="xoxb-test-token",
        slack_signing_secret="signing-secret-test",
        slack_review_channel_id="C0123456789",
    )


def test_build_review_card_matches_snapshot() -> None:
    blocks = build_review_card(_drafted_state())
    expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert blocks == expected


def test_build_review_card_block_kit_contract() -> None:
    blocks = build_review_card(_drafted_state())
    assert [b["type"] for b in blocks] == [
        "header",
        "section",
        "section",
        "section",
        "section",
        "section",
        "actions",
    ]
    header = blocks[0]["text"]["text"]
    assert "HIGH" in header
    assert "elise@sample-company.example.com" in header
    assert "🟠" in header

    joined = json.dumps(blocks, ensure_ascii=False)
    assert "confidence" not in joined.lower()
    assert "Teaching note" in joined
    assert "Vendor asked for the screening packet" in joined
    assert "Thread summary" in joined
    assert "Open in Outlook" in joined
    assert "outlook.office365.com/owa/" in joined
    assert "Proposed action" in joined
    assert "Reply — suggested recipients: vendor" in joined

    action_ids = [el["action_id"] for el in blocks[-1]["elements"]]
    assert action_ids == [ACTION_ID_APPROVE, ACTION_ID_EDIT, ACTION_ID_REJECT]


def test_build_review_card_forward_action() -> None:
    state = _drafted_state()
    assert state.draft is not None
    state.draft = state.draft.model_copy(update={"forward_to": "ops@example.com"})
    blocks = build_review_card(state)
    proposed = blocks[3]["text"]["text"]
    assert "Forward to" in proposed
    assert "ops@example.com" in proposed


def test_build_review_card_requires_draft() -> None:
    state = _drafted_state()
    state.draft = None
    with pytest.raises(ValueError, match="requires state.draft"):
        build_review_card(state)


@pytest.mark.asyncio
async def test_post_review_card_unconfigured_returns_skipped() -> None:
    redis = AsyncMock()
    result = await post_review_card(
        _drafted_state(),
        redis=redis,
        slack_app=None,
        settings=Settings(),
    )
    assert result.status == "skipped_unconfigured"
    assert result.ok_for_dedup is True
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_review_card_posts_and_is_idempotent() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(side_effect=[True, False])
    redis.delete = AsyncMock()

    slack_app = MagicMock()
    slack_app.client.chat_postMessage = AsyncMock(return_value={"ts": "1710000000.000100"})

    state = _drafted_state()
    settings = _configured_settings()

    first = await post_review_card(state, redis=redis, slack_app=slack_app, settings=settings)
    second = await post_review_card(state, redis=redis, slack_app=slack_app, settings=settings)

    assert first.status == "posted"
    assert first.message_ts == "1710000000.000100"
    assert first.ok_for_dedup is True
    assert second.status == "already_posted"
    assert second.ok_for_dedup is True
    assert redis.set.await_count == 2
    lock_key = slack_posted_key(state.original_email.mailbox, state.original_email.message_id)
    redis.set.assert_any_await(lock_key, "1", nx=True, ex=SLACK_POSTED_TTL_SECONDS)
    assert slack_app.client.chat_postMessage.await_count == 1
    call_kwargs = slack_app.client.chat_postMessage.await_args.kwargs
    assert call_kwargs["channel"] == "C0123456789"
    assert len(call_kwargs["blocks"]) == 7
    redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_review_card_releases_lock_on_slack_error() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()

    slack_app = MagicMock()
    slack_app.client.chat_postMessage = AsyncMock(side_effect=RuntimeError("slack down"))

    result = await post_review_card(
        _drafted_state(),
        redis=redis,
        slack_app=slack_app,
        settings=_configured_settings(),
    )

    assert result.status == "failed"
    assert result.ok_for_dedup is False
    redis.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_review_card_skips_when_not_drafted() -> None:
    redis = AsyncMock()
    state = _drafted_state()
    state.draft_status = "REQUIRES_HUMAN"
    result = await post_review_card(
        state,
        redis=redis,
        slack_app=MagicMock(),
        settings=_configured_settings(),
    )
    assert result.status == "skipped_no_draft"
    assert result.ok_for_dedup is False
    redis.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_review_card_missing_ts_releases_lock_and_fails() -> None:
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()
    slack_app = MagicMock()
    slack_app.client.chat_postMessage = AsyncMock(return_value={"ok": True})

    result = await post_review_card(
        _drafted_state(),
        redis=redis,
        slack_app=slack_app,
        settings=_configured_settings(),
    )
    assert result.status == "failed"
    assert result.ok_for_dedup is False
    redis.delete.assert_awaited_once()


def test_build_review_card_summary_and_reply_fallbacks() -> None:
    state = _drafted_state()
    assert state.triage is not None
    state.triage = state.triage.model_copy(update={"action_items_summary": None})
    state.original_email = state.original_email.model_copy(
        update={"body_preview": None, "body_text": "  Short body text.  "}
    )
    assert state.draft is not None
    state.draft = state.draft.model_copy(update={"suggested_recipients": [], "forward_to": None})
    blocks = build_review_card(state)
    assert "Short body text." in blocks[1]["text"]["text"]
    assert "Open email in Outlook" in blocks[2]["text"]["text"]
    assert "Reply to sender" in blocks[3]["text"]["text"]


def test_slack_service_has_zero_graph_write_paths() -> None:
    """Grep-style AST scan: no Graph write tokens reachable from this module."""
    source_path = Path(slack_service.__file__).resolve()
    source = source_path.read_text(encoding="utf-8")
    lowered = source.lower()
    for token in _GRAPH_WRITE_TOKENS:
        assert token.lower() not in lowered, f"forbidden token {token!r} in slack_service"

    tree = ast.parse(source)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module.split(".")[0])
            for alias in node.names:
                imported_names.add(alias.name)
    assert "graph" not in imported_names
    assert "GraphClient" not in imported_names
