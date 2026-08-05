"""API tests for POST /api/skills/import and file list/stream endpoints."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.core.exceptions import (
    SkillAlreadyImportedError,
    SkillPackagingError,
    SkillPathTraversalError,
)
from app.main import create_app
from app.models.schemas.auth import UserMe
from app.models.schemas.skill import (
    ImportSkillResultSchema,
    SkillFileMetaSchema,
    SkillResponseSchema,
)
from app.repositories.skill_files_repo import SkillFileRow

FIXTURE_ZIP = (
    Path(__file__).resolve().parents[2] / "misc" / "samplelab-rebilling-skill.zip"
)


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


def _user(*, role: str = "admin") -> UserMe:
    return UserMe(
        id=uuid.uuid4(),
        email="elise@example.com",
        role=role,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def app_factory(local_settings: Settings):
    def _make(*, role: str = "admin"):
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
                return _user(role=role)

            application.dependency_overrides[get_current_user] = fake_user
            mock_session = MagicMock()
            mock_session.commit = AsyncMock()

            async def fake_db():
                yield mock_session

            application.dependency_overrides[get_db] = fake_db
            return application

    yield _make
    get_settings.cache_clear()


def _zip_bytes(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in members.items():
            zf.writestr(path, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_non_admin_403(app_factory) -> None:
    app = app_factory(role="user")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills/import",
                files={"file": ("skill.zip", b"PK\x03\x04fake", "application/zip")},
            )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_valid_zip_201(app_factory) -> None:
    app = app_factory(role="admin")
    skill_id = uuid.uuid4()
    result = ImportSkillResultSchema(
        skill_id=skill_id,
        name="samplelab-rebilling",
        description="Rebill SampleLab invoices",
        reference_files=[
            "references/client_rules.md",
            "references/output_format.md",
            "references/harmeyer_departments.csv",
        ],
        asset_files=[],
        warnings=[],
        overwritten=False,
    )
    try:
        with patch(
            "app.api.web.skills.skill_archive_service.import_skill_archive",
            AsyncMock(return_value=result),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/skills/import",
                    files={
                        "file": (
                            "samplelab-rebilling-skill.zip",
                            FIXTURE_ZIP.read_bytes(),
                            "application/zip",
                        )
                    },
                )
        assert resp.status_code == 201
        body = resp.json()
        assert body["skill_id"] == str(skill_id)
        assert body["name"] == "samplelab-rebilling"
        assert len(body["reference_files"]) == 3
        assert body["warnings"] == []
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_not_a_zip_422(app_factory) -> None:
    app = app_factory(role="admin")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/skills/import",
                files={"file": ("notes.txt", b"not a zip", "text/plain")},
            )
        assert resp.status_code == 422
        assert "zip" in resp.json()["detail"].lower() or ".skill" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_duplicate_hash_409(app_factory) -> None:
    app = app_factory(role="admin")
    existing_id = uuid.uuid4()
    try:
        with patch(
            "app.api.web.skills.skill_archive_service.import_skill_archive",
            AsyncMock(
                side_effect=SkillAlreadyImportedError(
                    "already imported",
                    skill_id=existing_id,
                )
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/skills/import",
                    files={
                        "file": (
                            "demo.zip",
                            _zip_bytes({"demo/SKILL.md": "---\nname: demo\ndescription: d\n---\n"}),
                            "application/zip",
                        )
                    },
                )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["skill_id"] == str(existing_id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_path_traversal_422(app_factory) -> None:
    app = app_factory(role="admin")
    try:
        with patch(
            "app.api.web.skills.skill_archive_service.import_skill_archive",
            AsyncMock(side_effect=SkillPathTraversalError("Unsafe archive path")),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/skills/import",
                    files={
                        "file": ("bad.zip", b"PK\x03\x04" + b"0" * 20, "application/zip"),
                    },
                )
        assert resp.status_code == 422
        assert "Unsafe" in resp.json()["detail"] or "path" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_import_packaging_error_422(app_factory) -> None:
    app = app_factory(role="admin")
    try:
        with patch(
            "app.api.web.skills.skill_archive_service.import_skill_archive",
            AsyncMock(side_effect=SkillPackagingError("File is not a valid zip archive")),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/skills/import",
                    files={"file": ("bad.zip", b"notzip", "application/zip")},
                )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_and_stream_skill_files(app_factory) -> None:
    app = app_factory(role="admin")
    skill_id = uuid.uuid4()
    skill = SkillResponseSchema.model_validate(
        {
            "id": skill_id,
            "name": "demo-skill",
            "description": "d",
            "content": "body",
            "category": "billing",
            "is_active": True,
            "source_kind": "imported",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    meta = SkillFileMetaSchema.model_validate(
        {
            "id": uuid.uuid4(),
            "relative_path": "references/client_rules.md",
            "kind": "reference",
            "mime_type": "text/markdown",
            "size_bytes": 12,
            "created_at": datetime.now(UTC),
        }
    )
    file_row = SkillFileRow(
        id=meta.id,
        skill_id=skill_id,
        relative_path="references/client_rules.md",
        kind="reference",
        mime_type="text/markdown",
        size_bytes=12,
        content=b"# Client rules",
    )
    try:
        with (
            patch(
                "app.api.web.skills.skill_repo.get_by_id",
                AsyncMock(return_value=skill),
            ),
            patch(
                "app.api.web.skills.skill_files_repo.list_by_skill",
                AsyncMock(return_value=[meta]),
            ),
            patch(
                "app.api.web.skills.skill_files_repo.get_by_path",
                AsyncMock(return_value=file_row),
            ),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                listed = await client.get(f"/api/skills/{skill_id}/files")
                streamed = await client.get(
                    f"/api/skills/{skill_id}/files/references/client_rules.md"
                )
        assert listed.status_code == 200
        assert listed.json()[0]["relative_path"] == "references/client_rules.md"
        assert streamed.status_code == 200
        assert streamed.content == b"# Client rules"
        assert "text/markdown" in streamed.headers["content-type"]
    finally:
        app.dependency_overrides.clear()
