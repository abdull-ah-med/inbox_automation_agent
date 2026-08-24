"""Semantic chat cache: nearest neighbor above a hand-computed cosine floor."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.repositories import chat_cache_repo

DIM = 1536
MAILBOX = "sales@example.com"
USER = "reviewer-a"
THREAD_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
THREAD_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _unit(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index % DIM] = 1.0
    return vec


def _near_query() -> list[float]:
    """Unit vector with cosine 0.96 vs dim-0 axis: 0.96^2 + 0.28^2 = 1."""
    vec = [0.0] * DIM
    vec[0] = 0.96
    vec[1] = 0.28
    return vec


def _payload(answer: str) -> dict:
    return {
        "answer": answer,
        "citations": [],
        "retrieval_count": 1,
        "mailbox": MAILBOX,
        "refused_write": False,
    }


@pytest.mark.asyncio
async def test_find_semantic_hit_returns_closer_vector_above_threshold(db_session) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="what should i focus on",
        query_embedding=_near_query(),
        response_json=_payload("Focus on the overdue billing packet."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="unrelated weather chat",
        query_embedding=_unit(40),
        response_json=_payload("Wrong row."),
        citation_thread_ids=[THREAD_B],
        expires_at=expires,
    )
    await db_session.flush()

    query = _unit(0)
    # Hand cosine: q·near = 0.96; q·far = 0. Threshold 0.92 keeps only near.
    assert math.isclose(sum(a * b for a, b in zip(query, _near_query(), strict=True)), 0.96)
    hit = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=query,
        similarity_threshold=0.92,
    )
    assert hit is not None
    assert hit.response_json["answer"] == "Focus on the overdue billing packet."
    assert hit.similarity == pytest.approx(0.96, abs=0.001)

    miss = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=query,
        similarity_threshold=0.97,
    )
    assert miss is None


@pytest.mark.asyncio
async def test_find_semantic_hit_ignores_other_user_and_expired_rows(db_session) -> None:
    now = datetime.now(UTC)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key="other-user",
        query_normalized="what should i focus on",
        query_embedding=_near_query(),
        response_json=_payload("Other user."),
        citation_thread_ids=[THREAD_A],
        expires_at=now + timedelta(minutes=20),
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="what should i focus on",
        query_embedding=_near_query(),
        response_json=_payload("Expired."),
        citation_thread_ids=[THREAD_A],
        expires_at=now + timedelta(minutes=20),
    )
    await db_session.flush()
    await db_session.execute(
        text(
            "UPDATE chat_response_cache "
            "SET created_at = :created, expires_at = :expires "
            "WHERE user_key = :uk AND query_normalized = :q"
        ),
        {
            "created": now - timedelta(hours=2),
            "expires": now - timedelta(seconds=5),
            "uk": USER,
            "q": "what should i focus on",
        },
    )
    await db_session.flush()
    hit = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.5,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_invalidate_for_mailbox_deletes_matching_rows(db_session) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="focus",
        query_embedding=_unit(0),
        response_json=_payload("Sales."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key="cr@example.com",
        user_key=USER,
        query_normalized="focus",
        query_embedding=_unit(0),
        response_json=_payload("CR."),
        citation_thread_ids=[THREAD_B],
        expires_at=expires,
    )
    await db_session.flush()
    deleted = await chat_cache_repo.invalidate_for_mailbox(db_session, MAILBOX)
    await db_session.flush()
    assert deleted == 1
    remaining = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key="cr@example.com",
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.99,
    )
    assert remaining is not None
    assert remaining.response_json["answer"] == "CR."


@pytest.mark.asyncio
async def test_invalidate_for_mailbox_does_not_delete_empty_unscoped_bucket(
    db_session,
) -> None:
    """H9: invalidating sales@ must not sweep mailbox_key='' (shared bucket)."""
    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="focus",
        query_embedding=_unit(0),
        response_json=_payload("Sales."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key="",
        user_key=USER,
        query_normalized="focus",
        query_embedding=_unit(1),
        response_json=_payload("Unscoped."),
        citation_thread_ids=[THREAD_B],
        expires_at=expires,
    )
    await db_session.flush()
    deleted = await chat_cache_repo.invalidate_for_mailbox(db_session, MAILBOX)
    await db_session.flush()
    assert deleted == 1
    kept = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key="",
        user_key=USER,
        query_embedding=_unit(1),
        similarity_threshold=0.99,
    )
    assert kept is not None
    assert kept.response_json["answer"] == "Unscoped."


@pytest.mark.asyncio
async def test_invalidate_for_threads_deletes_citing_rows(db_session) -> None:
    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="billing",
        query_embedding=_unit(0),
        response_json=_payload("Cites A."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="other",
        query_embedding=_unit(3),
        response_json=_payload("Cites B."),
        citation_thread_ids=[THREAD_B],
        expires_at=expires,
    )
    await db_session.flush()
    deleted = await chat_cache_repo.invalidate_for_threads(db_session, [THREAD_A])
    await db_session.flush()
    assert deleted == 1
    kept = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(3),
        similarity_threshold=0.99,
    )
    assert kept is not None
    assert kept.response_json["answer"] == "Cites B."


@pytest.mark.asyncio
async def test_cache_invalidates_when_mailbox_ingests_new_mail(db_session) -> None:
    from app.services.pipeline_service import _invalidate_chat_cache_mailbox

    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="focus",
        query_embedding=_unit(0),
        response_json=_payload("Stale."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await db_session.flush()
    await _invalidate_chat_cache_mailbox(db_session, MAILBOX)
    await db_session.flush()
    hit = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.5,
    )
    assert hit is None


@pytest.mark.asyncio
async def test_find_semantic_hit_does_not_dirty_the_session(db_session) -> None:
    """H5: a cache lookup is a read. Updating hits on the SELECT path
    forces the request session into RW, takes a row lock, and makes a later
    rollback surprising. After find_semantic_hit the session must still be
    clean.
    """
    expires = datetime.now(UTC) + timedelta(minutes=20)
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="what should i focus on",
        query_embedding=_near_query(),
        response_json=_payload("Focus on the overdue billing packet."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await db_session.commit()

    hit = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.92,
    )
    assert hit is not None
    assert not db_session.dirty
    assert not db_session.new
    assert not db_session.deleted


@pytest.mark.asyncio
async def test_hit_count_incremented_out_of_band(db_session) -> None:
    """H5: two lookups still record two hits, but via record_semantic_hit
    rather than a write folded into the SELECT. Independent oracle: read
    the ``hits`` column directly from Postgres after both increments.
    """
    from sqlalchemy import text

    expires = datetime.now(UTC) + timedelta(minutes=20)
    cache_id = await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="what should i focus on",
        query_embedding=_near_query(),
        response_json=_payload("Focus on the overdue billing packet."),
        citation_thread_ids=[THREAD_A],
        expires_at=expires,
    )
    await db_session.commit()

    first = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.92,
    )
    second = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(0),
        similarity_threshold=0.92,
    )
    assert first is not None and second is not None
    await chat_cache_repo.record_semantic_hit(db_session, first.id)
    await chat_cache_repo.record_semantic_hit(db_session, second.id)
    await db_session.commit()

    hits = (
        await db_session.execute(
            text("SELECT hits FROM chat_response_cache WHERE id = :id"),
            {"id": cache_id},
        )
    ).scalar_one()
    assert hits == 2


@pytest.mark.asyncio
async def test_lookup_plan_uses_scope_index(db_session) -> None:
    """H11: mailbox_key + user_key lookup uses ix_chat_response_cache_mailbox_user."""
    from sqlalchemy import text

    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan_rows = (
        (
            await db_session.execute(
                text(
                    "EXPLAIN (FORMAT TEXT) "
                    "SELECT id FROM chat_response_cache "
                    "WHERE mailbox_key = :mb AND user_key = :uk"
                ),
                {"mb": MAILBOX, "uk": USER},
            )
        )
        .scalars()
        .all()
    )
    plan = "\n".join(plan_rows)
    assert "Seq Scan" not in plan, plan
    assert (
        "ix_chat_response_cache_mailbox_user" in plan
        or "uq_chat_response_cache_scope_query_hash" in plan
    ), plan


@pytest.mark.asyncio
async def test_expired_rows_purged_hard_within_ttl_grace(db_session) -> None:
    """H12: purge_expired deletes rows whose TTL already elapsed."""
    from sqlalchemy import func, select

    from app.models.db.chat_response_cache import ChatResponseCache

    now = datetime.now(UTC)
    expired_id = await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="expired billing ask",
        query_embedding=_near_query(),
        response_json=_payload("Expired answer."),
        citation_thread_ids=[THREAD_A],
        expires_at=now + timedelta(minutes=5),
    )
    await db_session.flush()
    await db_session.execute(
        text(
            "UPDATE chat_response_cache "
            "SET created_at = :created, expires_at = :expires "
            "WHERE id = :id"
        ),
        {
            "created": now - timedelta(hours=2),
            "expires": now - timedelta(hours=1),
            "id": expired_id,
        },
    )
    await chat_cache_repo.store(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_normalized="live billing ask",
        query_embedding=_unit(40),
        response_json=_payload("Live answer."),
        citation_thread_ids=[THREAD_B],
        expires_at=now + timedelta(minutes=5),
    )
    await db_session.flush()

    deleted = await chat_cache_repo.purge_expired(db_session)
    await db_session.flush()
    remaining = (
        await db_session.execute(select(func.count()).select_from(ChatResponseCache))
    ).scalar_one()
    live = await chat_cache_repo.find_semantic_hit(
        db_session,
        mailbox_key=MAILBOX,
        user_key=USER,
        query_embedding=_unit(40),
        similarity_threshold=0.5,
    )
    assert deleted == 1
    assert remaining == 1
    assert live is not None
    assert live.response_json["answer"] == "Live answer."
