"""Skills API route tests + skills injection into draft prompt."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import SkillNameConflictError, SkillNotFoundError
from app.llm.draft_generator import _build_user_content
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.classification import TriageResultSchema
from app.models.schemas.email import (
    EmailDirectionEnum,
    EmailMessageSchema,
    ThreadContextSchema,
)
from app.models.schemas.skill import SkillResponseSchema


@pytest.fixture
def local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes="sales@example.com,clientrelations@example.com",
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _skill(**overrides: object) -> SkillResponseSchema:
    base = {
        "id": uuid.uuid4(),
        "name": "Drug screen",
        "description": "Handle screens",
        "content": "Always CC Jordan on drug-screen emails",
        "category": "drug-screen",
        "is_active": True,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return SkillResponseSchema.model_validate(base)


@pytest.fixture
def app(local_settings: Settings):
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
                role="admin",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_skills_ok(app) -> None:
    skills = [_skill(), _skill(name="Vendor")]
    with patch(
        "app.api.web.skills.skill_repo.list_all",
        AsyncMock(return_value=skills),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/skills")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_create_skill_201(app) -> None:
    skill = _skill()
    with patch(
        "app.api.web.skills.skill_repo.create",
        AsyncMock(return_value=skill),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills",
                json={"name": "Drug screen", "content": "Always CC Jordan"},
            )
    assert resp.status_code == 201
    assert resp.json()["name"] == "Drug screen"


@pytest.mark.asyncio
async def test_create_duplicate_409(app) -> None:
    with patch(
        "app.api.web.skills.skill_repo.create",
        AsyncMock(side_effect=SkillNameConflictError("dup")),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills",
                json={"name": "Dup", "content": "x"},
            )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_missing_404(app) -> None:
    with patch(
        "app.api.web.skills.skill_repo.update_skill",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.put(
                f"/api/skills/{uuid.uuid4()}",
                json={"name": "Nope"},
            )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_404(app) -> None:
    with patch(
        "app.api.web.skills.skill_repo.delete_skill",
        AsyncMock(return_value=False),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/skills/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_skill_forbidden_for_non_admin(local_settings: Settings) -> None:
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
                role="user",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills",
                json={"name": "Nope", "content": "x"},
            )
        application.dependency_overrides.clear()
    assert resp.status_code == 403
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_skills_require_auth(local_settings: Settings) -> None:
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
            resp = await client.get("/api/skills")
        application.dependency_overrides.clear()
    assert resp.status_code == 401
    get_settings.cache_clear()


def test_skills_injected_into_user_content() -> None:
    email = EmailMessageSchema(
        message_id="m1",
        conversation_id="c1",
        mailbox="elise@example.com",
        subject="Drug screen",
        sender="vendor@example.com",
        body_text="Result attached",
        body_preview="Result attached",
        received_at=datetime.now(UTC),
        direction=EmailDirectionEnum.INBOUND,
        to_recipients=["elise@example.com"],
        cc_recipients=[],
    )
    triage = TriageResultSchema(
        is_spam=False,
        has_action_items=True,
        action_items_summary="Review screen",
        needs_context=False,
    )
    content = _build_user_content(
        email,
        ThreadContextSchema(
            conversation_id=email.conversation_id,
            mailbox=email.mailbox,
            subject=email.subject,
            messages=[email],
        ),
        triage,
        skills=[
            "Always CC Jordan on drug-screen emails",
            "Acknowledge within 24h",
        ],
    )
    assert "Standing instructions (skills):" in content
    assert "Always CC Jordan on drug-screen emails" in content
    assert "Acknowledge within 24h" in content


def test_skill_not_found_exception_exists() -> None:
    with pytest.raises(SkillNotFoundError):
        raise SkillNotFoundError("missing")
