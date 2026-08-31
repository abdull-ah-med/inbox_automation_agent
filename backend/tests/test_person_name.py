"""Person-shaped display name gates for salutations."""

from __future__ import annotations

from app.core.person_name import display_name_first_name, person_shaped_from


def test_person_shaped_from_accepts_two_token_name() -> None:
    assert person_shaped_from("Jane Doe") is True


def test_person_shaped_from_rejects_org_label() -> None:
    assert person_shaped_from("Vendor Newsletter") is False


def test_person_shaped_from_accepts_last_first() -> None:
    assert person_shaped_from("Hooker, Ruth E") is True


def test_display_name_first_name_from_two_token_display() -> None:
    assert display_name_first_name("Kelvin Collado") == "Kelvin"


def test_display_name_first_name_from_last_first() -> None:
    assert display_name_first_name("Hooker, Ruth E") == "Ruth"


def test_display_name_first_name_rejects_org_display() -> None:
    assert display_name_first_name("Vendor Newsletter") is None


def test_display_name_first_name_rejects_single_token() -> None:
    assert display_name_first_name("Support") is None
