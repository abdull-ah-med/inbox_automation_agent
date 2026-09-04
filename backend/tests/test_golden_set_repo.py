"""DB tests for golden_set_repo.

Worked example:
  - Insert one case for mailbox "sales@example.com" with expected_urgency="HIGH"
  - list_by_mailbox returns exactly 1 row
  - The returned case has the correct email_text and expected_urgency literals
  - Insert a second case for a different mailbox; list for first mailbox still
    returns exactly 1 (mailbox isolation)
"""

from __future__ import annotations

import pytest

from app.repositories import golden_set_repo

pytestmark = pytest.mark.db

SALES = "sales@example.com"
OTHER = "other@example.com"

EMAIL_TEXT = "Please invoice for 3 widgets at $150 each. Total $450."


async def test_insert_and_list_by_mailbox(db_session) -> None:
    """Inserted case is retrievable by mailbox with correct expected_urgency."""
    case = await golden_set_repo.insert_golden_case(
        db_session,
        mailbox=SALES,
        email_text=EMAIL_TEXT,
        expected_urgency="HIGH",
        expected_action="draft_reply",
    )

    assert case.mailbox == SALES
    assert case.email_text == EMAIL_TEXT
    assert case.expected_urgency == "HIGH"  # literal
    assert case.expected_action == "draft_reply"

    rows = await golden_set_repo.list_golden_cases_by_mailbox(db_session, mailbox=SALES)
    assert len(rows) == 1  # exactly one case inserted


async def test_mailbox_isolation(db_session) -> None:
    """Cases for OTHER mailbox do not appear in SALES list."""
    await golden_set_repo.insert_golden_case(
        db_session,
        mailbox=SALES,
        email_text="Invoice query",
        expected_urgency="NORMAL",
    )
    await golden_set_repo.insert_golden_case(
        db_session,
        mailbox=OTHER,
        email_text="Drug screen question",
        expected_urgency="CRITICAL",
    )

    sales_rows = await golden_set_repo.list_golden_cases_by_mailbox(db_session, mailbox=SALES)
    other_rows = await golden_set_repo.list_golden_cases_by_mailbox(db_session, mailbox=OTHER)

    assert len(sales_rows) == 1  # only the SALES case
    assert len(other_rows) == 1  # only the OTHER case
    assert sales_rows[0].expected_urgency == "NORMAL"
    assert other_rows[0].expected_urgency == "CRITICAL"


async def test_insert_with_associations_jsonb(db_session) -> None:
    """expected_associations JSONB is stored and returned verbatim."""
    assoc = {"thread_type": "billing", "flags": ["overdue"]}
    await golden_set_repo.insert_golden_case(
        db_session,
        mailbox=SALES,
        email_text="Your invoice is 30 days overdue.",
        expected_urgency="HIGH",
        expected_associations=assoc,
    )

    rows = await golden_set_repo.list_golden_cases_by_mailbox(db_session, mailbox=SALES)
    assert len(rows) == 1
    # Literal oracle: exact JSONB value
    assert rows[0].expected_associations == {"thread_type": "billing", "flags": ["overdue"]}
