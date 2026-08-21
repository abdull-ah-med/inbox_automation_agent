"""Word-aligned delta smoothing for Anthropic text_stream bursts.

Follows Vercel AI SDK smoothStream defaults (word chunking, ~10ms delay)
with a hard cap on total added sleep so long answers stay snappy.
https://ai-sdk.dev/docs/reference/ai-sdk-core/smooth-stream
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

_WORD_CHUNK = re.compile(r"\S+\s+", re.MULTILINE)
_DEFAULT_MIN_DELAY_MS = 8.0
_DEFAULT_MAX_ADDED_DELAY_MS = 300.0


async def smooth_deltas(
    source: AsyncIterator[str],
    *,
    chunk_re: re.Pattern[str] = _WORD_CHUNK,
    min_delay_ms: float = _DEFAULT_MIN_DELAY_MS,
    max_added_delay_ms: float = _DEFAULT_MAX_ADDED_DELAY_MS,
) -> AsyncIterator[str]:
    """Buffer incoming text and emit word-aligned chunks with a capped delay."""
    buffer = ""
    added_delay_ms = 0.0
    pending_sleep = False

    async def _maybe_sleep() -> None:
        nonlocal added_delay_ms, pending_sleep
        if not pending_sleep:
            return
        if min_delay_ms <= 0:
            pending_sleep = False
            return
        if added_delay_ms >= max_added_delay_ms:
            pending_sleep = False
            return
        delay = min(min_delay_ms, max_added_delay_ms - added_delay_ms)
        await asyncio.sleep(delay / 1000.0)
        added_delay_ms += delay
        pending_sleep = False

    async for piece in source:
        if not piece:
            continue
        buffer += piece
        while True:
            match = chunk_re.search(buffer)
            if match is None:
                break
            emit = buffer[: match.end()]
            buffer = buffer[match.end() :]
            await _maybe_sleep()
            yield emit
            # Only sleep between emissions when the buffer is empty (caught up).
            pending_sleep = not buffer

    if buffer:
        await _maybe_sleep()
        yield buffer
