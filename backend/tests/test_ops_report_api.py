"""Weekly report download API: auth, content-type, generate."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.main import create_app
from app.models.schemas.ops_report import OpsPeriod, OpsReportGenerateResponse


def _generate_result() -> OpsReportGenerateResponse:
    start = datetime(2026, 8, 3, tzinfo=UTC)
    end = datetime(2026, 8, 9, tzinfo=UTC)
    return OpsReportGenerateResponse(
        filename="ops-weekly-2026-08-03_2026-08-09.pdf",
        stored=False,
        period=OpsPeriod(date_from=start, date_to=end),
        generated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_download_requires_auth(local_settings: Settings) -> None:
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=local_settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: local_settings
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/reports/ops-weekly")
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_download_returns_pdf(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    pdf = b"%PDF-1.4 test-report"
    with patch(
        "app.api.web.reports.generate_ops_report",
        AsyncMock(return_value=(pdf, _generate_result())),
    ) as gen:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/reports/ops-weekly")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert "attachment" in resp.headers["content-disposition"]
    assert "ops-weekly-" in resp.headers["content-disposition"]
    assert resp.content == pdf
    kwargs = gen.await_args.kwargs
    assert kwargs["persist"] is False
    assert kwargs["send_email"] is False
    assert kwargs["generated_by"] == "elise@example.com"


@pytest.mark.asyncio
async def test_generate_returns_metadata(app) -> None:
    mock_session = AsyncMock()

    async def fake_db():
        yield mock_session

    app.dependency_overrides[get_db] = fake_db
    with patch(
        "app.api.web.reports.generate_ops_report",
        AsyncMock(return_value=(b"%PDF", _generate_result())),
    ) as gen:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/reports/ops-weekly/generate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"].endswith(".pdf")
    assert body["content_type"] == "application/pdf"
    assert gen.await_args.kwargs["persist"] is True
    assert gen.await_args.kwargs["send_email"] is False
    assert gen.await_args.kwargs["generated_by"] == "elise@example.com"
