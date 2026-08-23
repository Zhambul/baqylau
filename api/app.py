# api/app.py — the FastAPI application factory.
#
# build_web_application() wires the routers, the error contract, the response
# middleware and the OpenAPI documents around one SINGLETON SCOPE: the registry
# every provider memoises into (app/injection.py). It builds no service itself —
# a node is built the first time something asks for it, by the framework — which
# is what lets the daemon and a test share one set of definitions and disagree
# only about the registry they hand in.
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio.to_thread
import yaml
from fastapi import FastAPI, Request, Response
from pydantic import JsonValue
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import config
from api.application import catalog, files, preferences, static
from api.common import health
from api.controls import routes as controls
from api.diagnostics import routes as diagnostics
from api.hooks import routes as hooks
from api.telemetry import browser as browser_telemetry
from api.telemetry import harness as harness_telemetry
from api import dependencies
from api.lifecycle import background_workers
from api.middleware import SecurityHeaders, SelectiveGZip
from api.common.models.replies.error_response import ErrorResponse
from api.responses import EVERY_ROUTE
from api.sessiondata import routes as session_data_routes
from api.sessiondata import streams as session_data_streams
from api.terminal import panes
from app import providers
from app.injection import Instances, registry, resolve
from domain.errors import ApplicationInputError

# Starlette's stock message for an unrouted path; the daemon contract has always
# said {"error": "not found"} in this server's own casing.
_FRAMEWORK_NOT_FOUND = "Not Found"


@asynccontextmanager
async def _lifespan(web: FastAPI) -> AsyncIterator[None]:
    # Sync route handlers share the anyio worker-thread pool; SSE is async and
    # costs no thread, so this cap only has to absorb request bursts.
    policy = resolve(web.state.instances, dependencies.policy)
    anyio.to_thread.current_default_thread_limiter().total_tokens = policy.thread_pool_tokens
    if not web.state.run_background_workers:
        # An app that only serves requests — the test fixture, a schema dump.
        # The flag is the seam: interpreting and notifying are the DAEMON's
        # work, and every HTTP test would otherwise run an interpreter loop.
        yield
        return
    with background_workers(web.state.instances):
        yield


def _error_body(message: str, status_code: int) -> Response:
    """This server's one error shape, and the one place it is built.

    Built from ErrorResponse — the model every route publishes as its 400 and
    500 — so the declared shape and the emitted bytes cannot drift. An exception
    handler is not a route, so nothing serializes this one for us.

    Carries the security headers itself rather than leaving them to
    api/middleware.py, because `_internal_error` below runs inside Starlette's
    ServerErrorMiddleware — which is installed OUTSIDE the user middleware stack
    (a handler registered for `Exception` becomes its `error_handler`), so no
    middleware can wrap it. Same constant, so there is still one policy; the
    stamping middleware only ever fills a header in that is absent.
    """
    return Response(
        ErrorResponse(error=message).model_dump_json(),
        status_code,
        headers=config.SECURITY_HEADERS,
        media_type="application/json",
    )


async def _http_error(_request: Request, error: StarletteHTTPException) -> Response:
    message = str(error.detail)
    if message == _FRAMEWORK_NOT_FOUND:
        message = "not found"
    return _error_body(message, error.status_code)


async def _validation_error(_request: Request, error: RequestValidationError) -> Response:
    """A schema rejection as this server's one error shape: 400 with the first
    failing field named — {"error": "text: Input should be a valid string"}."""
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"] if part != "body")
    message = f"{location}: {first['msg']}" if location else str(first["msg"])
    return _error_body(message, 400)


async def _application_input_error(_request: Request, error: Exception) -> Response:
    """Bad application input raised past a route (unknown session, unknown
    catalogue, a malformed reference) — the read/control planes' 400 contract.

    Registered for domain.errors.ApplicationInputError and nothing else. It used
    to be registered for KeyError, ValueError and TypeError, which meant every
    invariant check anywhere below a route — "send_text handler requires
    SendText", a translator disagreeing with itself, a repository refusing a
    limit — was answered as the caller's fault: a 400, no `errors` row, and the
    internal message at the HTTP boundary. Those now fall through to `_internal_error`,
    which is a 500 and an audit row, and the sites that really did mean "your
    request" say so by type.
    """
    message = error.args[0] if error.args else str(error)
    return _error_body(str(message), 400)


async def _internal_error(request: Request, _error: Exception) -> Response:
    # An exception handler is not a route: it takes no dependencies. It has the
    # request, though, and the request has the application — so the recorder is
    # resolved from the same registry a route would have been handed it from.
    audit = resolve(request.app.state.instances, providers.recorder)
    audit.error("", "dashboard %s" % ("POST" if request.method == "POST" else "request"),
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

    def document() -> dict[str, JsonValue]:
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


def build_web_application(
    instances: Instances | None = None, run_background_workers: bool = False
) -> FastAPI:
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
    # The singleton scope every provider memoises into. Handed in by the daemon
    # so its background threads share the services the routes hold; a fresh one
    # per application otherwise, so nothing outlives the app that owns it.
    web.state.instances = registry() if instances is None else instances
    web.state.run_background_workers = run_background_workers
    web.include_router(health.router)
    web.include_router(hooks.router)
    web.include_router(harness_telemetry.router)
    web.include_router(panes.router)
    # The read surface: three GETs and two streams, over the read model only.
    # The streams go FIRST, deliberately: `/sessionData/stream` and
    # `/sessionData/{session_id}` both match the same path, and the first router
    # registered wins.
    web.include_router(session_data_streams.router)
    web.include_router(session_data_routes.router)
    web.include_router(controls.router)
    web.include_router(diagnostics.router)
    web.include_router(preferences.router)
    web.include_router(preferences.guarded)
    web.include_router(browser_telemetry.router)
    web.include_router(files.router)
    web.include_router(catalog.router)
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
    policy = resolve(web.state.instances, dependencies.policy)
    web.add_middleware(SelectiveGZip, minimum_size=policy.gzip_minimum_bytes)
    web.add_middleware(SecurityHeaders, headers=policy.security_headers)
    return web
