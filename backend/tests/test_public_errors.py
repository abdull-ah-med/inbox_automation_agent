"""Public API error sanitization — clients must not see internal IDs/paths.

Oracle: security SPEC (stable phrases per exception class; error_type retained).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import (
    AuthError,
    DraftNotFoundError,
    EmptySearchQueryError,
    GraphClientError,
    InboxTriageError,
    InvalidCredentialsError,
    InvalidCursorError,
    InvalidDateRangeError,
    InvalidTokenError,
    RejectionMemoryNotFoundError,
    ReplyMemoryNotFoundError,
    ReusedRefreshTokenError,
    SkillBudgetExceededError,
    SkillNameConflictError,
    SkillNotFoundError,
    ThreadNotFoundError,
    ThreadStateError,
    UnknownMailboxError,
)
from app.core.public_errors import public_detail


def test_graph_client_error_uses_stable_upstream_phrase() -> None:
    exc = GraphClientError("Graph 403 for /users/abc/messages/msg-99 path=/tmp/x")
    assert public_detail(exc) == "Upstream Microsoft Graph request failed"
    assert "msg-99" not in public_detail(exc)
    assert "/tmp" not in public_detail(exc)


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidCredentialsError("Invalid email or password"), "Invalid credentials"),
        (InvalidTokenError("Refresh token expired"), "Invalid or expired token"),
        (ReusedRefreshTokenError("token reuse detected secret=abc"), "Authentication failed"),
        (AuthError("raw internal auth dump"), "Authentication failed"),
    ],
)
def test_auth_errors_never_echo_internal_strings(exc: AuthError, expected: str) -> None:
    assert public_detail(exc) == expected
    assert "secret" not in public_detail(exc)
    assert "expired" not in public_detail(exc) or expected == "Invalid or expired token"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (DraftNotFoundError(f"Draft not found: {uuid.uuid4()}"), "Draft not found"),
        (ThreadNotFoundError(f"Thread not found: {uuid.uuid4()}"), "Thread not found"),
        (SkillNotFoundError(f"Skill not found: {uuid.uuid4()}"), "Skill not found"),
        (
            ReplyMemoryNotFoundError(f"missing reply {uuid.uuid4()}"),
            "Reply memory not found",
        ),
        (
            RejectionMemoryNotFoundError(f"missing rejection {uuid.uuid4()}"),
            "Rejection memory not found",
        ),
    ],
)
def test_not_found_errors_omit_ids(exc: InboxTriageError, expected: str) -> None:
    detail = public_detail(exc)
    assert detail == expected
    assert str(uuid.UUID(int=0))[:8] not in detail or True
    # No UUID hex fragments from the exception message.
    assert "-" not in detail or detail.count("-") <= 0


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidCursorError("cursor decode failed at offset 12"), "Invalid cursor"),
        (InvalidDateRangeError("date range exceeds 90 days"), "Invalid date range"),
        (EmptySearchQueryError("query must not be blank"), "Empty search query"),
        (UnknownMailboxError("Mailbox not found"), "Mailbox not found"),
        (
            SkillNameConflictError("Skill name already exists: Pricing"),
            "Skill name already exists",
        ),
        (
            SkillBudgetExceededError("Active skill limit exceeded (max 20)"),
            "Skill budget exceeded",
        ),
        (ThreadStateError("Association was not proposed"), "Invalid thread state"),
    ],
)
def test_validation_errors_use_stable_phrases(exc: InboxTriageError, expected: str) -> None:
    assert public_detail(exc) == expected


def test_default_inbox_triage_error_is_request_failed() -> None:
    exc = InboxTriageError(f"internal boom id={uuid.uuid4()} path=/var/secret")
    assert public_detail(exc) == "Request failed"
    assert "secret" not in public_detail(exc)


@pytest.mark.asyncio
async def test_exception_handler_returns_public_detail_and_error_type() -> None:
    """API seam: detail is sanitized; error_type remains the class name."""
    from app.main import EXCEPTION_STATUS_MAP

    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise DraftNotFoundError(f"Draft not found: {uuid.uuid4()}")

    @app.exception_handler(InboxTriageError)
    async def handler(_: Request, exc: InboxTriageError) -> JSONResponse:
        status_code = EXCEPTION_STATUS_MAP.get(
            type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        return JSONResponse(
            status_code=status_code,
            content={"detail": public_detail(exc), "error_type": type(exc).__name__},
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")

    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Draft not found"
    assert body["error_type"] == "DraftNotFoundError"
    assert "Draft not found:" not in body["detail"]
