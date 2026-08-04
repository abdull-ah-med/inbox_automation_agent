"""Live end-to-end — OpenAI embeddings, similarity search, tone memory, full pipeline.

Opt-in (never runs in default CI). Uses **live** OpenAI + Anthropic + Postgres +
Redis. Graph is stubbed only for the cross-thread message fetch so the match is
deterministic (real Graph mailboxes are not required).

What this proves, in order:

1. Live ``text-embedding-3-small`` embed + store into ``email_embeddings``
2. pgvector cosine similarity finds a near-duplicate across conversation ids
3. Dissimilar text stays below the similarity threshold
4. Approved-reply tone memory store / retrieve / exclude
5. ``context_service.resolve_cross_thread_context`` (Flow B) with live embed+search
6. Full simulate-ingest → triage → draft → approve → reply embedding path

Run from backend/ (DB must be migrated, Redis up, keys in .env):

    unset OPENAI_API_KEY ANTHROPIC_API_KEY
    set -a && source .env && set +a
    .venv/bin/alembic upgrade head
    RUN_LIVE_EMBEDDING_E2E=1 .venv/bin/pytest tests/test_live_embedding_e2e.py -vv -s
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import anthropic_client_from_settings
from app.core.redis_keys import dedup_key
from app.db.session import dispose_engine
from app.models.db.email_embedding import EmailEmbedding
from app.models.db.reply_embedding import ReplyEmbedding
from app.models.schemas.draft import DraftSchema
from app.models.schemas.email import EmailDirectionEnum, EmailMessageSchema, ThreadContextSchema
from app.models.schemas.email_triage_state import CrossThreadContextSchema, EmailTriageState
from app.models.schemas.graph import (
    GraphEmailAddressSchema,
    GraphMessageBodySchema,
    GraphMessageSchema,
    GraphRecipientSchema,
    SimulateIngestRequestSchema,
)
from app.repositories import (
    draft_repo,
    embedding_repo,
    message_repo,
    reply_embedding_repo,
    thread_repo,
)
from app.services import (
    context_service,
    draft_feedback_service,
    embedding_service,
    ingestion_service,
    pipeline_service,
    reply_memory_service,
)
from tests.live_helpers import env_flag, print_divider, require_openai_settings, truncate

pytestmark = [
    pytest.mark.live_embedding,
    pytest.mark.live_claude,
    pytest.mark.live_e2e,
]

# Intentionally near-duplicate: cosine similarity should clear 0.78 easily.
_PRIOR_SUBJECT = "Drug screen results for driver John Martinez — need clearance"
_PRIOR_BODY = (
    "Hi Elise,\n\n"
    "Attached are the drug screen results for driver John Martinez (DOT).\n"
    "Please confirm clearance status and let us know if SampleLab needs a retest.\n"
    "Carrier: SampleSite Logistics. Collection date was Monday.\n\n"
    "Thanks,\nClinic Admin"
)
_FOLLOWUP_SUBJECT = "Following up — John Martinez drug screen clearance"
_FOLLOWUP_BODY = (
    "Hi Elise,\n\n"
    "Following up on our earlier email about the drug screen results for "
    "driver John Martinez. As discussed previously, we still need clearance "
    "confirmation and whether SampleLab requires a retest.\n\n"
    "Can you please advise on next steps?\n\n"
    "Thanks,\nClinic Admin"
)
_UNRELATED_SUBJECT = "Office pizza party next Friday"
_UNRELATED_BODY = (
    "Team lunch menu: pepperoni, veggie, and gluten-free options in the "
    "break room at noon. RSVP on the calendar invite."
)
_APPROVED_REPLY = (
    "Hi Clinic Admin,\n\n"
    "Thanks for sending John Martinez's drug screen results. I've reviewed "
    "them and will confirm clearance with Jordan. If SampleLab flags a retest "
    "we will notify you within one business day.\n\n"
    "Best regards,\nElise"
)


def _recipient(address: str) -> GraphRecipientSchema:
    return GraphRecipientSchema(email_address=GraphEmailAddressSchema(address=address))


def _graph_message(
    *,
    message_id: str,
    conversation_id: str,
    mailbox: str,
    sender: str,
    subject: str,
    body: str,
    received_at: datetime,
) -> GraphMessageSchema:
    return GraphMessageSchema(
        id=message_id,
        subject=subject,
        body_preview=body[:200],
        body=GraphMessageBodySchema(content_type="text", content=body),
        from_=_recipient(sender),
        sender=_recipient(sender),
        to_recipients=[_recipient(mailbox)],
        received_date_time=received_at,
        conversation_id=conversation_id,
        has_attachments=False,
    )


class _StubGraphClient:
    """Minimal Graph stand-in: only ``list_thread_messages`` for Flow B."""

    def __init__(self, threads: dict[str, list[GraphMessageSchema]]) -> None:
        self._threads = threads
        self.calls: list[tuple[str, str]] = []

    async def list_thread_messages(
        self,
        mailbox: str,
        conversation_id: str,
    ) -> list[GraphMessageSchema]:
        self.calls.append((mailbox, conversation_id))
        return list(self._threads.get(conversation_id, []))


@pytest.fixture
def live_settings():
    if not env_flag("RUN_LIVE_EMBEDDING_E2E"):
        pytest.skip("Set RUN_LIVE_EMBEDDING_E2E=1 to run the live embedding / tone E2E test")
    try:
        return require_openai_settings()
    except RuntimeError as exc:
        pytest.skip(str(exc))


@pytest.fixture
def live_mailbox(live_settings) -> str:
    return live_settings.mailbox_list[0]


def _email(
    *,
    message_id: str,
    conversation_id: str,
    mailbox: str,
    sender: str,
    subject: str,
    body: str,
    received_at: datetime,
) -> EmailMessageSchema:
    return EmailMessageSchema(
        message_id=message_id,
        conversation_id=conversation_id,
        mailbox=mailbox,
        sender=sender,
        subject=subject,
        body_text=body,
        body_preview=body[:200],
        received_at=received_at,
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=[mailbox],
        cc_recipients=[],
        has_attachments=False,
    )


@pytest.mark.asyncio
async def test_live_embedding_similarity_tone_and_pipeline(
    live_settings,
    live_mailbox: str,
) -> None:
    """Full live path: embed → search → tone memory → Flow B → pipeline → approve."""
    run_id = uuid.uuid4().hex[:12]
    mailbox = live_mailbox
    prior_conv = f"live-embed-prior-{run_id}"
    follow_conv = f"live-embed-follow-{run_id}"
    unrelated_conv = f"live-embed-unrelated-{run_id}"
    pipeline_conv = f"live-pipe-conv-{run_id}"
    prior_msg_id = f"live-prior-msg-{run_id}"
    follow_msg_id = f"live-follow-msg-{run_id}"
    unrelated_msg_id = f"live-unrelated-msg-{run_id}"
    now = datetime.now(UTC)

    await dispose_engine()
    engine = create_async_engine(live_settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    redis = Redis.from_url(
        live_settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=5.0,
    )
    openai = AsyncOpenAI(api_key=live_settings.openai_api_key.strip())
    anthropic = anthropic_client_from_settings(live_settings)

    prior_email = _email(
        message_id=prior_msg_id,
        conversation_id=prior_conv,
        mailbox=mailbox,
        sender="clinic@example.com",
        subject=_PRIOR_SUBJECT,
        body=_PRIOR_BODY,
        received_at=now - timedelta(days=2),
    )
    follow_email = _email(
        message_id=follow_msg_id,
        conversation_id=follow_conv,
        mailbox=mailbox,
        sender="clinic@example.com",
        subject=_FOLLOWUP_SUBJECT,
        body=_FOLLOWUP_BODY,
        received_at=now,
    )
    unrelated_email = _email(
        message_id=unrelated_msg_id,
        conversation_id=unrelated_conv,
        mailbox=mailbox,
        sender="hr@example.com",
        subject=_UNRELATED_SUBJECT,
        body=_UNRELATED_BODY,
        received_at=now - timedelta(hours=1),
    )

    stub_graph = _StubGraphClient(
        {
            prior_conv: [
                _graph_message(
                    message_id=prior_msg_id,
                    conversation_id=prior_conv,
                    mailbox=mailbox,
                    sender="clinic@example.com",
                    subject=_PRIOR_SUBJECT,
                    body=_PRIOR_BODY,
                    received_at=now - timedelta(days=2),
                )
            ]
        }
    )

    results: dict[str, Any] = {"run_id": run_id, "mailbox": mailbox}

    try:
        assert await redis.ping() is True
        async with session_factory() as session:
            await session.execute(select(1))
            # pgvector must be available for cosine search.
            await session.execute(text("SELECT 1"))

        print_divider("LIVE EMBEDDING E2E — START")
        print(f"  run_id:              {run_id}")
        print(f"  mailbox:             {mailbox}")
        print(f"  embedding_model:     {live_settings.embedding_model}")
        print(f"  min_similarity:      {live_settings.embedding_min_similarity}")
        print(f"  classification:      {live_settings.classification_model}")
        print(f"  draft_model:         {live_settings.draft_model}")
        print(f"  slack_enabled:       {live_settings.slack_enabled}")

        # Purge leftover fixtures from prior interrupted live runs.
        async with session_factory() as session, session.begin():
            await session.execute(
                delete(EmailEmbedding).where(
                    EmailEmbedding.mailbox == mailbox,
                    EmailEmbedding.conversation_id.like("live-embed-%"),
                )
            )
            await session.execute(
                delete(EmailEmbedding).where(
                    EmailEmbedding.mailbox == mailbox,
                    EmailEmbedding.conversation_id.like("live-pipe-%"),
                )
            )

        # ------------------------------------------------------------------
        # 1) Live OpenAI embed + store (prior + unrelated)
        # ------------------------------------------------------------------
        print_divider("STAGE 1 — Live OpenAI embed + store")
        prior_vector = await embedding_service.embed_email(
            prior_email,
            client=openai,
            settings=live_settings,
        )
        unrelated_vector = await embedding_service.embed_email(
            unrelated_email,
            client=openai,
            settings=live_settings,
        )
        assert len(prior_vector) == live_settings.embedding_dimension
        assert len(unrelated_vector) == live_settings.embedding_dimension
        print(f"  prior vector dims:     {len(prior_vector)}")
        print(f"  unrelated vector dims: {len(unrelated_vector)}")

        async with session_factory() as session, session.begin():
            # Persist threads/messages so embeddings can link to message PKs.
            prior_thread = await thread_repo.upsert_thread(
                session,
                mailbox=mailbox,
                conversation_id=prior_conv,
                subject=_PRIOR_SUBJECT,
                last_message_at=prior_email.received_at,
            )
            await message_repo.create_message(
                session,
                thread_id=prior_thread.id,
                graph_message_id=prior_msg_id,
                direction=EmailDirectionEnum.INBOUND.value,
                sender=prior_email.sender,
                body_text=prior_email.body_text,
                body_preview=prior_email.body_preview,
                received_at=prior_email.received_at,
                to_recipients=list(prior_email.to_recipients),
                cc_recipients=[],
            )
            unrelated_thread = await thread_repo.upsert_thread(
                session,
                mailbox=mailbox,
                conversation_id=unrelated_conv,
                subject=_UNRELATED_SUBJECT,
                last_message_at=unrelated_email.received_at,
            )
            await message_repo.create_message(
                session,
                thread_id=unrelated_thread.id,
                graph_message_id=unrelated_msg_id,
                direction=EmailDirectionEnum.INBOUND.value,
                sender=unrelated_email.sender,
                body_text=unrelated_email.body_text,
                body_preview=unrelated_email.body_preview,
                received_at=unrelated_email.received_at,
                to_recipients=list(unrelated_email.to_recipients),
                cc_recipients=[],
            )
            prior_stored = await embedding_service.store_email_embedding(
                session,
                email=prior_email,
                embedding=prior_vector,
            )
            unrelated_stored = await embedding_service.store_email_embedding(
                session,
                email=unrelated_email,
                embedding=unrelated_vector,
            )

        assert prior_stored.conversation_id == prior_conv
        assert unrelated_stored.conversation_id == unrelated_conv
        results["prior_embedding_id"] = str(prior_stored.id)
        print(f"  stored prior embedding:     {prior_stored.id}")
        print(f"  stored unrelated embedding: {unrelated_stored.id}")

        # ------------------------------------------------------------------
        # 2) Similarity search — follow-up should match prior, not unrelated
        # ------------------------------------------------------------------
        print_divider("STAGE 2 — Live similarity search")
        follow_vector = await embedding_service.embed_email(
            follow_email,
            client=openai,
            settings=live_settings,
        )
        async with session_factory() as session:
            matches = await embedding_repo.search_similar(
                session,
                embedding=follow_vector,
                min_similarity=live_settings.embedding_min_similarity,
                top_k=live_settings.embedding_candidate_k,
                mailbox=follow_email.mailbox,
                exclude_conversation_id=follow_conv,
            )
        print(f"  matches (>= {live_settings.embedding_min_similarity}): {len(matches)}")
        for m in matches:
            print(f"    conv={m.conversation_id} score={m.similarity_score:.4f} id={m.id}")

        assert matches, (
            "Expected at least one similarity match for the drug-screen follow-up "
            f"(min_similarity={live_settings.embedding_min_similarity})"
        )
        top = matches[0]
        assert top.conversation_id == prior_conv, (
            f"Top match should be prior drug-screen thread, got {top.conversation_id}"
        )
        assert top.similarity_score >= live_settings.embedding_min_similarity
        assert all(m.conversation_id != follow_conv for m in matches)
        # Unrelated pizza email should not outrank the drug-screen prior.
        if any(m.conversation_id == unrelated_conv for m in matches):
            unrelated_score = next(
                m.similarity_score for m in matches if m.conversation_id == unrelated_conv
            )
            assert unrelated_score < top.similarity_score
        results["top_similarity"] = top.similarity_score
        print(f"  PASS top match = prior thread @ {top.similarity_score:.4f}")

        # ------------------------------------------------------------------
        # 3) Tone memory — store / find / exclude
        # ------------------------------------------------------------------
        print_divider("STAGE 3 — Live reply tone memory")
        # Need a real draft row (FK). Create a minimal thread + draft.
        async with session_factory() as session, session.begin():
            tone_thread = await thread_repo.upsert_thread(
                session,
                mailbox=mailbox,
                conversation_id=f"live-tone-{run_id}",
                subject="Tone seed",
                last_message_at=now,
            )
            tone_draft = await draft_repo.create_draft(
                session,
                thread_id=tone_thread.id,
                message_id=f"live-tone-msg-{run_id}",
                draft=DraftSchema(
                    subject_line="Re: Drug screen",
                    reply_body=_APPROVED_REPLY,
                    teaching_note="Acknowledge and route clearance.",
                    urgency="NORMAL",
                    urgency_reason="Routine clearance follow-up",
                ),
                prompt_version="live-e2e",
            )
            tone_draft_id = tone_draft.id

        async with session_factory() as session:
            await reply_memory_service.store_approved_reply(
                session,
                openai_client=openai,
                settings=live_settings,
                draft_id=tone_draft_id,
                mailbox=mailbox,
                final_body=_APPROVED_REPLY,
                email_preview=_PRIOR_SUBJECT,
            )
            await session.commit()

        async with session_factory() as session:
            tone_hits = await reply_memory_service.find_similar_replies(
                session,
                openai_client=openai,
                settings=live_settings,
                email_text=f"{_FOLLOWUP_SUBJECT}\n\n{_FOLLOWUP_BODY}",
                mailbox=mailbox,
                limit=3,
            )
        print(f"  tone hits: {len(tone_hits)}")
        for i, hit in enumerate(tone_hits, start=1):
            print(f"    [{i}] {truncate(hit, limit=160)!r}")
        assert tone_hits, "Expected at least one similar approved reply"
        assert any("John Martinez" in hit or "drug screen" in hit.lower() for hit in tone_hits)
        results["tone_hits_before_exclude"] = len(tone_hits)

        async with session_factory() as session:
            rows = await reply_embedding_repo.list_reply_embeddings(
                session,
                mailbox=mailbox,
                limit=50,
            )
            seeded = next((r for r in rows if r.draft_id == tone_draft_id), None)
            assert seeded is not None
            updated = await reply_embedding_repo.set_excluded(
                session,
                seeded.id,
                is_excluded=True,
            )
            await session.commit()
        assert updated is not None and updated.is_excluded is True

        async with session_factory() as session:
            tone_after_exclude = await reply_memory_service.find_similar_replies(
                session,
                openai_client=openai,
                settings=live_settings,
                email_text=f"{_FOLLOWUP_SUBJECT}\n\n{_FOLLOWUP_BODY}",
                mailbox=mailbox,
                limit=3,
            )
        assert _APPROVED_REPLY not in tone_after_exclude, (
            "Excluded reply must not appear in tone references"
        )
        print("  PASS exclude removes reply from tone RAG")

        async with session_factory() as session:
            await reply_embedding_repo.set_excluded(
                session,
                seeded.id,
                is_excluded=False,
            )
            await session.commit()

        # ------------------------------------------------------------------
        # 4) Flow B — resolve_cross_thread_context (live embed+search, stub Graph)
        # ------------------------------------------------------------------
        print_divider("STAGE 4 — Flow B cross-thread context (live)")
        # Seed follow-up thread/message so store can link embedding to message PK.
        async with session_factory() as session, session.begin():
            follow_thread = await thread_repo.upsert_thread(
                session,
                mailbox=mailbox,
                conversation_id=follow_conv,
                subject=_FOLLOWUP_SUBJECT,
                last_message_at=follow_email.received_at,
            )
            await message_repo.create_message(
                session,
                thread_id=follow_thread.id,
                graph_message_id=follow_msg_id,
                direction=EmailDirectionEnum.INBOUND.value,
                sender=follow_email.sender,
                body_text=follow_email.body_text,
                body_preview=follow_email.body_preview,
                received_at=follow_email.received_at,
                to_recipients=list(follow_email.to_recipients),
                cc_recipients=[],
            )

        follow_state = EmailTriageState(
            original_email=follow_email,
            thread_context=ThreadContextSchema(
                conversation_id=follow_conv,
                mailbox=mailbox,
                subject=_FOLLOWUP_SUBJECT,
                messages=[follow_email],
            ),
            draft_status="PENDING",
        )
        cross = await context_service.resolve_cross_thread_context(
            follow_state,
            session_factory=session_factory,
            graph_client=stub_graph,  # type: ignore[arg-type]
            openai_client=openai,
            settings=live_settings,
        )
        assert cross is not None, "Flow B should return cross-thread context"
        assert isinstance(cross, CrossThreadContextSchema)
        assert cross.matched_conversation_id == prior_conv
        # conversation_score is RRF-fused (typically << cosine). Cosine gate is
        # already proven in STAGE 2; here we only require a positive fused score.
        assert cross.similarity_score > 0
        assert cross.thread_messages, "Matched prior thread messages must be loaded"
        # Graph fetch is optional when Postgres already has the prior messages.
        results["flow_b_similarity"] = cross.similarity_score
        results["flow_b_graph_fetched"] = bool(stub_graph.calls)
        print(
            f"  PASS Flow B matched {prior_conv} @ rrf_score={cross.similarity_score:.4f} "
            f"({len(cross.thread_messages)} msgs, graph_fetch={bool(stub_graph.calls)})"
        )

        # ------------------------------------------------------------------
        # 5) Full pipeline: simulate ingest → Haiku → Sonnet → approve
        # ------------------------------------------------------------------
        print_divider("STAGE 5 — Full pipeline (simulate + live LLMs)")
        pipeline_msg_id = f"live-pipe-msg-{run_id}"
        await redis.delete(dedup_key(mailbox, pipeline_msg_id))

        payload = SimulateIngestRequestSchema(
            mailbox=mailbox,
            message_id=pipeline_msg_id,
            conversation_id=pipeline_conv,
            sender="clinic@example.com",
            subject=_FOLLOWUP_SUBJECT,
            body_text=_FOLLOWUP_BODY,
            body_preview=_FOLLOWUP_BODY[:200],
            received_at=now,
            to_recipients=[mailbox],
            cc_recipients=[],
            has_attachments=False,
        )

        async with session_factory() as session, session.begin():
            ingest_result = await ingestion_service.ingest_simulated_message(
                session=session,
                redis=redis,
                payload=payload,
            )
        assert ingest_result.status in {"ingested", "retry_triage"}
        assert ingest_result.thread_id
        print(f"  ingest status: {ingest_result.status} thread_id={ingest_result.thread_id}")

        state = await pipeline_service.run_phased_after_ingest(
            redis=redis,
            settings=live_settings,
            ingest_result=ingest_result,
            client=anthropic,
            openai_client=openai,
            session_factory=session_factory,
            graph_client=stub_graph,  # type: ignore[arg-type]
            post_slack=False,
        )

        assert state.triage is not None, f"Haiku triage failed: {state.error_logs}"
        print(
            f"  triage: spam={state.triage.is_spam} "
            f"action={state.triage.has_action_items} "
            f"needs_context={state.triage.needs_context}"
        )
        print(f"  draft_status: {state.draft_status}")
        if state.draft is not None:
            print(f"  draft subject: {state.draft.subject_line!r}")
            print(f"  draft body:\n{truncate(state.draft.reply_body, limit=400)}")

        assert isinstance(state.triage.is_spam, bool)
        assert isinstance(state.triage.has_action_items, bool)
        assert isinstance(state.triage.needs_context, bool)

        # Non-spam emails must get an embedding row for the pipeline message.
        if not state.triage.is_spam:
            async with session_factory() as session:
                db_msg = await message_repo.get_by_graph_id(session, pipeline_msg_id)
                assert db_msg is not None
                emb_row = (
                    await session.execute(
                        select(EmailEmbedding).where(EmailEmbedding.message_id == db_msg.id)
                    )
                ).scalar_one_or_none()
            assert emb_row is not None, "Expected email embedding stored for non-spam"
            print(f"  PASS pipeline embedding stored id={emb_row.id}")

        if state.triage.has_action_items and not state.triage.is_spam:
            assert state.draft_status in {"DRAFTED", "REQUIRES_HUMAN"}
            if state.draft_status == "DRAFTED":
                assert state.draft is not None
                assert state.draft.reply_body.strip()
                assert state.draft.teaching_note.strip()
                # Approve → reply memory
                async with session_factory() as session:
                    draft_row = await draft_repo.get_draft_by_message(
                        session,
                        message_id=pipeline_msg_id,
                    )
                    assert draft_row is not None
                    approved = await draft_feedback_service.approve_draft(
                        session,
                        draft_row.id,
                        actor="live-embedding-e2e",
                        settings=live_settings,
                    )
                    await session.commit()
                    await draft_feedback_service.store_approved_reply_memory(
                        draft=approved,
                        settings=live_settings,
                        openai_client=openai,
                    )
                async with session_factory() as session:
                    reply_rows = await reply_embedding_repo.list_reply_embeddings(
                        session,
                        mailbox=mailbox,
                        limit=20,
                    )
                assert any(r.draft_id == approved.id for r in reply_rows), (
                    "Approved draft must create a reply_embeddings row"
                )
                print(f"  PASS approve → reply memory for draft {approved.id}")
                results["pipeline_draft_id"] = str(approved.id)
        else:
            assert state.draft_status == "SKIPPED"

        if pipeline_service.pipeline_ready_for_dedup(state):
            await ingestion_service.complete_ingest_dedup(redis, mailbox, pipeline_msg_id)
        else:
            await ingestion_service.release_ingest_dedup(redis, mailbox, pipeline_msg_id)

        results["pipeline_draft_status"] = state.draft_status
        results["pipeline_needs_context"] = state.triage.needs_context
        results["pipeline_cross_thread"] = (
            state.cross_thread_context.matched_conversation_id
            if state.cross_thread_context is not None
            else None
        )

        print_divider("LIVE EMBEDDING E2E — PASS")
        print(f"  results: {results}")

    finally:
        # Best-effort cleanup of this run's rows (leave DB usable for re-runs).
        try:
            async with session_factory() as session, session.begin():
                await session.execute(
                    delete(ReplyEmbedding).where(
                        ReplyEmbedding.mailbox == mailbox,
                        ReplyEmbedding.reply_text == _APPROVED_REPLY,
                    )
                )
                await session.execute(
                    delete(EmailEmbedding).where(
                        EmailEmbedding.conversation_id.in_(
                            [prior_conv, follow_conv, unrelated_conv, pipeline_conv]
                        )
                    )
                )
        except Exception as cleanup_exc:
            print(f"  cleanup warning: {cleanup_exc}")

        await openai.close()
        await anthropic.close()
        await redis.aclose()
        await engine.dispose()
        await dispose_engine()
