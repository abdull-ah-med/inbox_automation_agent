"""SQL shape tests for ops metrics aggregates (no live DB)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.repositories import ops_metrics_repo


class _EmptyResult:
    def all(self) -> list[object]:
        return []

    def one(self) -> object:
        class _Row:
            approvals = 0
            rejects = 0
            avg_hours = None
            sample_count = 0

        return _Row()

    def scalar_one(self) -> int:
        return 0


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, stmt: object) -> _EmptyResult:
        self.statements.append(stmt)
        return _EmptyResult()


def _compiled(stmt: object) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


START = datetime(2026, 8, 3, tzinfo=UTC)
END = datetime(2026, 8, 9, 23, 59, 59, tzinfo=UTC)
MAILBOXES = ["sales@example.com"]


@pytest.mark.asyncio
async def test_volume_sql_uses_inbound_received_at() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.volume_by_mailbox(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "messages.direction" in sql
    assert "inbound" in sql
    assert "messages.received_at" in sql
    assert "sales@example.com" in sql


@pytest.mark.asyncio
async def test_spam_sql_uses_audit_discard_events() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.count_spam_filtered(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "triage.spam_discarded" in sql
    assert "triage.no_action_discarded" in sql
    assert "audit_events.created_at" in sql


@pytest.mark.asyncio
async def test_approval_sql_uses_approved_and_rejected_at() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.count_approvals_rejects(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "drafts.approved_at" in sql
    assert "drafts.rejected_at" in sql


@pytest.mark.asyncio
async def test_reject_themes_sql_groups_reason_code() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.top_reject_themes(session, MAILBOXES, START, END, limit=5)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "feedback_reason_code" in sql
    assert "drafts.rejected_at" in sql
    assert "LIMIT 5" in sql.upper() or "LIMIT :param" in sql.upper() or "5" in sql


@pytest.mark.asyncio
async def test_resolve_sql_uses_first_inbound_and_sent_at() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.avg_resolve_hours(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "sent_replies.sent_at" in sql
    assert "inbound" in sql
    assert "received_at" in sql


@pytest.mark.asyncio
async def test_drafts_generated_sql_uses_created_at() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.count_drafts_generated(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "drafts.created_at" in sql


@pytest.mark.asyncio
async def test_category_sql_groups_thread_category() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.volume_by_category(session, MAILBOXES, START, END)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "threads.category" in sql
    assert "inbound" in sql


@pytest.mark.asyncio
async def test_queue_sql_uses_awaiting_and_stale() -> None:
    session = _CapturingSession()
    await ops_metrics_repo.queue_snapshot(session, MAILBOXES, stale_after_hours=24)  # type: ignore[arg-type]
    sql = _compiled(session.statements[0])
    assert "DRAFTED" in sql
    assert "last_message_at" in sql
    assert "CRITICAL" in sql


@pytest.mark.asyncio
async def test_empty_mailbox_list_short_circuits() -> None:
    session = _CapturingSession()
    volume = await ops_metrics_repo.volume_by_mailbox(session, [], START, END)  # type: ignore[arg-type]
    spam = await ops_metrics_repo.count_spam_filtered(session, [], START, END)  # type: ignore[arg-type]
    approvals, rejects = await ops_metrics_repo.count_approvals_rejects(
        session,
        [],
        START,
        END,  # type: ignore[arg-type]
    )
    themes = await ops_metrics_repo.top_reject_themes(session, [], START, END)  # type: ignore[arg-type]
    avg, n = await ops_metrics_repo.avg_resolve_hours(session, [], START, END)  # type: ignore[arg-type]
    drafts = await ops_metrics_repo.count_drafts_generated(session, [], START, END)  # type: ignore[arg-type]
    categories = await ops_metrics_repo.volume_by_category(session, [], START, END)  # type: ignore[arg-type]
    queue, by_mailbox = await ops_metrics_repo.queue_snapshot(session, [])  # type: ignore[arg-type]
    assert volume == []
    assert spam == 0
    assert (approvals, rejects) == (0, 0)
    assert themes == []
    assert avg is None and n == 0
    assert drafts == 0
    assert categories == []
    assert queue.awaiting_action == 0
    assert by_mailbox == {}
    assert session.statements == []
