# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the app module."""

# api/app.py — the FastAPI application factory.
#
# build_web_application() wires the routers, the error contract, the response
# middleware and the OpenAPI documents around one SINGLETON SCOPE: the registry
# every provider memoises into (app/injection.py). It builds no service itself —
# a node is built the first time something asks for it, by the framework — which
# is what lets the daemon and a test share one set of definitions and disagree
# only about the registry they hand in.
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from anyio import to_thread as anyio_thread
from fastapi import FastAPI

from api import application_routes, dependencies, error_responses, observation_routes, openapi_document
from api.middleware import SecurityHeaders, SelectiveGZip
from api.responses import EVERY_ROUTE
from api.workers import background_workers
from app import provider_databases, provider_extensions
from app.injection import Instances, registry, resolve

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def _lifespan(web: FastAPI) -> AsyncIterator[None]:
    # Sync route handlers share the anyio worker-thread pool; SSE is async and
    # costs no thread, so this cap only has to absorb request bursts.
    policy = resolve(web.state.instances, dependencies.policy)
    anyio_thread.current_default_thread_limiter().total_tokens = policy.thread_pool_tokens
    # Finish both schemas before the socket is advertised as healthy. The
    # audit read handle is deliberately read-only; if its first request races
    # the writer's first connection, it can otherwise observe the newly-created
    # file before CREATE TABLE has committed.
    resolve(web.state.instances, provider_databases.main_db).initialize()
    resolve(web.state.instances, provider_databases.audit_db).initialize()
    catalog = resolve(web.state.instances, provider_extensions.extension_catalog)
    await anyio_thread.run_sync(catalog.rescan_packages, catalog.catalog_snapshot().revision)
    if not web.state.run_background_workers:
        # An app that only serves requests — the test fixture, a schema dump.
        # The flag is the seam: interpreting and notifying are the DAEMON's
        # work, and every HTTP test would otherwise run an interpreter loop.
        yield
        return
    with background_workers(web.state.instances):
        yield


def _register_middleware(web: FastAPI) -> None:
    """Register response middleware in wrapper order."""
    policy = resolve(web.state.instances, dependencies.policy)
    web.add_middleware(SelectiveGZip, minimum_size=policy.gzip_minimum_bytes)
    web.add_middleware(SecurityHeaders, headers=policy.security_headers)


def build_web_application(
    instances: Instances | None = None,
    *,
    run_background_workers: bool = False,
) -> FastAPI:
    """Build web application.

    Returns:
        The fast api.

    """
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
    # The read surface: the global stream also carries application state.
    # The streams go FIRST, deliberately: `/sessionData/stream` and
    # `/sessionData/{session_id}` both match the same path, and the first router
    # registered wins.
    observation_routes.configure(web)
    application_routes.configure(web)
    openapi_document.configure(web)
    error_responses.configure(web)
    # Added last, so it wraps first: the header policy has to reach the replies
    # the compression layer and the error handlers produce, not just the routes'.
    _register_middleware(web)
    return web
