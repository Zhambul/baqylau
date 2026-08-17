# api/dashboard/catalog.py — what the new-session form and the composer menus
# read: the installed harnesses, one harness's catalogue, the insights page,
# and the resumable-session picker.
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.dashboard.models.harnesses.harness_description_response import (
    HarnessDescriptionResponse,
)
from api.dashboard.models.harnesses.harness_catalog_response import HarnessCatalogResponse
from api.common.models.fields import HarnessNamePath
from api.dependencies import ApplicationGraph
from app.services.insights import ApplicationInsights
from app.services.resume import ResumableSession
from harness.models import QueryContext
from dashboard.render.serialize import json_ready
from domain.ids import SessionId

router = APIRouter()


@router.get("/api/harnesses")
def harnesses(application: ApplicationGraph) -> list[HarnessDescriptionResponse]:
    return [
        HarnessDescriptionResponse(
            name=plugin.info.name,
            display_name=plugin.info.display_name,
            launchable=plugin.launcher is not None,
            default_for_launch=plugin.info.default_for_launch,
            supports_attachments=plugin.info.supports_attachments,
            control_names=(
                tuple(sorted(plugin.controller.handlers)) if plugin.controller else ()
            ),
            supports_accounts=plugin.info.supports_accounts,
            supports_terminal_input=plugin.terminal_probe is not None,
            requires_initial_message=plugin.info.requires_initial_message,
        )
        for plugin in application.registry.plugins()
    ]


@router.get("/api/harnesses/{harness}/catalog", response_model=HarnessCatalogResponse)
def catalog(
    harness: HarnessNamePath,
    application: ApplicationGraph,
    session_id: str | None = None,
    working_directory: str | None = None,
) -> JSONResponse:
    context = QueryContext(
        session_id=SessionId(session_id) if session_id else None,
        working_directory=working_directory,
    )
    # The menu payload is composed here, from the two places its parts honestly
    # live: the STATIC vocabulary on the plugin's HarnessInfo (built once, as a
    # literal) and the per-directory part from the catalogue. The contract
    # keeps them apart; this endpoint is where the browser wants them together.
    info = application.registry.plugin(harness).info
    payload = json_ready(application.catalog.read(harness, context))
    payload["models"] = json_ready(info.models)
    payload["rewind_modes"] = json_ready(info.rewind_modes)
    return JSONResponse(payload)


@router.get("/api/insights", response_model=ApplicationInsights)
def insights(application: ApplicationGraph) -> JSONResponse:
    return JSONResponse(json_ready(application.insights.snapshot()))


@router.get("/api/resumable-sessions", response_model=list[ResumableSession])
def resumable_sessions(
    application: ApplicationGraph,
    working_directory: str = "",
    search: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        json_ready(application.resumable_sessions.sessions_for(working_directory, search))
    )
