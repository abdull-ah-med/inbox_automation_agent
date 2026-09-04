"""Teaching notes API tests.

Oracle: API contract — create returns 201 + body; list 200;
PATCH with wider scope creates a proposal row not a direct widen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_admin, get_current_user
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.repositories.teaching_note_repo import TeachingNoteSchema

MAILBOX = "sales@example.com"


def _local_settings() -> Settings:
    return Settings(
        environment="local",
        jwt_secret="c" * 64,
        frontend_origin="http://localhost:3000",
        cookie_secure=False,
        enable_dev_routes=False,
        target_mailboxes=MAILBOX,
        database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test",
        redis_url="redis://localhost:6379/15",
    )


def _note(**overrides) -> TeachingNoteSchema:
    base = {
        "id": uuid.uuid4(),
        "mailbox": MAILBOX,
        "title": "Ack driver",
        "body": "Always acknowledge the driver by name",
        "applies_when": None,
        "scope": "mailbox",
        "scope_key": MAILBOX,
        "status": "active",
        "origin": "manual",
        "origin_atom_id": None,
        "person_bound": False,
        "hit_count": 0,
        "precision_num": 0,
        "precision_den": 0,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    base.update(overrides)
    return TeachingNoteSchema.model_validate(base)


@pytest.fixture
def app():
    settings = _local_settings()
    get_settings.cache_clear()
    with (
        patch("app.main.get_settings", return_value=settings),
        patch("app.main._ping_redis", AsyncMock()),
        patch("app.main.get_slack_app", return_value=None),
        patch("app.main.run_subscription_reconcile", AsyncMock()),
        patch("app.main.AsyncIOScheduler") as sched,
    ):
        sched.return_value.start = lambda: None
        sched.return_value.shutdown = lambda wait=False: None
        application = create_app()
        application.dependency_overrides[get_settings] = lambda: settings

        async def fake_user() -> UserMe:
            return UserMe(
                id=uuid.uuid4(),
                email="elise@example.com",
                role="admin",
                created_at=datetime.now(UTC),
            )

        application.dependency_overrides[get_current_user] = fake_user
        application.dependency_overrides[get_current_admin] = fake_user

        mock_session = MagicMock()
        mock_session.commit = AsyncMock()

        async def fake_db():
            yield mock_session

        application.dependency_overrides[get_db] = fake_db
        yield application
        application.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_create_teaching_note_returns_201(app) -> None:
    note = _note(scope_key=f"mailbox:{MAILBOX}")
    with patch(
        "app.api.web.teaching_notes.teaching_note_repo.create_teaching_note",
        AsyncMock(return_value=note),
    ) as mock_create:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/teaching-notes",
                json={
                    "mailbox": MAILBOX,
                    "title": "Ack driver",
                    "body": "Always acknowledge the driver by name",
                    "scope": "mailbox",
                },
            )
    assert resp.status_code == 201
    body = resp.json()
    assert body["mailbox"] == MAILBOX
    assert body["scope"] == "mailbox"
    assert body["scope_key"] == f"mailbox:{MAILBOX}"
    assert mock_create.await_args.kwargs["scope_key"] == f"mailbox:{MAILBOX}"


@pytest.mark.asyncio
async def test_create_global_scope_returns_400(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/teaching-notes",
            json={
                "mailbox": MAILBOX,
                "title": "Org-wide",
                "body": "Never go global from create",
                "scope": "global",
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_missing_mailbox_and_scope_returns_422(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/teaching-notes",
            json={
                "title": "Ack driver",
                "body": "Always acknowledge the driver by name",
                "scope_key": "mailbox",
            },
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_teaching_notes_requires_mailbox(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/teaching-notes")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_teaching_notes_unknown_mailbox_returns_404(app) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/teaching-notes?mailbox=other@example.com")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_teaching_notes_returns_200(app) -> None:
    notes = [_note(), _note(id=uuid.uuid4(), title="Another")]
    with patch(
        "app.api.web.teaching_notes.teaching_note_repo.list_teaching_notes",
        AsyncMock(return_value=notes),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/teaching-notes?mailbox={MAILBOX}")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_patch_wider_scope_creates_proposal_and_applies_body(app) -> None:
    """Wider scope creates a proposal; body from the same PATCH is still applied."""
    note_id = uuid.uuid4()
    proposal_id = uuid.uuid4()
    existing = _note(id=note_id, scope="sender_address", scope_key="sender:ops@statuspage.io")
    updated = _note(
        id=note_id,
        scope="sender_address",
        body="Lead with the invoice number",
        title="Invoice first",
    )

    with (
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.get_teaching_note_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.api.web.teaching_notes.promotion_proposal_repo.create_promotion_proposal",
            AsyncMock(return_value=SimpleNamespace(id=proposal_id)),
        ) as mock_proposal,
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.update_teaching_note",
            AsyncMock(return_value=updated),
        ) as mock_update,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/teaching-notes/{note_id}",
                json={
                    "scope": "mailbox",
                    "body": "Lead with the invoice number",
                    "title": "Invoice first",
                },
            )

    assert resp.status_code == 202
    assert resp.json()["proposal_id"] == str(proposal_id)
    mock_proposal.assert_awaited_once()
    mock_update.assert_awaited_once()
    assert mock_update.await_args.kwargs["body"] == "Lead with the invoice number"
    assert mock_update.await_args.kwargs["title"] == "Invoice first"
    assert resp.json()["scope"] == "sender_address"
    assert resp.json()["body"] == "Lead with the invoice number"


@pytest.mark.asyncio
async def test_patch_global_scope_returns_400(app) -> None:
    note_id = uuid.uuid4()
    existing = _note(id=note_id, scope="mailbox")
    with patch(
        "app.api.web.teaching_notes.teaching_note_repo.get_teaching_note_by_id",
        AsyncMock(return_value=existing),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/teaching-notes/{note_id}",
                json={"scope": "global"},
            )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_archive_note_returns_204(app) -> None:
    note_id = uuid.uuid4()
    existing = _note(id=note_id)
    with (
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.get_teaching_note_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.update_teaching_note_status",
            AsyncMock(return_value=_note(id=note_id, status="archived")),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/api/teaching-notes/{note_id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_patch_narrower_scope_updates_in_place(app) -> None:
    """Narrowing mailbox → sender_domain applies immediately when the domain is provided."""
    note_id = uuid.uuid4()
    existing = _note(id=note_id, scope="mailbox", scope_key=f"mailbox:{MAILBOX}")
    narrowed = _note(
        id=note_id,
        scope="sender_domain",
        scope_key="domain:statuspage.io",
    )

    with (
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.get_teaching_note_by_id",
            AsyncMock(return_value=existing),
        ),
        patch(
            "app.api.web.teaching_notes.promotion_proposal_repo.create_promotion_proposal",
            AsyncMock(),
        ) as mock_proposal,
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.update_teaching_note_scope",
            AsyncMock(return_value=narrowed),
        ) as mock_scope,
        patch(
            "app.api.web.teaching_notes.teaching_note_repo.update_teaching_note",
            AsyncMock(return_value=narrowed),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/api/teaching-notes/{note_id}",
                json={"scope": "sender_domain", "sender_domain": "statuspage.io"},
            )

    assert resp.status_code == 200
    mock_proposal.assert_not_awaited()
    mock_scope.assert_awaited_once()
    assert mock_scope.await_args.kwargs["scope"] == "sender_domain"
    assert mock_scope.await_args.kwargs["scope_key"] == "domain:statuspage.io"
