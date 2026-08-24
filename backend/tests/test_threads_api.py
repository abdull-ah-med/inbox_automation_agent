"""HTTP contract for the /api/threads/* router — import/hint-eval safety.

Regression guard for B1: a missing schema import in threads.py breaks
FastAPI's `get_type_hints()` resolution for the whole router at include time,
so ANY route under /api/threads/* 500s, not just regenerate-draft. This test
proves the router loads and a real request against an unknown thread id
resolves through normal business logic (404), not an import-time crash (500).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_anthropic_client, get_db, get_openai_client, get_redis
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe

UNKNOWN_THREAD_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="elise@sample-site.example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


@pytest.mark.db
@pytest.mark.asyncio
async def test_regenerate_draft_route_is_loadable(local_settings: Settings, db_session) -> None:
    """Constructs the real app (no mock of regenerate_draft) and hits an
    unknown thread id. If RegenerateDraftSchema were unimported, FastAPI
    would fail to resolve the route's type hints when the app is created,
    or the request would 500 with an import/NameError rather than reach
    draft_regeneration_service's real ThreadNotFoundError -> 404 mapping.
    """
    get_settings.cache_clear()
    application = create_app()
    application.dependency_overrides[get_settings] = lambda: local_settings

    async def fake_user() -> UserMe:
        return UserMe(
            id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            email="elise@sample-site.example.com",
            role="user",
            created_at=datetime.now(UTC),
        )

    application.dependency_overrides[get_current_user] = fake_user
    application.dependency_overrides[get_db] = lambda: db_session
    application.dependency_overrides[get_redis] = lambda: None
    application.dependency_overrides[get_anthropic_client] = lambda: None
    application.dependency_overrides[get_openai_client] = lambda: None

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            f"/api/threads/{UNKNOWN_THREAD_ID}/regenerate-draft",
            json={"instruction": "Make it shorter"},
        )

    application.dependency_overrides.clear()
    get_settings.cache_clear()

    assert resp.status_code == 404, resp.text
