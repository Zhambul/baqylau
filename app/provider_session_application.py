# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide per-session application state services."""

from typing import Annotated

from fastapi import Depends

from app import (
    provider_audit_storage as audit_providers,
    provider_control_support as support_providers,
    provider_preference_storage as storage_providers,
    provider_session_storage as session_data_providers,
    session_application_resources as resources,
)
from app.injection import singleton
from app.provider_goal_dismissals import GoalDismissals
from dashboard.services import workspace as workspace_service


@singleton
def session_application_core(
    read_model: session_data_providers.SessionDataStore,
    terminal_input: support_providers.TerminalInput,
    audit_reader: audit_providers.AuditReads,
    workspace_storage: session_data_providers.Workspaces,
    modes: storage_providers.ViewModes,
) -> resources.SessionApplicationCore:
    """Return core resources for session application state.

    Returns:
        Core resources for session application state.

    """
    return resources.SessionApplicationCore(
        read_model,
        terminal_input,
        audit_reader,
        workspace_storage,
        modes,
    )


SessionCore = Annotated[
    resources.SessionApplicationCore,
    Depends(session_application_core),
]


@singleton
def session_application_rules(
    settings: storage_providers.NotificationSettings,
    hidden_tasks: storage_providers.Dismissals,
    hidden_goals: GoalDismissals,
    terminal_gate: support_providers.TerminalGate,
) -> resources.SessionApplicationRules:
    """Return rules for session application state.

    Returns:
        Rules for session application state.

    """
    return resources.SessionApplicationRules(
        settings,
        hidden_tasks,
        hidden_goals,
        terminal_gate,
    )


SessionRules = Annotated[
    resources.SessionApplicationRules,
    Depends(session_application_rules),
]


@singleton
def session_application(
    core: SessionCore,
    rules: SessionRules,
) -> workspace_service.SessionApplicationService:
    """Return the per-session application state service.

    Returns:
        Per-session application state service.

    """
    return workspace_service.SessionApplicationService(
        core,
        rules,
    )


SessionApplication = Annotated[
    workspace_service.SessionApplicationService,
    Depends(session_application),
]
