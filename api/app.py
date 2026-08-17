# api/app.py — the FastAPI application factory.
#
# build_web_application(graph) wires the routers, the error contract, the
# response middleware and the OpenAPI documents around one already-built
# application graph. It builds no graph itself — that is api/server.py
# serve()'s job, exactly once — which is what lets tests hand in a fixture
# graph the same way.
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import anyio.to_thread
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import config
from api.common import content, hooks
from api.common import telemetry as harness_telemetry
from api.dashboard import application as dashboard_application
from api.dashboard import catalog, controls, files, sessions, static, telemetry
from api.dashboard import streams as dashboard_streams
from api.middleware import SecurityHeaders, SelectiveGZip
from api.responses import EVERY_ROUTE
from api.terminal import panes, views
from api.terminal import streams as terminal_streams
from app.bootstrap import CanonicalApplication
from diagnostics import record as A
from domain.errors import ApplicationInputError

# Starlette's stock message for an unrouted path; the daemon contract has always
# said {"error": "not found"} in this server's own casing.
_FRAMEWORK_NOT_FOUND = "Not Found"


@asynccontextmanager
async def _lifespan(_application: FastAPI):
    # Sync route handlers share the anyio worker-thread pool; SSE is async and
    # costs no thread, so this cap only has to absorb request bursts.
    anyio.to_thread.current_default_thread_limiter().total_tokens = config.THREAD_POOL_TOKENS
    yield


def _error_body(message: str, status_code: int) -> JSONResponse:
    """This server's one error shape, and the one place it is built.

    Carries the security headers itself rather than leaving them to
    api/middleware.py, because `_internal_error` below runs inside Starlette's
    ServerErrorMiddleware — which is installed OUTSIDE the user middleware stack
    (a handler registered for `Exception` becomes its `error_handler`), so no
    middleware can wrap it. Same constant, so there is still one policy; the
    stamping middleware only ever fills a header in that is absent.
    """
    return JSONResponse({"error": message}, status_code, headers=config.SECURITY_HEADERS)


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
    catalogue, a malformed reference) — the read/control planes' 400 contract.

    Registered for domain.errors.ApplicationInputError and nothing else. It used
    to be registered for KeyError, ValueError and TypeError, which meant every
    invariant check anywhere below a route — "send_text handler requires
    SendText", a translator disagreeing with itself, a repository refusing a
    limit — was answered as the caller's fault: a 400, no `errors` row, and the
    internal message on the wire. Those now fall through to `_internal_error`,
    which is a 500 and an audit row, and the sites that really did mean "your
    request" say so by type.
    """
    message = error.args[0] if error.args else str(error)
    return _error_body(str(message), 400)


async def _internal_error(request: Request, _error: Exception) -> JSONResponse:
    A.error("", "dashboard %s" % ("POST" if request.method == "POST" else "request"),
            {"path": request.url.path[:200]})
    return _error_body("internal", 500)


def _publish_openapi_without_the_422(web: FastAPI) -> None:
    """Drop FastAPI's automatic 422 from the published schema.

    FastAPI documents a 422 + HTTPValidationError on every route that takes a
    parameter, because that is what it would answer by default. This server never
    does: `_validation_error` above renders a schema rejection as the SAME
    {"error": ...} body at 400 as everything else, and that 400 is declared on
    every route already (api/responses.py EVERY_ROUTE). Leaving the 422 in a
    document we publish would tell a generated client to handle a status it can
    never see, in a body shape this server never sends.

    Replacing `openapi()` is FastAPI's own documented extension point for this.
    """
    generate = web.openapi

    def document() -> dict[str, Any]:
        if web.openapi_schema is None:
            schema = generate()
            for operations in schema.get("paths", {}).values():
                for operation in operations.values():
                    if isinstance(operation, dict):
                        operation.get("responses", {}).pop("422", None)
            schemas = schema.get("components", {}).get("schemas", {})
            schemas.pop("HTTPValidationError", None)
            schemas.pop("ValidationError", None)
            web.openapi_schema = schema
        return web.openapi_schema

    web.openapi = document  # type: ignore[method-assign]


def build_web_application(graph: CanonicalApplication) -> FastAPI:
    web = FastAPI(
        title="baqylau",
        openapi_url="/openapi.json",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
        # Inherited by every route, including the ones added below: the two
        # statuses the handlers above can produce for any request at all.
        responses=EVERY_ROUTE,
    )
    web.state.canonical_application = graph
    web.include_router(hooks.router)
    web.include_router(harness_telemetry.router)
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

    _publish_openapi_without_the_422(web)

    @web.get("/openapi.yaml", include_in_schema=False)
    def openapi_yaml() -> Response:
        return Response(
            yaml.safe_dump(web.openapi(), sort_keys=False, allow_unicode=True),
            media_type="application/yaml",
        )

    # Starlette types the handler argument as taking a bare Exception, but it
    # dispatches on the class registered alongside it — a handler for
    # StarletteHTTPException is only ever CALLED with one. Narrowing the
    # parameter is correct and unrepresentable in that signature; the handlers
    # below take Exception itself and so need no ignore.
    web.add_exception_handler(StarletteHTTPException, _http_error)  # type: ignore[arg-type]
    web.add_exception_handler(RequestValidationError, _validation_error)  # type: ignore[arg-type]
    web.add_exception_handler(ApplicationInputError, _application_input_error)
    web.add_exception_handler(Exception, _internal_error)
    # Added last, so it wraps first: the header policy has to reach the replies
    # the compression layer and the error handlers produce, not just the routes'.
    web.add_middleware(SelectiveGZip)
    web.add_middleware(SecurityHeaders)
    return web
