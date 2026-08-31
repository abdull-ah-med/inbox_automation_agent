"""Mailbox contact repo: per-mailbox alias CRUD + isolation."""

from __future__ import annotations

import pytest

from app.repositories import mailbox_contact_repo

pytestmark = pytest.mark.asyncio


async def test_upsert_get_and_case_normalization(db_session) -> None:
    row, created = await mailbox_contact_repo.upsert(
        db_session,
        "Elise@Example.com",
        "Samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kelvin",
        notes="goes by Kel",
    )
    await db_session.commit()
    assert created is True
    assert row.mailbox == "elise@example.com"
    assert row.email == "samplecontact@sample-vendor.example.com"
    assert row.first_name == "Kelvin"
    assert row.full_name == "Kelvin Collado"

    fetched = await mailbox_contact_repo.get(
        db_session,
        "ELISE@example.com",
        "samplecontact@sample-vendor.example.com",
    )
    assert fetched is not None
    assert fetched.first_name == "Kelvin"

    updated, created_again = await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kel",
    )
    await db_session.commit()
    assert created_again is False
    assert updated.first_name == "Kel"


async def test_get_many_single_query_and_cross_mailbox_isolation(db_session) -> None:
    await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "a@example.com",
        full_name="Alice A",
        first_name="Alice",
    )
    await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "b@example.com",
        full_name="Bob B",
        first_name="Bob",
    )
    await mailbox_contact_repo.upsert(
        db_session,
        "bob@example.com",
        "a@example.com",
        full_name="Other Alice",
        first_name="Other",
    )
    await db_session.commit()

    found = await mailbox_contact_repo.get_many(
        db_session,
        "elise@example.com",
        ["A@Example.com", "b@example.com", "missing@example.com", "A@Example.com"],
    )
    assert set(found.keys()) == {"a@example.com", "b@example.com"}
    assert found["a@example.com"].first_name == "Alice"
    assert found["b@example.com"].first_name == "Bob"

    bob_view = await mailbox_contact_repo.get_many(
        db_session,
        "bob@example.com",
        ["a@example.com", "b@example.com"],
    )
    assert set(bob_view.keys()) == {"a@example.com"}
    assert bob_view["a@example.com"].first_name == "Other"


async def test_list_search_update_delete(db_session) -> None:
    await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kelvin",
    )
    await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "alex@sample-helpdesk.example.com",
        full_name="Alex Taylor",
        first_name="Alex",
    )
    await db_session.commit()

    rows, total = await mailbox_contact_repo.list_by_mailbox(
        db_session,
        "elise@example.com",
        q="Kel",
    )
    assert total == 1
    assert rows[0].email == "samplecontact@sample-vendor.example.com"

    patched = await mailbox_contact_repo.update(
        db_session,
        "elise@example.com",
        "samplecontact@sample-vendor.example.com",
        first_name="Kel",
    )
    await db_session.commit()
    assert patched is not None
    assert patched.first_name == "Kel"

    missing = await mailbox_contact_repo.update(
        db_session,
        "elise@example.com",
        "nobody@example.com",
        first_name="X",
    )
    assert missing is None

    deleted = await mailbox_contact_repo.delete(
        db_session,
        "elise@example.com",
        "samplecontact@sample-vendor.example.com",
    )
    await db_session.commit()
    assert deleted is True
    assert (
        await mailbox_contact_repo.get(
            db_session,
            "elise@example.com",
            "samplecontact@sample-vendor.example.com",
        )
        is None
    )
