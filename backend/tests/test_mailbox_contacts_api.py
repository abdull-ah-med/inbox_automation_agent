"""Mailbox contacts API — CRUD for salutation aliases."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import get_db
from app.core.dependencies_auth import get_current_user
from app.models.schemas.mailbox_contact import MailboxContactListResponse, MailboxContactView
from app.repositories.mailbox_contact_repo import MailboxContactRow


def _row(**overrides: object) -> MailboxContactRow:
    base = {
        "id": uuid4(),
        "mailbox": "sales@example.com",
        "email": "samplecontact@sample-vendor.example.com",
        "full_name": "Kelvin Collado",
        "first_name": "Kelvin",
        "notes": None,
        "created_by_user_id": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    base.update(overrides)
    return MailboxContactRow(**base)  # type: ignore[arg-type]


def _view(row: MailboxContactRow) -> MailboxContactView:
    return MailboxContactView(
        email=row.email,
        full_name=row.full_name,
        first_name=row.first_name,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@pytest.mark.asyncio
async def test_list_contacts(app) -> None:
    row = _row()
    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.list_contacts",
        AsyncMock(
            return_value=MailboxContactListResponse(items=[_view(row)], total=1),
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/mailboxes/sales/contacts?q=Kel")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["first_name"] == "Kelvin"
    assert body["items"][0]["email"] == "samplecontact@sample-vendor.example.com"


@pytest.mark.asyncio
async def test_upsert_returns_201_on_create(app) -> None:
    row = _row()
    session = AsyncMock()
    session.begin = lambda: _AsyncBegin(session)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.upsert_contact",
        AsyncMock(return_value=(_view(row), True)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/mailboxes/sales/contacts",
                json={
                    "email": "samplecontact@sample-vendor.example.com",
                    "full_name": "Kelvin Collado",
                    "first_name": "Kelvin",
                },
            )
    assert response.status_code == 201
    assert response.json()["first_name"] == "Kelvin"


@pytest.mark.asyncio
async def test_upsert_returns_200_on_update(app) -> None:
    row = _row(first_name="Kel")
    session = AsyncMock()
    session.begin = lambda: _AsyncBegin(session)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.upsert_contact",
        AsyncMock(return_value=(_view(row), False)),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/mailboxes/sales/contacts",
                json={
                    "email": "samplecontact@sample-vendor.example.com",
                    "full_name": "Kelvin Collado",
                    "first_name": "Kel",
                },
            )
    assert response.status_code == 200
    assert response.json()["first_name"] == "Kel"


@pytest.mark.asyncio
async def test_patch_missing_returns_404(app) -> None:
    from fastapi import HTTPException, status

    session = AsyncMock()
    session.begin = lambda: _AsyncBegin(session)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.patch_contact",
        AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Contact not found",
            )
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                "/api/mailboxes/sales/contacts/by-email",
                json={"email": "nobody@example.com", "first_name": "X"},
            )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_mailbox_returns_404(app) -> None:
    from fastapi import HTTPException, status

    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.list_contacts",
        AsyncMock(
            side_effect=HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mailbox not found",
            )
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/mailboxes/unknown/contacts")
    assert response.status_code == 404
    assert response.json()["detail"] == "Mailbox not found"


@pytest.mark.asyncio
async def test_unauthorized_without_bearer(app) -> None:
    app.dependency_overrides.pop(get_current_user, None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/mailboxes/sales/contacts")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_contact(app) -> None:
    session = AsyncMock()
    session.begin = lambda: _AsyncBegin(session)

    async def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db
    with patch(
        "app.api.web.mailbox_contacts.mailbox_contact_service.delete_contact",
        AsyncMock(return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                "/api/mailboxes/sales/contacts/by-email",
                params={"email": "samplecontact@sample-vendor.example.com"},
            )
    assert response.status_code == 204


class _AsyncBegin:
    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None
