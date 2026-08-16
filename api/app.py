# api/app.py — the FastAPI application factory.
#
# build_web_application(graph) wires the routers, the error contract, and the
# compression policy around one already-built application graph. It builds no
# graph itself — that is api/server.py serve()'s job, exactly once — which is
# what lets tests hand in a fixture graph the same way.
from __future__ import annotations

from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from api import config
from api.routes import application as application_routes
from api.routes import control, evidence, files, read, static, streams
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
    catalogue, a malformed reference) — the read routes' 400 contract."""
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
    web = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, lifespan=_lifespan)
    web.state.canonical_application = graph
    web.include_router(evidence.router)
    web.include_router(streams.router)
    web.include_router(control.router)
    web.include_router(application_routes.router)
    web.include_router(application_routes.guarded)
    web.include_router(files.router)
    web.include_router(read.router)
    web.include_router(static.router)
    web.add_exception_handler(StarletteHTTPException, _http_error)
    web.add_exception_handler(RequestValidationError, _validation_error)
    web.add_exception_handler(KeyError, _application_input_error)
    web.add_exception_handler(ValueError, _application_input_error)
    web.add_exception_handler(TypeError, _application_input_error)
    web.add_exception_handler(Exception, _internal_error)
    web.add_middleware(_SelectiveGZip)
    return web
