"""Mailbox contact repo: global greeting names keyed by email."""

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
        "samplecontact@sample-vendor.example.com",
    )
    assert fetched is not None
    assert fetched.first_name == "Kelvin"

    updated, created_again = await mailbox_contact_repo.upsert(
        db_session,
        "support@example.com",
        "samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kel",
    )
    await db_session.commit()
    assert created_again is False
    assert updated.first_name == "Kel"
    assert updated.mailbox == "support@example.com"


async def test_get_many_is_global_across_mailboxes(db_session) -> None:
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
    await db_session.commit()

    found = await mailbox_contact_repo.get_many(
        db_session,
        ["A@Example.com", "b@example.com", "missing@example.com", "A@Example.com"],
    )
    assert set(found.keys()) == {"a@example.com", "b@example.com"}
    assert found["a@example.com"].first_name == "Alice"
    assert found["b@example.com"].first_name == "Bob"

    # Same email taught from Support must resolve for any mailbox lookup.
    from_other_mailbox = await mailbox_contact_repo.get_many(
        db_session,
        ["a@example.com", "b@example.com"],
    )
    assert from_other_mailbox["a@example.com"].first_name == "Alice"


async def test_list_search_update_delete_are_global(db_session) -> None:
    await mailbox_contact_repo.upsert(
        db_session,
        "elise@example.com",
        "samplecontact@sample-vendor.example.com",
        full_name="Kelvin Collado",
        first_name="Kelvin",
    )
    await mailbox_contact_repo.upsert(
        db_session,
        "support@example.com",
        "alex@sample-helpdesk.example.com",
        full_name="Alex Taylor",
        first_name="Alex",
    )
    await db_session.commit()

    rows, total = await mailbox_contact_repo.list_contacts(
        db_session,
        q="Kel",
    )
    assert total == 1
    assert rows[0].email == "samplecontact@sample-vendor.example.com"

    all_rows, all_total = await mailbox_contact_repo.list_contacts(db_session)
    assert all_total == 2
    assert {row.email for row in all_rows} == {
        "alex@sample-helpdesk.example.com",
        "samplecontact@sample-vendor.example.com",
    }

    patched = await mailbox_contact_repo.update(
        db_session,
        "samplecontact@sample-vendor.example.com",
        first_name="Kel",
    )
    await db_session.commit()
    assert patched is not None
    assert patched.first_name == "Kel"

    missing = await mailbox_contact_repo.update(
        db_session,
        "nobody@example.com",
        first_name="X",
    )
    assert missing is None

    deleted = await mailbox_contact_repo.delete(
        db_session,
        "samplecontact@sample-vendor.example.com",
    )
    await db_session.commit()
    assert deleted is True
    assert await mailbox_contact_repo.get(db_session, "samplecontact@sample-vendor.example.com") is None
