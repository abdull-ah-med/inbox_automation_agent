"""Cursor encoding helpers for thread listing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.repositories.thread_repo import _decode_cursor, _encode_cursor


def test_cursor_roundtrip() -> None:
    thread_id = uuid.uuid4()
    ts = datetime.now(UTC)
    cursor = _encode_cursor(ts, thread_id)
    decoded_ts, decoded_id = _decode_cursor(cursor)
    assert decoded_id == thread_id
    assert decoded_ts == ts
