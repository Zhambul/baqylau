# api/middleware.py — what wraps EVERY response, in one place.
#
# Two policies, both about the response and neither about any route: the
# security headers every reply carries, and the compression exemption the event
# streams need. They are ASGI middleware rather than route dependencies because
# a route is exactly the thing they must not have to know about — including the
# static server, the two evidence-plane endpoints that answer in a harness's own
# bytes, and the error handlers.
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware

from api import config


class SecurityHeaders:
    """Stamp api.config.SECURITY_HEADERS onto every response.

    Applied here, as the outermost thing `add_middleware` can install, so it
    reaches the replies no handler produced — a guard's 403, the framework's 404
    — and the streaming ones, whose headers go out long before their body ends.
    The single response it cannot wrap is the 500 from Starlette's own
    ServerErrorMiddleware, which sits above every user middleware; api/app.py
    `_error_body` stamps that one from the same constant.

    Only ever ADDS: a route that set a header itself (the static server's
    Cache-Control) keeps its value, because a policy that silently overwrote a
    handler's own decision would be a second, invisible owner of it.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in config.SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SelectiveGZip:
    """Gzip everything except the event streams: compressing SSE would buffer
    the incremental frames the streams exist to deliver. An EventSource always
    sends `Accept: text/event-stream`, which is the routing fact used here."""

    def __init__(self, app) -> None:
        self.plain = app
        self.compressing = GZipMiddleware(app, minimum_size=config.GZIP_MIN)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or ())
            if b"text/event-stream" in headers.get(b"accept", b""):
                await self.plain(scope, receive, send)
                return
        await self.compressing(scope, receive, send)
