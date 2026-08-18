"""Shared judge-model helpers for DeepEval and RAGAS (OpenAI judge by default)."""

from __future__ import annotations

import os

from app.core.config import Settings


def require_eval_settings() -> Settings:
    settings = Settings()
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY required (generation under test)")
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY required (OpenAI judge + RAGAS embeddings)")
    # DeepEval/RAGAS SDKs read process env, not pydantic Settings.
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
    os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    return settings


def deepeval_judge_model() -> str:
    """OpenAI model id for DeepEval metrics (string form)."""
    return os.environ.get("EVAL_JUDGE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def ragas_judge_model_name() -> str:
    return os.environ.get("EVAL_JUDGE_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"


def ragas_embedding_model_name() -> str:
    return (
        os.environ.get("EVAL_EMBEDDING_MODEL", "text-embedding-3-small").strip()
        or "text-embedding-3-small"
    )
