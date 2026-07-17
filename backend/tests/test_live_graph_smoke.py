"""Live Graph smoke tests (opt-in) — evidence for Entra auth vs mailbox access.

Run from backend/:

    unset TARGET_MAILBOXES
    RUN_LIVE_GRAPH=1 ./venv/bin/pytest tests/test_live_graph_smoke.py -vv -s

Lead-ready report with Microsoft JWT claims + Graph request-id:

    unset TARGET_MAILBOXES
    ./venv/bin/python scripts/prove_graph_mailbox_access.py
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.exceptions import GraphClientError
from app.graph.auth import GraphAuth
from app.graph.client import GRAPH_BASE_URL, GraphClient

pytestmark = pytest.mark.live_graph


def _live_enabled() -> bool:
    return os.environ.get("RUN_LIVE_GRAPH", "").strip() in {"1", "true", "yes"}


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    if not isinstance(payload, dict):
        raise ValueError("JWT payload is not an object")
    return payload


def _print_token_claims(claims: dict[str, Any]) -> None:
    exp = claims.get("exp")
    exp_s = (
        datetime.fromtimestamp(exp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(exp, int)
        else str(exp)
    )
    print("  Microsoft Entra JWT claims:")
    print(f"    iss={claims.get('iss')}")
    print(f"    aud={claims.get('aud')}")
    print(f"    tid={claims.get('tid')}")
    print(f"    appid/azp={claims.get('appid') or claims.get('azp')}")
    print(f"    roles={claims.get('roles')}")
    print(f"    exp={exp_s}")


@pytest.fixture
def live_settings() -> Settings:
    if not _live_enabled():
        pytest.skip("Set RUN_LIVE_GRAPH=1 to run live Graph smoke tests")

    settings = Settings()
    if not (settings.graph_client_id and settings.graph_client_secret and settings.graph_tenant_id):
        pytest.skip("GRAPH_CLIENT_ID / GRAPH_CLIENT_SECRET / GRAPH_TENANT_ID required in .env")
    if not settings.mailbox_list:
        pytest.skip("TARGET_MAILBOXES required in .env")
    return settings


@pytest.fixture
def live_mailbox(live_settings: Settings) -> str:
    """Mailbox only — avoids dumping Settings (incl. secret) in pytest failure repr."""
    return live_settings.mailbox_list[0]


@pytest.mark.asyncio
async def test_live_graph_auth_acquires_token(live_settings: Settings) -> None:
    """PROVES: App MSAL client-credentials auth works against Entra."""
    auth = GraphAuth(live_settings, redis=None)
    token = await auth.get_access_token()

    assert isinstance(token, str)
    assert len(token) > 20
    claims = _decode_jwt_claims(token)
    assert claims.get("tid") == live_settings.graph_tenant_id
    assert "graph.microsoft.com" in str(claims.get("aud", ""))

    print("\n======== GRAPH ACCESS PROOF ========")
    print("STEP 1 — Entra issued an access token (Microsoft JWT evidence):")
    _print_token_claims(claims)
    print("====================================\n")


@pytest.mark.asyncio
async def test_live_graph_mailbox_read_access(
    live_settings: Settings,
    live_mailbox: str,
) -> None:
    """PROVES: App GraphClient can call Graph; 401 means mailbox not reachable in tenant."""
    auth = GraphAuth(live_settings, redis=None)
    token = await auth.get_access_token()
    claims = _decode_jwt_claims(token)
    client = GraphClient(auth)
    try:
        try:
            messages = await client.list_messages(live_mailbox, top=5)
        except GraphClientError as exc:
            detail = str(exc)
            if "401" in detail:
                # Capture Microsoft Graph response headers for the lead report.
                async with httpx.AsyncClient(timeout=30.0) as http:
                    graph_resp = await http.get(
                        f"{GRAPH_BASE_URL}/users/{live_mailbox}/mailFolders('inbox')/messages",
                        params={"$top": "1"},
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    )
                print("\n======== GRAPH ACCESS PROOF ========")
                print("STEP 1 — Entra token OK (Microsoft JWT):")
                _print_token_claims(claims)
                print("STEP 2 — Microsoft Graph mailbox read DENIED:")
                print(f"    status_code={graph_resp.status_code}")
                print(f"    request-id={graph_resp.headers.get('request-id')}")
                print(f"    client-request-id={graph_resp.headers.get('client-request-id')}")
                print(f"    date={graph_resp.headers.get('date')}")
                print(f"    body={(graph_resp.text or '')[:500]!r}")
                print("====================================\n")
                pytest.fail(
                    "PROOF: Microsoft Entra authenticated the app (JWT iss/aud/tid/roles), "
                    f"but Microsoft Graph returned HTTP {graph_resp.status_code} for mailbox "
                    f"'{live_mailbox}' (request-id={graph_resp.headers.get('request-id')}). "
                    "Need an M365 mailbox UPN in this tenant. "
                    f"Detail: {detail}"
                )
            raise

        subjects = [m.subject or "(no subject)" for m in messages]
        print("\n======== GRAPH ACCESS PROOF ========")
        print("STEP 1 — Entra token OK:")
        _print_token_claims(claims)
        print("STEP 2 — Microsoft Graph mailbox read OK:")
        print(f"    mailbox={live_mailbox} count={len(messages)} subjects={subjects}")
        print("====================================\n")
        assert isinstance(messages, list)
    finally:
        await client.aclose()
