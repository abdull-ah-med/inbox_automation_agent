"""Weekly ops report job: isolation from Graph, email off by default."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.models.schemas.ops_report import OpsPeriod, OpsReportGenerateResponse
from app.services.ops_report_job import maybe_send_email
from app.workers import ops_report_worker
from app.workers.ops_report_worker import run_weekly_ops_report


def _result() -> OpsReportGenerateResponse:
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, tzinfo=UTC)
    return OpsReportGenerateResponse(
        filename="ops-weekly-2026-08-03_2026-08-09.pdf",
        stored=True,
        period=OpsPeriod(date_from=start, date_to=end),
        generated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_weekly_job_generates_without_graph_send() -> None:
    settings = Settings(
        environment="local",
        target_mailboxes="sales@example.com",
        ops_report_email_enabled=False,
        ops_report_timezone="America/New_York",
    )
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    graph_send = MagicMock()

    with (
        patch("app.workers.ops_report_worker.get_settings", return_value=settings),
        patch(
            "app.workers.ops_report_worker.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "app.workers.ops_report_worker.generate_ops_report",
            AsyncMock(return_value=(b"%PDF", _result())),
        ) as gen,
        patch("app.graph.client.GraphClient.send_mail", graph_send, create=True),
        patch("app.workers.ops_report_worker.ops_metrics_service.last_calendar_week") as week,
    ):
        week.return_value = (
            datetime(2026, 8, 3, tzinfo=UTC),
            datetime(2026, 8, 10, tzinfo=UTC),
        )
        await run_weekly_ops_report()

    gen.assert_awaited_once()
    kwargs = gen.await_args.kwargs
    assert kwargs["persist"] is True
    assert kwargs["send_email"] is False
    graph_send.assert_not_called()


@pytest.mark.asyncio
async def test_weekly_job_swallows_errors() -> None:
    settings = Settings(environment="local", target_mailboxes="sales@example.com")
    session = AsyncMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.workers.ops_report_worker.get_settings", return_value=settings),
        patch(
            "app.workers.ops_report_worker.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "app.workers.ops_report_worker.generate_ops_report",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch("app.workers.ops_report_worker.logger.exception") as log_exc,
    ):
        await run_weekly_ops_report()

    log_exc.assert_called_once()
    assert log_exc.call_args.args[0] == "ops_report_job_failed"


@pytest.mark.asyncio
async def test_email_disabled_by_default() -> None:
    settings = Settings(
        environment="local",
        ops_report_email_enabled=False,
        ops_report_smtp_host="smtp.example.com",
        ops_report_smtp_to="ops@example.com",
    )
    from app.models.schemas.ops_report import MailboxVolume, OpsMetricsResponse

    metrics = OpsMetricsResponse(
        period=OpsPeriod(
            date_from=datetime(2026, 8, 3, tzinfo=UTC),
            date_to=datetime(2026, 8, 9, tzinfo=UTC),
        ),
        volume_by_mailbox=[
            MailboxVolume(mailbox="sales", email="sales@example.com", thread_volume=1)
        ],
        total_volume=1,
        spam_filtered=0,
        approvals=1,
        rejects=0,
        approval_rate=1.0,
        top_reject_themes=[],
        avg_resolve_hours=None,
        resolve_sample_count=0,
        generated_at=datetime.now(UTC),
    )
    with patch("app.services.ops_report_job._send_smtp") as send:
        sent = await maybe_send_email(settings, metrics, b"%PDF", "ops-weekly.pdf")
    assert sent is False
    send.assert_not_called()


@pytest.mark.asyncio
async def test_email_skips_when_smtp_host_missing() -> None:
    settings = Settings(
        environment="local",
        ops_report_email_enabled=True,
        ops_report_smtp_host="",
        ops_report_smtp_to="ops@example.com",
    )
    from app.models.schemas.ops_report import OpsMetricsResponse

    metrics = OpsMetricsResponse(
        period=OpsPeriod(
            date_from=datetime(2026, 8, 3, tzinfo=UTC),
            date_to=datetime(2026, 8, 9, tzinfo=UTC),
        ),
        volume_by_mailbox=[],
        total_volume=0,
        spam_filtered=0,
        approvals=0,
        rejects=0,
        approval_rate=0.0,
        top_reject_themes=[],
        avg_resolve_hours=None,
        resolve_sample_count=0,
        generated_at=datetime.now(UTC),
    )
    with patch("app.services.ops_report_job._send_smtp") as send:
        sent = await maybe_send_email(settings, metrics, b"%PDF", "ops-weekly.pdf")
    assert sent is False
    send.assert_not_called()


def test_worker_module_has_no_graph_send() -> None:
    source = inspect.getsource(ops_report_worker)
    assert "sendMail" not in source
    assert "send_mail" not in source
    assert "app.graph" not in source


def test_ops_report_email_default_is_false() -> None:
    settings = Settings(environment="local")
    assert settings.ops_report_email_enabled is False
    assert settings.ops_report_smtp_host == ""


@pytest.mark.asyncio
async def test_email_sends_via_smtp_never_graph() -> None:
    settings = Settings(
        environment="local",
        ops_report_email_enabled=True,
        ops_report_smtp_host="smtp.example.com",
        ops_report_smtp_to="ops@example.com",
        ops_report_smtp_from="reports@example.com",
    )
    from app.models.schemas.ops_report import MailboxVolume, OpsMetricsResponse

    metrics = OpsMetricsResponse(
        period=OpsPeriod(
            date_from=datetime(2026, 8, 3, tzinfo=UTC),
            date_to=datetime(2026, 8, 9, tzinfo=UTC),
        ),
        volume_by_mailbox=[
            MailboxVolume(mailbox="sales", email="sales@example.com", thread_volume=1)
        ],
        total_volume=1,
        spam_filtered=0,
        approvals=1,
        rejects=0,
        approval_rate=1.0,
        top_reject_themes=[],
        avg_resolve_hours=None,
        resolve_sample_count=0,
        generated_at=datetime.now(UTC),
    )
    graph_send = MagicMock()
    with (
        patch("app.services.ops_report_job._send_smtp") as send,
        patch("app.graph.client.GraphClient.send_mail", graph_send, create=True),
    ):
        sent = await maybe_send_email(settings, metrics, b"%PDF", "ops-weekly.pdf")
    assert sent is True
    send.assert_called_once()
    graph_send.assert_not_called()


def test_smtp_starttls_uses_default_ssl_context() -> None:
    """SPEC: STARTTLS must verify certs (ssl.create_default_context → CERT_REQUIRED)."""
    import ssl
    from unittest.mock import MagicMock, patch

    from app.models.schemas.ops_report import OpsMetricsResponse
    from app.services.ops_report_job import _send_smtp

    settings = Settings(
        environment="local",
        ops_report_email_enabled=True,
        ops_report_smtp_host="smtp.example.com",
        ops_report_smtp_port=587,
        ops_report_smtp_to="ops@example.com",
        ops_report_smtp_from="reports@example.com",
    )
    metrics = OpsMetricsResponse(
        period=OpsPeriod(
            date_from=datetime(2026, 8, 3, tzinfo=UTC),
            date_to=datetime(2026, 8, 9, tzinfo=UTC),
        ),
        volume_by_mailbox=[],
        total_volume=0,
        spam_filtered=0,
        approvals=0,
        rejects=0,
        approval_rate=0.0,
        top_reject_themes=[],
        avg_resolve_hours=None,
        resolve_sample_count=0,
        generated_at=datetime.now(UTC),
    )
    smtp = MagicMock()
    smtp_cm = MagicMock()
    smtp_cm.__enter__.return_value = smtp
    smtp_cm.__exit__.return_value = False
    ctx = ssl.create_default_context()

    with (
        patch("app.services.ops_report_job.smtplib.SMTP", return_value=smtp_cm) as smtp_ctor,
        patch(
            "app.services.ops_report_job.ssl.create_default_context", return_value=ctx
        ) as create_ctx,
    ):
        _send_smtp(settings, metrics, b"%PDF", "ops-weekly.pdf")

    smtp_ctor.assert_called_once()
    create_ctx.assert_called_once()
    smtp.starttls.assert_called_once()
    kwargs = smtp.starttls.call_args.kwargs
    assert "context" in kwargs
    assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
