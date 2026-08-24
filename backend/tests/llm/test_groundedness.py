"""Groundedness verifier: Haiku checks claims against citation text.

Oracles are literal claim strings from the worked examples, not a recomputed
classifier. Canned refusals must not call the model.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from anthropic import APIError

from app.core.config import Settings
from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER

AUG12_ANSWER = "Ashley signed the deal on Aug 12."
CITATION_WITHOUT_DATE = "Ashley asked us to review the contract this week."
PARAPHRASE_ANSWER = (
    "Ashley asked the team to look over the contract during this week."
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "local",
        "jwt_secret": "c" * 64,
        "frontend_origin": "http://localhost:3000",
        "cookie_secure": False,
        "target_mailboxes": "sales@example.com",
        "anthropic_api_key": "sk-ant-test",
        "chat_model": "claude-haiku-4-5",
        "classification_model": "claude-haiku-4-5",
        "database_url": (
            "postgresql+asyncpg://postgres:postgres@localhost:5432/inbox_triage_test"
        ),
        "redis_url": "redis://localhost:6379/15",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _text_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _client(text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=_text_response(text))
    return client


def test_groundedness_disabled_by_default() -> None:
    settings = Settings(environment="local", _env_file=None)
    assert settings.chat_groundedness_enabled is False


@pytest.mark.asyncio
async def test_date_claim_missing_from_citations_is_unsupported() -> None:
    from app.llm.groundedness import verify_grounded

    assert "Aug 12" not in CITATION_WITHOUT_DATE
    client = _client(
        '{"verdict":"UNSUPPORTED","unsupported_spans":["Aug 12"]}'
    )
    result = await verify_grounded(
        AUG12_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "UNSUPPORTED"
    assert "Aug 12" in result.unsupported_spans
    user = client.messages.create.await_args.kwargs["messages"][0]["content"]
    assert AUG12_ANSWER in user
    assert CITATION_WITHOUT_DATE in user


@pytest.mark.asyncio
async def test_paraphrase_of_citation_is_supported() -> None:
    from app.llm.groundedness import verify_grounded

    client = _client('{"verdict":"SUPPORTED","unsupported_spans":[]}')
    result = await verify_grounded(
        PARAPHRASE_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "SUPPORTED"
    assert result.unsupported_spans == []


@pytest.mark.asyncio
async def test_write_refusal_does_not_call_verifier() -> None:
    from app.llm.groundedness import verify_grounded

    client = MagicMock()
    client.messages.create = AsyncMock()
    result = await verify_grounded(
        WRITE_REFUSAL_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "SUPPORTED"
    assert result.unsupported_spans == []
    assert client.messages.create.await_count == 0


@pytest.mark.asyncio
async def test_timeout_returns_unknown_not_supported() -> None:
    """H2: a verifier that never answers must not silently claim SUPPORTED —
    that would let an actually-UNSUPPORTED answer slip into the semantic
    cache and be served, unverified, to every future asker within its TTL.
    """
    from app.llm.groundedness import verify_grounded

    async def hangs(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(10)

    client = MagicMock()
    client.messages.create = hangs
    result = await verify_grounded(
        AUG12_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "UNKNOWN"
    assert result.verdict != "SUPPORTED"


@pytest.mark.asyncio
async def test_malformed_json_response_returns_unknown_not_supported() -> None:
    from app.llm.groundedness import verify_grounded

    client = _client("not valid json at all")
    result = await verify_grounded(
        AUG12_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "UNKNOWN"
    assert result.verdict != "SUPPORTED"


@pytest.mark.asyncio
async def test_api_error_returns_unknown_not_supported() -> None:
    import httpx

    from app.llm.groundedness import verify_grounded

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise APIError(
            "overloaded",
            httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            body=None,
        )

    client = MagicMock()
    client.messages.create = boom
    result = await verify_grounded(
        AUG12_ANSWER,
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert result.verdict == "UNKNOWN"
    assert result.verdict != "SUPPORTED"


@pytest.mark.asyncio
async def test_no_match_and_short_answers_skip_verifier() -> None:
    from app.llm.groundedness import verify_grounded

    client = MagicMock()
    client.messages.create = AsyncMock()
    no_match = await verify_grounded(
        NO_MATCH_ANSWER,
        [],
        client=client,
        settings=_settings(),
    )
    short = await verify_grounded(
        "No.",
        [{"text": CITATION_WITHOUT_DATE}],
        client=client,
        settings=_settings(),
    )
    assert no_match.verdict == "SUPPORTED"
    assert short.verdict == "SUPPORTED"
    assert client.messages.create.await_count == 0
