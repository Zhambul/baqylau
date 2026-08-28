# api/application/catalog.py — what the new-session form and the composer menus
# read: the installed harnesses, one harness's catalogue, the insights page,
# and the resumable-session picker.
from __future__ import annotations

from fastapi import APIRouter

from api.application.models.harnesses.harness_description_response import (
    HarnessDescriptionResponse,
)
from api.application.models.harnesses.harness_catalog_response import HarnessCatalogResponse
from api.common.models.fields import HarnessNamePath
from api.application.mapper import catalog as catalog_mapper
from api.application.mapper import insights as insights_mapper
from api.application.mapper import resume as resume_mapper
from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
)
from api.application.models.resume.resumable_session_response import ResumableSessionResponse
from app.providers import Catalog, Insights, Registry, ResumableSessions
from harness.models import ControlName, QueryContext
from harness.registry import HarnessRegistryError
from domain.ids import HarnessName, SessionId

router = APIRouter()


@router.get("/api/harnesses")
def harnesses(registry: Registry) -> list[HarnessDescriptionResponse]:
    return [
        HarnessDescriptionResponse(
            name=plugin.info.name,
            display_name=plugin.info.display_name,
            launchable=plugin.launcher is not None,
            default_for_launch=plugin.info.default_for_launch,
            supports_attachments=plugin.info.supports_attachments,
            control_names=tuple(sorted({
                *(plugin.controller.handlers if plugin.controller else ()),
                *(() if plugin.info.supports_native_automatic_renaming else (ControlName.AUTO_NAME_SESSION,)),
            })),
            supports_accounts=plugin.info.supports_accounts,
            supports_terminal_input=plugin.composer is not None,
            supports_readable_compaction_context=(
                plugin.info.supports_readable_compaction_context
            ),
            requires_initial_message=plugin.info.requires_initial_message,
        )
        for plugin in registry.plugins()
    ]


@router.get("/api/harnesses/{harness}/catalog")
def catalog(
    harness: HarnessNamePath,
    registry: Registry,
    harnesses_catalog: Catalog,
    session_id: str | None = None,
    working_directory: str | None = None,
) -> HarnessCatalogResponse:
    context = QueryContext(
        session_id=SessionId(session_id) if session_id else None,
        working_directory=working_directory,
    )
    try:
        harness_name = HarnessName(harness)
    except ValueError as error:
        raise HarnessRegistryError(f"unknown harness: {harness}") from error
    # The menu payload is composed here, from the two places its parts honestly
    # live: the STATIC vocabulary on the plugin's HarnessInfo (built once, as a
    # literal) and the per-directory part from the catalogue. The contract
    # keeps them apart; this endpoint is where the browser wants them together.
    info = registry.plugin(harness_name).info
    return catalog_mapper.harness_catalog(
        harnesses_catalog.read(harness_name, context), info.models, info.rewind_modes
    )


@router.get("/api/insights")
def insights(application_insights: Insights) -> ApplicationInsightsResponse:
    return insights_mapper.application_insights(application_insights.snapshot())


@router.get("/api/resumable-sessions")
def resumable_sessions(
    resumable: ResumableSessions,
    working_directory: str = "",
    search: str | None = None,
) -> tuple[ResumableSessionResponse, ...]:
    return tuple(
        resume_mapper.resumable_session(session)
        for session in resumable.sessions_for(working_directory, search)
    )
