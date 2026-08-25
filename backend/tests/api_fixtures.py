"""Shared FastAPI test app + settings fixtures."""

from __future__ import annotations

from collections.abc import Iterator
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.dependencies_auth import get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe


def make_local_settings(**overrides: object) -> Settings:
    """Build a Settings instance for local-environment API tests. Overrides win."""
    defaults: dict[str, object] = {
        "environment": "local",
        "jwt_secret": "c" * 64,
        "frontend_origin": "http://localhost:3000",
        "cookie_secure": False,
        "enable_dev_routes": False,
        "target_mailboxes": "sales@example.com,clientrelations@example.com",
        "database_url": "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        "redis_url": "redis://localhost:6379/15",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def local_settings() -> Settings:
    return make_local_settings()


@pytest.fixture
def api_user_role() -> str:
    return "user"


@pytest.fixture
def app(local_settings: Settings, api_user_role: str) -> Iterator[FastAPI]:
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

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.uuid4(),
                email="elise@example.com",
                role=api_user_role,
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()
