"""SlowAPI checks without buffering streaming responses.

SlowAPI's ``SlowAPIMiddleware`` subclasses ``BaseHTTPMiddleware``, which
consumes the full body before headers go out. ``SlowAPIASGIMiddleware`` holds
``http.response.start`` and re-sends it on every body chunk. Both break SSE.
This wrapper uses the same limit checks and injects headers on the first start
message only.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.middleware import _find_route_handler, _should_exempt, async_check_limits
from starlette.applications import Starlette
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SlowAPIStreamingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        app: Starlette = scope["app"]
        limiter: Limiter = app.state.limiter
        if not limiter.enabled:
            await self.app(scope, receive, send)
            return

        handler = _find_route_handler(app.routes, scope)
        request = Request(scope, receive=receive, send=send)
        if _should_exempt(limiter, handler):
            await self.app(scope, receive, send)
            return

        error_response, should_inject_headers = await async_check_limits(
            limiter, request, handler, app
        )
        if error_response is not None:
            await error_response(scope, receive, send)
            return

        if not should_inject_headers:
            await self.app(scope, receive, send)
            return

        async def send_with_limit_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=list(message.get("headers", [])))
                headers = limiter._inject_asgi_headers(headers, request.state.view_rate_limit)
                message = {**message, "headers": headers.raw}
            await send(message)

        await self.app(scope, receive, send_with_limit_headers)
