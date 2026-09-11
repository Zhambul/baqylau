# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the catalog module."""

# api/application/catalog.py — what the new-session form and the composer menus
# read: the installed harnesses, one harness's catalogue, the insights page,
# and the resumable-session picker.
from __future__ import annotations

from fastapi import APIRouter

from api.application.mapper import catalog as catalog_mapper, insights as insights_mapper, resume as resume_mapper
from api.application.models.harnesses import (
    harness_catalog_response,
    harness_description_response,
)
from api.application.models.insights.application_insights_response import (
    ApplicationInsightsResponse,
)
from api.application.models.resume.resumable_session_response import ResumableSessionResponse
from api.common.models.fields import HarnessNamePath
from app import (
    provider_application_queries as application_queries,
    provider_control_support as control_support,
    provider_harness_registry as harness_registry,
)
from domain.ids import HarnessName, SessionId
from harness.models.catalog import (
    QueryContext,
)
from harness.models.controls import (
    ControlName,
)

router = APIRouter()


@router.get(
    "/api/harnesses",
    response_model=list[harness_description_response.HarnessDescriptionResponse],
)
def harnesses(
    registry: harness_registry.Registry,
) -> list[harness_description_response.HarnessDescriptionResponse]:
    """Return the harnesses.

    Returns:
        Harnesses.

    """
    return [
        harness_description_response.HarnessDescriptionResponse(
            name=plugin.harness_info.name,
            display_name=plugin.harness_info.display_name,
            launchable=plugin.launcher is not None,
            default_for_launch=plugin.harness_info.default_for_launch,
            supports_attachments=plugin.harness_info.supports_attachments,
            control_names=tuple(
                sorted(
                    {
                        *(plugin.controller.handlers if plugin.controller else ()),
                        *(
                            ()
                            if plugin.harness_info.supports_native_automatic_renaming
                            else (ControlName.AUTO_NAME_SESSION,)
                        ),
                    },
                ),
            ),
            supports_accounts=plugin.harness_info.supports_accounts,
            supports_terminal_input=plugin.composer is not None,
            supports_readable_compaction_context=(plugin.harness_info.supports_readable_compaction_context),
            requires_initial_message=plugin.harness_info.requires_initial_message,
        )
        for plugin in registry.plugins()
    ]


@router.get("/api/harnesses/{harness}/catalog")
def catalog(
    harness: HarnessNamePath,
    registry: harness_registry.Registry,
    harnesses_catalog: control_support.Catalog,
    session_id: str | None = None,
    working_directory: str | None = None,
) -> harness_catalog_response.HarnessCatalogResponse:
    """Return the catalog.

    Returns:
        Catalog.

    """
    context = QueryContext(
        session_id=SessionId(session_id) if session_id else None,
        working_directory=working_directory,
    )
    harness_name = HarnessName(harness)
    # The menu payload is composed here, from the two places its parts honestly
    # live: the STATIC vocabulary on the plugin's HarnessInfo (built once, as a
    # literal) and the per-directory part from the catalogue. The contract
    # keeps them apart; this endpoint is where the browser wants them together.
    harness_info = registry.plugin(harness_name).harness_info
    return catalog_mapper.harness_catalog(
        harnesses_catalog.read(harness_name, context),
        harness_info.models,
        harness_info.rewind_modes,
    )


@router.get("/api/insights")
def insights(application_insights: application_queries.Insights) -> ApplicationInsightsResponse:
    """Return the insights.

    Returns:
        Insights.

    """
    return insights_mapper.application_insights(application_insights.snapshot())


@router.get("/api/resumable-sessions")
def resumable_sessions(
    resumable: application_queries.ResumableSessions,
    working_directory: str = "",
    search: str | None = None,
) -> tuple[ResumableSessionResponse, ...]:
    """Return the resumable sessions.

    Returns:
        Resumable sessions.

    """
    return tuple(
        resume_mapper.resumable_session(session) for session in resumable.sessions_for(working_directory, search)
    )
