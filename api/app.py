# api/app.py — the FastAPI application factory.
#
# build_web_application(graph) wires the routers, the error contract, the
# compression policy and the OpenAPI documents around one already-built
# application graph. It builds no graph itself — that is api/server.py
# serve()'s job, exactly once — which is what lets tests hand in a fixture
# graph the same way.
from __future__ import annotations

from contextlib import asynccontextmanager

import anyio.to_thread
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from api import config
from api.common import content, hooks
from api.dashboard import application as dashboard_application
from api.dashboard import catalog, controls, files, sessions, static, telemetry
from api.dashboard import streams as dashboard_streams
from api.terminal import panes, views
from api.terminal import streams as terminal_streams
from app.bootstrap import CanonicalApplication
from core import audit as A

# Starlette's stock message for an unrouted path; the wire contract has always
# said {"error": "not found"} in this server's own casing.
_FRAMEWORK_NOT_FOUND = "Not Found"


@asynccontextmanager
async def _lifespan(_application: FastAPI):
    # Sync route handlers share the anyio worker-thread pool; SSE is async and
    # costs no thread, so this cap only has to absorb request bursts.
    anyio.to_thread.current_default_thread_limiter().total_tokens = config.THREAD_POOL_TOKENS
    yield


def _error_body(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code)


async def _http_error(_request: Request, error: StarletteHTTPException) -> JSONResponse:
    message = str(error.detail)
    if message == _FRAMEWORK_NOT_FOUND:
        message = "not found"
    return _error_body(message, error.status_code)


async def _validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    """A schema rejection as this server's one error shape: 400 with the first
    failing field named — {"error": "text: Input should be a valid string"}."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"] if part != "body")
    message = f"{location}: {first['msg']}" if location else str(first["msg"])
    return _error_body(message, 400)


async def _application_input_error(_request: Request, error: Exception) -> JSONResponse:
    """Bad application input raised past a route (unknown session, unknown
    catalogue, a malformed reference) — the read/control planes' 400 contract."""
    message = error.args[0] if error.args else str(error)
    return _error_body(str(message), 400)


async def _internal_error(request: Request, _error: Exception) -> JSONResponse:
    A.error("", "dashboard %s" % ("POST" if request.method == "POST" else "request"),
            {"path": request.url.path[:200]})
    return _error_body("internal", 500)


class _SelectiveGZip:
    """Gzip everything except the event streams: compressing SSE would buffer
    the incremental frames the streams exist to deliver. An EventSource always
    sends `Accept: text/event-stream`, which is the routing fact used here."""

    def __init__(self, app):
        self.plain = app
        self.compressing = GZipMiddleware(app, minimum_size=config.GZIP_MIN)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or ())
            if b"text/event-stream" in headers.get(b"accept", b""):
                await self.plain(scope, receive, send)
                return
        await self.compressing(scope, receive, send)


def build_web_application(graph: CanonicalApplication) -> FastAPI:
    web = FastAPI(
        title="baqylau",
        openapi_url="/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    web.state.canonical_application = graph
    web.include_router(hooks.router)
    web.include_router(content.router)
    web.include_router(terminal_streams.router)
    web.include_router(panes.router)
    web.include_router(views.router)
    web.include_router(dashboard_streams.router)
    web.include_router(controls.router)
    web.include_router(dashboard_application.router)
    web.include_router(dashboard_application.guarded)
    web.include_router(telemetry.router)
    web.include_router(files.router)
    web.include_router(catalog.router)
    web.include_router(sessions.router)
    web.include_router(static.router)

    @web.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml() -> Response:
        return Response(
            yaml.safe_dump(web.openapi(), sort_keys=False, allow_unicode=True),
            media_type="application/yaml",
        )

    web.add_exception_handler(StarletteHTTPException, _http_error)
    web.add_exception_handler(RequestValidationError, _validation_error)
    web.add_exception_handler(KeyError, _application_input_error)
    web.add_exception_handler(ValueError, _application_input_error)
    web.add_exception_handler(TypeError, _application_input_error)
    web.add_exception_handler(Exception, _internal_error)
    web.add_middleware(_SelectiveGZip)
    return web
