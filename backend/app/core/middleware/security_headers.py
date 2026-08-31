"""Security response headers middleware (FastAPI has no Helmet equivalent)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings


class SecurityHeadersMiddleware:
    """Attach hard security headers without buffering the response body.

    BaseHTTPMiddleware waits for the full body before ``http.response.start``.
    That turns SSE token deltas into a single dump. Pure ASGI sets headers on
    the start message and forwards each body chunk as it arrives.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
                headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                # cross-origin: SPA often runs on a different host than the API.
                headers["Cross-Origin-Resource-Policy"] = "cross-origin"
                # API is JSON-only — deny everything by default.
                headers["Content-Security-Policy"] = (
                    "default-src 'none'; connect-src 'self'; frame-ancestors 'none'"
                )
                if self._settings.environment != "local":
                    headers["Strict-Transport-Security"] = (
                        "max-age=63072000; includeSubDomains; preload"
                    )
                message = {**message, "headers": headers.raw}
            await send(message)

        await self.app(scope, receive, send_with_headers)
