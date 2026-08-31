"""Map InboxAssistant ask outputs onto DeepEval / RAGAS field contracts.

Retrieval contexts are the same hit blocks Haiku is grounded on.
Search is stubbed from curated gold hits so generator evals stay synthetic.
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

from app.core.config import Settings
from app.core.dependencies import anthropic_client_from_settings
from app.llm.chat import ChatAgentResult, run_chat_agent
from app.llm.chat_prompts import NO_MATCH_ANSWER, WRITE_REFUSAL_ANSWER
from app.llm.pii_redact import scrub_text
from app.models.schemas.search import SearchHit, SearchResponse
from app.services import chat_service
from app.services.chat_service import detect_write_intent


@dataclass
class ChatEvalPayload:
    case_id: str
    input_text: str
    actual_output: str
    expected_output: str | None
    retrieval_contexts: list[str]
    hits: list[SearchHit]
    refused_write: bool
    retrieval_count: int
    mailbox: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


def _parse_when(raw: object) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    text = str(raw).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def hits_from_case(case: dict[str, Any]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for item in case.get("hits") or []:
        hits.append(
            SearchHit(
                thread_id=uuid.UUID(str(item["thread_id"])),
                mailbox=str(item["mailbox"]),
                conversation_id=str(item["conversation_id"]),
                subject=item.get("subject"),
                state=str(item["state"]),
                urgency=item.get("urgency"),
                snippet=str(item.get("snippet") or ""),
                score=float(item.get("score") or 0.0),
                last_message_at=_parse_when(item.get("last_message_at")),
            )
        )
    return hits


def _hit_block(hit: SearchHit) -> str:
    """Mirror of former app.llm.chat._hit_block — keep eval contexts identical."""
    snippet = scrub_text(hit.snippet or "")
    subject = scrub_text(hit.subject or "") or "(no subject)"
    when = hit.last_message_at.isoformat() if hit.last_message_at else "(none)"
    return (
        f"mailbox: {hit.mailbox}\n"
        f"subject: {subject}\n"
        f"sender: {hit.sender or '(none)'}\n"
        f"state: {hit.state}\n"
        f"urgency: {hit.urgency or '(none)'}\n"
        f"last_message_at: {when}\n"
        f"snippet:\n{snippet}\n"
    )


def retrieval_contexts_from_hits(hits: list[SearchHit]) -> list[str]:
    """One judge context per retrieved thread — the same block packed for Haiku."""
    return [_hit_block(hit) for hit in hits]


def _payload(
    case: dict[str, Any],
    *,
    hits: list[SearchHit],
    answer: str,
    refused_write: bool,
    retrieval_count: int,
    mailbox: str | None,
) -> ChatEvalPayload:
    expected = str(case["expected_output"]) if case.get("expected_output") else None
    return ChatEvalPayload(
        case_id=str(case["id"]),
        input_text=str(case["question"]),
        actual_output=answer,
        expected_output=expected,
        retrieval_contexts=retrieval_contexts_from_hits(hits),
        hits=hits,
        refused_write=refused_write,
        retrieval_count=retrieval_count,
        mailbox=mailbox,
        metadata={
            "suite_tags": case.get("suite_tags", []),
            "geval_criteria": case.get("geval_criteria"),
            "expected_facts": case.get("expected_facts") or [],
            "forbidden_facts": case.get("forbidden_facts") or [],
        },
    )


def _canned_payload(case: dict[str, Any], hits: list[SearchHit]) -> ChatEvalPayload:
    question = str(case["question"])
    refused = bool(case.get("expect_write_refusal")) or detect_write_intent(question)
    if refused:
        answer = WRITE_REFUSAL_ANSWER if hits else f"{WRITE_REFUSAL_ANSWER}\n\n{NO_MATCH_ANSWER}"
        count = len(hits)
    elif not hits:
        answer = NO_MATCH_ANSWER
        count = 0
    else:
        answer = str(case["expected_output"]) if case.get("expected_output") else ""
        count = len(hits)
    return _payload(
        case,
        hits=hits,
        answer=answer,
        refused_write=refused,
        retrieval_count=count,
        mailbox=case.get("mailbox"),
    )


def _settings_from_env() -> Settings:
    settings = Settings()
    if not settings.anthropic_api_key.strip():
        raise RuntimeError("ANTHROPIC_API_KEY required for InboxAssistant eval generation")
    return settings


_GENERATE_CACHE: dict[str, ChatEvalPayload] = {}


async def run_chat_case(
    case: dict[str, Any],
    *,
    generate: bool = True,
    anthropic_client: Any = None,
) -> ChatEvalPayload:
    """Run InboxAssistant ask against gold hits. ``generate=False`` skips Haiku."""
    hits = hits_from_case(case)
    if not generate:
        return _canned_payload(case, hits)

    cached = _GENERATE_CACHE.get(str(case["id"]))
    if cached is not None:
        return cached

    question = str(case["question"])
    mailbox = case.get("mailbox")
    search = SearchResponse(query=question, mailbox=mailbox, hits=hits)
    settings = Settings() if anthropic_client is not None else _settings_from_env()
    client = anthropic_client or anthropic_client_from_settings(settings)
    refused = bool(case.get("expect_write_refusal")) or detect_write_intent(question)

    async def gold_agent(
        *,
        client: Any,
        settings: Settings,
        question: str,
        execute_tool: Any,
        history: Any = None,
        initial_tool: str | None = None,
        mailbox: str | None = None,
        user_id: Any = None,
    ) -> ChatAgentResult:
        from app.services.chat_tools import ChatToolExecution

        async def execute(name: str, arguments: dict) -> ChatToolExecution:
            _ = name, arguments
            return ChatToolExecution(hits=hits, status="Searching mail")

        _ = execute_tool
        if not hits:
            return ChatAgentResult(answer="", hits=[])
        return await run_chat_agent(
            client=client,
            settings=settings,
            question=question,
            execute_tool=execute,
            history=history,
            initial_tool=initial_tool,
            mailbox=mailbox,
            user_id=user_id,
        )

    patches = [
        patch(
            "app.services.chat_service.search_service.search_threads",
            AsyncMock(return_value=search),
        ),
    ]
    if not refused:
        patches.append(patch("app.services.chat_service.run_chat_agent", gold_agent))

    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        result = await chat_service.ask(
            session=AsyncMock(),
            settings=settings,
            openai_client=None,
            anthropic_client=client,
            message=question,
            mailbox=mailbox,
        )

    payload = _payload(
        case,
        hits=hits,
        answer=result.answer,
        refused_write=result.refused_write,
        retrieval_count=result.retrieval_count,
        mailbox=result.mailbox,
    )
    _GENERATE_CACHE[str(case["id"])] = payload
    return payload
