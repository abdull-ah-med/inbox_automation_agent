"""Unit tests for the optional OpenAI client dependency.

Regression coverage for the crash that left every thread stuck at ``NEW``:
``get_openai_client`` used to raise when ``OPENAI_API_KEY`` was unset, which
aborted ``run_after_ingest`` before triage ever ran. Embeddings are an
optional enrichment, so a missing/broken key must degrade to ``None``
instead of taking down the pipeline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from openai import AsyncOpenAI

from app.core import dependencies
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _reset_openai_singleton() -> None:
    """Each test observes a fresh module-level cache (mirrors app shutdown)."""
    dependencies._openai_client = None
    dependencies._openai_client_resolved = False
    yield
    dependencies._openai_client = None
    dependencies._openai_client_resolved = False


def test_get_openai_client_returns_none_when_key_unset() -> None:
    settings = Settings(environment="local", openai_api_key="")
    client = dependencies.get_openai_client(settings)
    assert client is None


def test_get_openai_client_returns_none_when_key_blank() -> None:
    settings = Settings(environment="local", openai_api_key="   ")
    client = dependencies.get_openai_client(settings)
    assert client is None


def test_get_openai_client_builds_client_when_key_present() -> None:
    settings = Settings(environment="local", openai_api_key="sk-test-key")
    client = dependencies.get_openai_client(settings)
    assert isinstance(client, AsyncOpenAI)


def test_get_openai_client_caches_across_calls() -> None:
    settings = Settings(environment="local", openai_api_key="sk-test-key")
    first = dependencies.get_openai_client(settings)
    second = dependencies.get_openai_client(settings)
    assert first is second


def test_get_openai_client_swallows_construction_errors() -> None:
    settings = Settings(environment="local", openai_api_key="sk-test-key")
    with patch(
        "app.core.dependencies.AsyncOpenAI",
        side_effect=RuntimeError("boom"),
    ):
        client = dependencies.get_openai_client(settings)
    assert client is None


def test_openai_client_from_settings_delegates_to_singleton() -> None:
    settings = Settings(environment="local", openai_api_key="")
    assert dependencies.openai_client_from_settings(settings) is None
