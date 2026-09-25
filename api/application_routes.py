# Copyright (c) 2026 Zhambyl Yermagambet
"""Register application and control API routes."""

from fastapi import FastAPI

from api.application import catalog, files, preferences, static
from api.controls import routes as controls
from api.diagnostics import extension_work_routes as extension_work, routes as diagnostics
from api.extensions import (
    error_handlers,
    routers as extension_routers,
    terminal_action_routes as extension_actions,
    terminal_pane_routes as extension_panes,
    terminal_routes as extension_terminal,
    terminal_section_routes as extension_sections,
    terminal_selector_routes as extension_selector,
    web_routes as extension_web,
)
from api.extensions.repository_scope_routes import router as extension_repository_router
from api.telemetry import browser as browser_telemetry


def configure(web: FastAPI) -> None:
    """Register control and application routes."""
    for router in (
        controls.router, diagnostics.router, extension_work.router, preferences.router, preferences.guarded,
        browser_telemetry.router, files.router, catalog.router, *extension_routers.ROUTERS,
        extension_web.router, extension_terminal.router, extension_panes.router, extension_selector.router,
        extension_sections.router, extension_actions.router, extension_repository_router, static.router,
    ):
        web.include_router(router)
    error_handlers.configure(web)
