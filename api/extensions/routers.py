# Copyright (c) 2026 Zhambyl Yermagambet
"""Collect the extension API routers in their registration order."""

from api.extensions.change_routes import router as change_router
from api.extensions.command_routes import router as command_router
from api.extensions.generation_routes import router as generation_router
from api.extensions.history_routes import router as history_router
from api.extensions.job_control_routes import router as job_control_router
from api.extensions.job_routes import router as job_router
from api.extensions.lifecycle_routes import router as lifecycle_router
from api.extensions.query_routes import router as query_router
from api.extensions.records_routes import router as records_router
from api.extensions.routes import router as catalog_router
from api.extensions.secret_routes import router as secret_router
from api.extensions.settings_routes import router as settings_router

ROUTERS = (
    catalog_router,
    lifecycle_router,
    settings_router,
    secret_router,
    records_router,
    change_router,
    job_router,
    job_control_router,
    command_router,
    query_router,
    generation_router,
    history_router,
)
