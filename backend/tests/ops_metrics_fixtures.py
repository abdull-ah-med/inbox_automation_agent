"""Worked-example week for ops metric formula tests.

Hand-counted expected values (do not derive these from the SQL under test):

Window: 2026-08-03 00:00 UTC through 2026-08-09 23:59:59 UTC inclusive.
Mailboxes: sales@example.com, cr@example.com
Queue clock: 2026-08-12 16:00 UTC; stale after 24h (cutoff 2026-08-11 16:00 UTC).

Volume — distinct threads with ≥1 inbound in window, not message rows
  sales: T_multi, T_billing_a, T_billing_b, T_uncat → 4
  cr:    T_cr → 1
  total: 5
  excluded: T_outbound_only (outbound only), T_before (inbound before window)

Spam filtered — distinct (mailbox, conversation_id) discard events in window
  sales: T_spam_dup (two events, one thread) + T_no_action → 2
  cr:    T_cr_spam → 1
  total: 3
  excluded: T_spam_late (after window)
  T_filtered_spam is currently SPAM but has no in-window discard → queue only

Approvals / rejects — decision timestamp in window
  approved: T_billing_a, T_billing_b → 2
  rejected: T_multi (tone), T_uncat (tone), T_cr (null → other) → 3
  rate: 2 / 5 = 0.4
  excluded: T_late_approve (approved after window)

Reject themes: tone=2, other=1
Drafts generated (created_at in window): 5
  excluded: T_late_draft created after window

Resolve hours — sent_at in window and sent_at ≥ first inbound
  T_billing_a: 6.0h, T_billing_b: 24.0h → average 15.0, n=2
  excluded: T_uncat (sent before first inbound), T_multi (sent after window)

Categories of inbound-in-window threads
  billing=2, scheduling=1 (T_multi), client=1 (T_cr), uncategorized=1

Queue now (current state, not the period)
  awaiting: T_open_fresh, T_open_stale, T_cr → 3
  stale: T_open_stale only → 1
  filtered: T_filtered_spam → 1
  urgency on awaiting: critical=1, high=1, normal=1
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.db.audit_event import AuditEvent
from app.models.db.draft import Draft
from app.models.db.message import Message
from app.models.db.sent_reply import SentReply
from app.models.db.thread import Thread
from app.models.schemas.email import EmailDirectionEnum, ThreadStateEnum

SALES = "sales@example.com"
CR = "cr@example.com"
MAILBOXES = [SALES, CR]

WINDOW_START = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 9, 23, 59, 59, tzinfo=UTC)
IN_WINDOW = datetime(2026, 8, 5, 15, 0, tzinfo=UTC)
BEFORE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
AFTER = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
QUEUE_NOW = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
STALE_AFTER_HOURS = 24

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"
)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SALES_VOLUME = 4
EXPECTED_CR_VOLUME = 1
EXPECTED_TOTAL_VOLUME = 5
EXPECTED_SPAM = 3
EXPECTED_SALES_SPAM = 2
EXPECTED_APPROVALS = 2
EXPECTED_REJECTS = 3
EXPECTED_APPROVAL_RATE = 0.4
EXPECTED_DRAFTS_GENERATED = 5
EXPECTED_AVG_RESOLVE_HOURS = 15.0
EXPECTED_RESOLVE_SAMPLE = 2
EXPECTED_AWAITING = 3
EXPECTED_STALE = 1
EXPECTED_FILTERED = 1
EXPECTED_CRITICAL = 1
EXPECTED_HIGH = 1
EXPECTED_NORMAL = 1


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _is_unreachable(exc: BaseException) -> bool:
    text_exc = str(exc).lower()
    unreachable_types = (OSError, OperationalError, ConnectionRefusedError, TimeoutError)
    if isinstance(exc, unreachable_types):
        return True
    return any(
        token in text_exc
        for token in ("connection refused", "could not connect", "connect call failed", "timeout")
    )


def _quoted_db_name(url_str: str) -> str:
    dbname = make_url(url_str).database or ""
    if "test" not in dbname.lower():
        raise RuntimeError(
            f"Refusing to run formula tests against {dbname!r}; database name must contain 'test'"
        )
    return dbname.replace('"', "")


async def _recreate_test_database(url_str: str) -> None:
    url = make_url(url_str)
    dbname = _quoted_db_name(url_str)
    admin = url.set(database="postgres")
    engine = create_async_engine(
        admin.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await engine.dispose()


def _alembic_upgrade(url_str: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = url_str
    env["ENVIRONMENT"] = "local"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade failed for formula tests:\n{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session")
def migrated_test_database() -> Iterator[str]:
    """Create inbox_triage_test and apply Alembic. Skip only if Postgres is down."""
    url = test_database_url()
    try:
        import asyncio

        asyncio.run(_recreate_test_database(url))
        _alembic_upgrade(url)
    except Exception as exc:
        if _is_unreachable(exc):
            pytest.skip(f"Postgres is required for formula tests ({type(exc).__name__})")
        raise
    return url


@pytest.fixture
async def db_session(migrated_test_database: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_test_database, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
            await session.execute(
                text(
                    "TRUNCATE sent_replies, drafts, messages, audit_events, spam_allowlist, "
                    "thread_summaries, chat_response_cache, "
                    "alert_fingerprint_feedbacks, thread_association_reviews, "
                    "mailbox_contacts, "
                    "preference_pairs, feedback_atoms, teaching_notes, "
                    "urgency_predictions, urgency_rules, promotion_proposals, "
                    "golden_set_cases, "
                    "thread_context_facts, thread_contexts, "
                    "threads "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


def _thread(
    mailbox: str,
    conversation_id: str,
    *,
    state: str = ThreadStateEnum.NEW.value,
    urgency: str | None = None,
    category: str | None = None,
    last_message_at: datetime | None = None,
) -> Thread:
    return Thread(
        id=uuid.uuid4(),
        mailbox=mailbox,
        conversation_id=conversation_id,
        subject=conversation_id,
        state=state,
        urgency=urgency,
        category=category,
        last_message_at=last_message_at,
    )


def _message(
    thread: Thread,
    *,
    direction: str,
    received_at: datetime,
) -> Message:
    return Message(
        id=uuid.uuid4(),
        thread_id=thread.id,
        graph_message_id=str(uuid.uuid4()),
        direction=direction,
        sender="sender@example.com",
        body_text="body",
        received_at=received_at,
        to_recipients=[],
        cc_recipients=[],
    )


def _draft(
    thread: Thread,
    *,
    created_at: datetime,
    approved_at: datetime | None = None,
    rejected_at: datetime | None = None,
    reason: str | None = None,
) -> Draft:
    return Draft(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=str(uuid.uuid4()),
        subject=thread.subject,
        body="draft",
        recipients={},
        teaching_note="note",
        created_at=created_at,
        approved_at=approved_at,
        rejected_at=rejected_at,
        feedback_reason_code=reason,
    )


def _sent(thread: Thread, outbound: Message, sent_at: datetime) -> SentReply:
    return SentReply(
        id=uuid.uuid4(),
        thread_id=thread.id,
        message_id=outbound.id,
        sent_body_snapshot="sent",
        sent_at=sent_at,
        matched_by="internet_message_id",
    )


def _audit(
    mailbox: str,
    conversation_id: str,
    event_type: str,
    created_at: datetime,
) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        conversation_id=conversation_id,
        mailbox=mailbox,
        payload={},
        actor="system",
        created_at=created_at,
    )


async def seed_worked_example(session: AsyncSession) -> None:
    inbound = EmailDirectionEnum.INBOUND.value
    outbound = EmailDirectionEnum.OUTBOUND.value
    fresh = QUEUE_NOW - timedelta(hours=2)
    stale = QUEUE_NOW - timedelta(hours=48)

    t_multi = _thread(SALES, "t-multi", category="scheduling")
    t_outbound = _thread(SALES, "t-outbound-only")
    t_before = _thread(SALES, "t-before")
    t_billing_a = _thread(SALES, "t-billing-a", category="billing")
    t_billing_b = _thread(SALES, "t-billing-b", category="billing")
    t_uncat = _thread(SALES, "t-uncat")
    t_cr = _thread(
        CR,
        "t-cr",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="NORMAL",
        category="client",
        last_message_at=fresh,
    )
    t_spam_dup = _thread(SALES, "t-spam-dup")
    t_no_action = _thread(SALES, "t-no-action")
    t_cr_spam = _thread(CR, "t-cr-spam")
    t_spam_late = _thread(SALES, "t-spam-late")
    t_late_approve = _thread(SALES, "t-late-approve")
    t_late_draft = _thread(SALES, "t-late-draft")
    t_open_fresh = _thread(
        SALES,
        "t-open-fresh",
        state=ThreadStateEnum.DRAFTED.value,
        urgency="CRITICAL",
        last_message_at=fresh,
    )
    t_open_stale = _thread(
        SALES,
        "t-open-stale",
        state=ThreadStateEnum.REQUIRES_HUMAN.value,
        urgency="HIGH",
        last_message_at=stale,
    )
    t_filtered = _thread(
        SALES,
        "t-filtered-spam",
        state=ThreadStateEnum.SPAM.value,
        last_message_at=fresh,
    )
    t_resolved = _thread(
        SALES,
        "t-resolved",
        state=ThreadStateEnum.RESOLVED.value,
        last_message_at=fresh,
    )
    session.add_all(
        [
            t_multi,
            t_outbound,
            t_before,
            t_billing_a,
            t_billing_b,
            t_uncat,
            t_cr,
            t_spam_dup,
            t_no_action,
            t_cr_spam,
            t_spam_late,
            t_late_approve,
            t_late_draft,
            t_open_fresh,
            t_open_stale,
            t_filtered,
            t_resolved,
        ]
    )
    await session.flush()

    m_multi_1 = _message(t_multi, direction=inbound, received_at=IN_WINDOW)
    m_multi_2 = _message(t_multi, direction=inbound, received_at=IN_WINDOW + timedelta(hours=1))
    m_multi_3 = _message(t_multi, direction=inbound, received_at=IN_WINDOW + timedelta(hours=2))
    m_multi_out = _message(t_multi, direction=outbound, received_at=AFTER + timedelta(hours=1))
    m_outbound = _message(t_outbound, direction=outbound, received_at=IN_WINDOW)
    m_before = _message(t_before, direction=inbound, received_at=BEFORE)
    m_billing_a_in = _message(t_billing_a, direction=inbound, received_at=IN_WINDOW)
    m_billing_a_out = _message(
        t_billing_a, direction=outbound, received_at=IN_WINDOW + timedelta(hours=6)
    )
    m_billing_b_in = _message(t_billing_b, direction=inbound, received_at=IN_WINDOW)
    m_billing_b_out = _message(
        t_billing_b, direction=outbound, received_at=IN_WINDOW + timedelta(hours=24)
    )
    m_uncat_in = _message(t_uncat, direction=inbound, received_at=IN_WINDOW)
    m_uncat_out = _message(t_uncat, direction=outbound, received_at=IN_WINDOW - timedelta(hours=1))
    m_cr = _message(t_cr, direction=inbound, received_at=IN_WINDOW)
    session.add_all(
        [
            m_multi_1,
            m_multi_2,
            m_multi_3,
            m_multi_out,
            m_outbound,
            m_before,
            m_billing_a_in,
            m_billing_a_out,
            m_billing_b_in,
            m_billing_b_out,
            m_uncat_in,
            m_uncat_out,
            m_cr,
        ]
    )
    await session.flush()

    session.add_all(
        [
            _draft(
                t_multi,
                created_at=IN_WINDOW,
                rejected_at=IN_WINDOW,
                reason="tone",
            ),
            _draft(
                t_billing_a,
                created_at=IN_WINDOW,
                approved_at=IN_WINDOW,
            ),
            _draft(
                t_billing_b,
                created_at=IN_WINDOW,
                approved_at=IN_WINDOW,
            ),
            _draft(
                t_uncat,
                created_at=IN_WINDOW,
                rejected_at=IN_WINDOW,
                reason="tone",
            ),
            _draft(
                t_cr,
                created_at=IN_WINDOW,
                rejected_at=IN_WINDOW,
                reason=None,
            ),
            _draft(
                t_late_approve,
                created_at=BEFORE,
                approved_at=AFTER,
            ),
            _draft(t_late_draft, created_at=AFTER),
            _sent(t_billing_a, m_billing_a_out, IN_WINDOW + timedelta(hours=6)),
            _sent(t_billing_b, m_billing_b_out, IN_WINDOW + timedelta(hours=24)),
            _sent(t_uncat, m_uncat_out, IN_WINDOW - timedelta(hours=1)),
            _sent(t_multi, m_multi_out, AFTER + timedelta(hours=1)),
            _audit(SALES, "t-spam-dup", "triage.spam_discarded", IN_WINDOW),
            _audit(
                SALES,
                "t-spam-dup",
                "triage.spam_discarded",
                IN_WINDOW + timedelta(minutes=5),
            ),
            _audit(SALES, "t-no-action", "triage.no_action_discarded", IN_WINDOW),
            _audit(CR, "t-cr-spam", "triage.spam_discarded", IN_WINDOW),
            _audit(SALES, "t-spam-late", "triage.spam_discarded", AFTER),
        ]
    )
    await session.commit()
