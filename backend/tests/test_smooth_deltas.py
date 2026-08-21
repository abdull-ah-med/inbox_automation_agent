"""Server-side word-aligned re-chunking for bursty Anthropic text_stream.

Mirrors Vercel AI SDK smoothStream({chunking:'word'}) so client reveal
does not have to hide 400-char bursts.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_smooth_deltas_splits_a_burst_into_word_aligned_emissions() -> None:
    from app.llm.smooth_deltas import smooth_deltas

    burst = (
        "The overdue billing dispute is waiting on review "
        "and Ashley still needs the signed certificate on file "
        "before they can add new users to the portal today."
    )
    assert len(burst) >= 100

    async def one_burst():
        yield burst

    chunks: list[str] = []
    async for piece in smooth_deltas(one_burst(), min_delay_ms=0):
        chunks.append(piece)

    assert len(chunks) >= 5
    assert "".join(chunks) == burst
    assert all(chunk for chunk in chunks)
    # Every emission except possibly the last ends on a word boundary (whitespace).
    for chunk in chunks[:-1]:
        assert chunk[-1].isspace()


@pytest.mark.asyncio
async def test_smooth_deltas_preserves_small_token_stream() -> None:
    from app.llm.smooth_deltas import smooth_deltas

    async def tokens():
        for piece in ["The ", "overdue ", "billing"]:
            yield piece

    chunks = [piece async for piece in smooth_deltas(tokens(), min_delay_ms=0)]
    assert "".join(chunks) == "The overdue billing"
