"""Persist InboxAssistant turns and compact older history like Riverside's chatbot_sessions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas.chat import ChatCitedThread, ChatHistoryTurn
from app.repositories import chat_session_repo

CHAT_SESSION_WINDOW = 16


def compact_history(
    turns: list[ChatHistoryTurn],
    *,
    prior_summary: str = "",
) -> tuple[list[ChatHistoryTurn], str]:
    """Keep the last N turns; fold older turns into a plain-text summary."""
    if len(turns) <= CHAT_SESSION_WINDOW:
        return list(turns), prior_summary
    older = turns[:-CHAT_SESSION_WINDOW]
    kept = turns[-CHAT_SESSION_WINDOW:]
    lines: list[str] = []
    if prior_summary.strip():
        lines.append(prior_summary.strip())
    for turn in older:
        role = "User" if turn.role == "user" else "Assistant"
        lines.append(f"{role}: {turn.content}")
    return kept, "\n".join(lines)


def _turns_from_json(raw: list) -> list[ChatHistoryTurn]:
    turns: list[ChatHistoryTurn] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        citations: list[ChatCitedThread] = []
        for cited in item.get("citations") or []:
            if not isinstance(cited, dict):
                continue
            try:
                citations.append(
                    ChatCitedThread(
                        thread_id=uuid.UUID(str(cited["thread_id"])),
                        subject=cited.get("subject"),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        turns.append(ChatHistoryTurn(role=role, content=content, citations=citations))
    return turns


def _turns_to_json(turns: list[ChatHistoryTurn]) -> list[dict]:
    payload: list[dict] = []
    for turn in turns:
        entry: dict = {"role": turn.role, "content": turn.content}
        if turn.citations:
            entry["citations"] = [
                {
                    "thread_id": str(cited.thread_id),
                    "subject": cited.subject,
                }
                for cited in turn.citations
            ]
        payload.append(entry)
    return payload


async def create_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    mailbox: str | None,
) -> chat_session_repo.ChatSessionSchema:
    return await chat_session_repo.create(session, user_id=user_id, mailbox=mailbox)


async def get_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
) -> chat_session_repo.ChatSessionSchema | None:
    return await chat_session_repo.get_for_user(session, session_id, user_id)


def history_from_session(
    row: chat_session_repo.ChatSessionSchema,
) -> list[ChatHistoryTurn]:
    turns = _turns_from_json(list(row.messages or []))
    if row.summary.strip():
        # Prepend a synthetic user note so the agent sees compacted context.
        return [
            ChatHistoryTurn(
                role="user",
                content=f"Earlier conversation summary:\n{row.summary.strip()}",
            ),
            *turns,
        ]
    return turns


async def append_turn(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    user_message: str,
    assistant_message: str,
    citations: list[dict] | None = None,
) -> chat_session_repo.ChatSessionSchema | None:
    row = await chat_session_repo.get_for_user(session, session_id, user_id)
    if row is None:
        return None
    turns = _turns_from_json(list(row.messages or []))
    turns.append(ChatHistoryTurn(role="user", content=user_message))
    cited: list[ChatCitedThread] = []
    for item in citations or []:
        try:
            cited.append(
                ChatCitedThread(
                    thread_id=uuid.UUID(str(item["thread_id"])),
                    subject=item.get("subject"),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    turns.append(
        ChatHistoryTurn(
            role="assistant",
            content=assistant_message,
            citations=cited,
        )
    )
    kept, summary = compact_history(turns, prior_summary=row.summary or "")
    return await chat_session_repo.update_messages(
        session,
        session_id=session_id,
        user_id=user_id,
        messages=_turns_to_json(kept),
        summary=summary,
        last_message_at=datetime.now(UTC),
    )
