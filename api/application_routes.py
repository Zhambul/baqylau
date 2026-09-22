# Copyright (c) 2026 Zhambyl Yermagambet
"""Register application and control API routes."""

from fastapi import FastAPI

from api.application import catalog, files, preferences, static
from api.controls import routes as controls
from api.diagnostics import routes as diagnostics
from api.extensions import (
    change_routes,
    command_routes,
    error_handlers,
    job_routes,
    lifecycle_routes,
    query_routes,
    records_routes,
    routes as extensions,
    settings_routes,
)
from api.telemetry import browser as browser_telemetry


def configure(web: FastAPI) -> None:
    """Register control and application routes."""
    for router in (
        controls.router, diagnostics.router, preferences.router, preferences.guarded,
        browser_telemetry.router, files.router, catalog.router, extensions.router, lifecycle_routes.router,
        settings_routes.router, records_routes.router, change_routes.router, job_routes.router,
        command_routes.router, query_routes.router, static.router,
    ):
        web.include_router(router)
    error_handlers.configure(web)
