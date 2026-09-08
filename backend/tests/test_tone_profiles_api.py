"""Tone profiles API — mailbox allowlist on list."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.api_fixtures import make_local_settings

ALLOWED = "sales@example.com"


@pytest.fixture
def local_settings():
    return make_local_settings(target_mailboxes=ALLOWED)


@pytest.mark.asyncio
async def test_list_tone_profiles_disallowed_mailbox_returns_404(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/tone-profiles?mailbox=other@example.com")
    assert response.status_code == 404
    assert response.json()["detail"] == "Mailbox not found"


@pytest.mark.asyncio
async def test_list_tone_profiles_without_mailbox_scopes_to_allowed(app) -> None:
    with patch(
        "app.api.web.tone_profiles.tone_profile_service.list_profiles",
        AsyncMock(return_value=[]),
    ) as list_mock:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/tone-profiles")
    assert response.status_code == 200
    list_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs["mailboxes"] == [ALLOWED]
    assert list_mock.await_args.kwargs["mailbox"] is None
