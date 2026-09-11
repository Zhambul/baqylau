# Copyright (c) 2026 Zhambyl Yermagambet
"""Group dependencies for session application state."""

from dataclasses import dataclass
from typing import Protocol

from domain.ids import SessionId
from harness.models.probe import (
    TerminalSessionState,
)
from harness.services.terminal_gate import SessionTerminalGate
from repository.contract import audit, preferences, session_data, workspace
from repository.contract.goal_dismissals import GoalDismissalRepository


class TerminalSessionReader(Protocol):
    """Read the terminal state for one session."""

    def state(self, session_id: SessionId) -> TerminalSessionState:
        """Return the terminal state."""
        ...


@dataclass(frozen=True)
class SessionApplicationCore:
    """Hold session readers and workspace storage."""

    session_data_repository: session_data.SessionDataRepository
    terminal_input_service: TerminalSessionReader
    audit_read_repository: audit.AuditReadRepository
    workspace_repository: workspace.SessionWorkspaceRepository
    view_mode_repository: preferences.ViewModeRepository


@dataclass(frozen=True)
class SessionApplicationRules:
    """Hold settings and gates for session application state."""

    notification_setting_repository: preferences.NotificationSettingRepository
    task_dismissal_repository: preferences.TaskDismissalRepository
    goal_dismissal_repository: GoalDismissalRepository
    terminal_gate: SessionTerminalGate
