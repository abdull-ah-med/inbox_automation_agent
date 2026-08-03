"""Unit tests for same-thread / cross-thread prompt packing."""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm.context_pack import (
    anchor_first_and_newest,
    pack_cross_thread,
    pack_same_thread,
)
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema


def _msg(
    mid: str,
    *,
    body: str = "body",
    clean: str | None = None,
    summary: str | None = None,
    minutes: int = 0,
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=mid,
        conversation_id="c1",
        mailbox="elise@example.com",
        sender="a@example.com",
        subject="Subj",
        body_text=body,
        body_clean=clean if clean is not None else body,
        received_at=datetime(2026, 8, 1, 12, minutes, tzinfo=UTC),
        direction=EmailDirectionEnum.INBOUND,
        summary_one_line=summary,
    )


def test_pack_short_thread_uses_full_bodies() -> None:
    msgs = [_msg(f"m{i}", body=f"full-{i}") for i in range(3)]
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=msgs,
        ),
        full_if_at_most=5,
    )
    assert "full-0" in packed and "full-2" in packed
    assert "summary=" not in packed


def test_pack_long_thread_summarizes_older() -> None:
    msgs = [
        _msg(f"m{i}", body=f"body-{i}", summary=f"sum-{i}", minutes=i)
        for i in range(8)
    ]
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=msgs,
        ),
        current_message_id="m7",
        verbatim_tail=2,
        full_if_at_most=5,
    )
    assert "sum-0" in packed
    assert "body-7" in packed
    assert "body-6" in packed
    assert "body-5" in packed


def test_anchor_first_and_newest_keeps_starter() -> None:
    msgs = [_msg(f"m{i}", minutes=i) for i in range(10)]
    capped = anchor_first_and_newest(msgs, cap=4)
    assert capped[0].message_id == "m0"
    assert [m.message_id for m in capped[1:]] == ["m7", "m8", "m9"]


def test_anchor_under_cap_noop() -> None:
    msgs = [_msg(f"m{i}") for i in range(3)]
    assert anchor_first_and_newest(msgs, cap=5) == msgs


def test_pack_cross_thread_uses_summaries_and_newest_full() -> None:
    msgs = [
        _msg("old", body="old-full", summary="old-sum", minutes=0),
        _msg("new", body="new-full", summary="new-sum", minutes=1),
    ]
    packed = pack_cross_thread(
        CrossThreadContextSchema(
            matched_conversation_id="other",
            similarity_score=0.9,
            thread_messages=msgs,
        )
    )
    assert "old-sum" in packed
    assert "new-full" in packed
    assert "score=0.9000" in packed
