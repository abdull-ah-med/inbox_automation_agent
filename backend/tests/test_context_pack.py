"""Unit tests for same-thread / cross-thread prompt packing."""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm.context_pack import (
    anchor_first_and_newest,
    pack_cross_thread,
    pack_same_thread,
)
from app.llm.prompts import wrap_untrusted
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
    msgs = [_msg(f"m{i}", body=f"body-{i}", summary=f"sum-{i}", minutes=i) for i in range(8)]
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


def _long_thread() -> list[EmailMessageSchema]:
    """Six-message worked example for Lost-in-the-Middle pack order."""
    specs: list[tuple[str, EmailDirectionEnum, str]] = [
        ("m0", EmailDirectionEnum.INBOUND, "FIRST-INBOUND-FULL-BODY"),
        ("m1", EmailDirectionEnum.INBOUND, "MIDDLE-INBOUND-ONE-LINER-UNIQUE"),
        ("m2", EmailDirectionEnum.OUTBOUND, "HER-OUTBOUND-FULL-BODY"),
        ("m3", EmailDirectionEnum.INBOUND, "OLDER-INBOUND-NOT-IN-WORKING-SET"),
        ("m4", EmailDirectionEnum.INBOUND, "TAIL-PREV-FULL-BODY"),
        ("m5", EmailDirectionEnum.INBOUND, "LAST-INBOUND-TAIL-FULL-BODY"),
    ]
    messages: list[EmailMessageSchema] = []
    for i, (mid, direction, body) in enumerate(specs):
        messages.append(
            EmailMessageSchema(
                message_id=mid,
                conversation_id="c1",
                mailbox="elise@example.com",
                sender="buyer@acme.com"
                if direction == EmailDirectionEnum.INBOUND
                else "elise@example.com",
                subject="Subj",
                body_text=body,
                body_clean=body,
                received_at=datetime(2026, 8, 1, 12, i, tzinfo=UTC),
                direction=direction,
                summary_one_line=f"sum-{mid}",
            )
        )
    return messages


def test_working_memory_pack_orders_pins_facts_first_inbound_outbound_tail() -> None:
    """Pins and facts at the start; original ask then her send; latest last."""
    pin = "Do not CC legal"
    fact_a = "Check 11111 is for driver Ames"
    fact_b = "Elise cancelled check 11111 in the portal"
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=_long_thread(),
        ),
        current_message_id="m5",
        verbatim_tail=2,
        full_if_at_most=5,
        user_notes=pin,
        facts=[
            {"text": fact_a, "source_message_id": "m0"},
            {"text": fact_b, "source_message_id": "m2"},
        ],
    )
    assert wrap_untrusted("untrusted_thread_pins", pin) in packed
    facts_block = wrap_untrusted(
        "untrusted_thread_facts",
        f"Thread facts:\n- [source=m0] {fact_a}\n- [source=m2] {fact_b}",
    )
    assert facts_block in packed
    assert fact_b in packed and "m2" in packed
    assert "FIRST-INBOUND-FULL-BODY" in packed
    assert "HER-OUTBOUND-FULL-BODY" in packed
    assert "LAST-INBOUND-TAIL-FULL-BODY" in packed
    assert packed.index(pin) < packed.index(fact_a) < packed.index(fact_b)
    assert packed.index(fact_b) < packed.index("FIRST-INBOUND-FULL-BODY")
    assert packed.index("FIRST-INBOUND-FULL-BODY") < packed.index("HER-OUTBOUND-FULL-BODY")
    assert packed.index("HER-OUTBOUND-FULL-BODY") < packed.index("LAST-INBOUND-TAIL-FULL-BODY")
    assert "MIDDLE-INBOUND-ONE-LINER-UNIQUE" not in packed
    assert "sum-m1" not in packed


def test_pack_without_pins_or_facts_keeps_one_liners_for_long_thread() -> None:
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=_long_thread(),
        ),
        current_message_id="m5",
        verbatim_tail=2,
        full_if_at_most=5,
    )
    assert "sum-m1" in packed
    assert "LAST-INBOUND-TAIL-FULL-BODY" in packed


def test_working_memory_pack_includes_recipient_prior_sends_after_facts() -> None:
    prior = "PRIOR-SEND-TO-ACME-PORTAL-FIX"
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=_long_thread(),
        ),
        user_notes="Do not CC legal",
        facts=[{"text": "Check 11111 cancelled", "source_message_id": "m2"}],
        prior_sends=[{"body": prior, "to": "buyer@acme.com"}],
    )
    assert prior in packed
    assert packed.index("Check 11111 cancelled") < packed.index(prior)
    assert packed.index(prior) < packed.index("FIRST-INBOUND-FULL-BODY")


def test_org_identity_omitted_when_empty() -> None:
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=_long_thread(),
        ),
        user_notes="Do not CC legal",
        facts=[{"text": "Check 11111 cancelled", "source_message_id": "m2"}],
        org_identity="",
    )
    assert "Org identity:" not in packed
    assert "SampleSite Support" not in packed


def test_org_identity_included_when_set() -> None:
    packed = pack_same_thread(
        ThreadContextSchema(
            conversation_id="c1",
            mailbox="elise@example.com",
            subject="S",
            messages=_long_thread(),
        ),
        user_notes="Do not CC legal",
        facts=[{"text": "Check 11111 cancelled", "source_message_id": "m2"}],
        org_identity="We are SampleSite Support, not SAMPLERECORDS.",
    )
    assert "We are SampleSite Support, not SAMPLERECORDS." in packed
    assert packed.index("We are SampleSite Support, not SAMPLERECORDS.") < packed.index(
        "Do not CC legal"
    )
