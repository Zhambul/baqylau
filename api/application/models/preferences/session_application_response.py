# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the session application response module."""

# What YOU have on one session: the density you chose, the message you are
# still typing, the ones you queued behind it, the option you highlighted in a
# dialog — plus the errors the daemon swallowed while working on it.
from pydantic import BaseModel

from api.application.models.preferences.composer_state_response import ComposerStateResponse
from api.application.models.preferences.dialog_state_response import DialogStateResponse
from api.common.models.values.terminal_state import TerminalStateResponse
from domain.preferences import ViewMode


class SessionPreferencesResponse(BaseModel):
    """Represent session preferences response."""

    view_mode: ViewMode
    notifications_muted: bool
    tasks_hidden: bool
    goal_hidden: bool


class ApplicationErrorResponse(BaseModel):
    """Represent application error response."""

    error_id: int
    timestamp: float
    component: str
    action: str
    traceback: str
    context: str


class SessionApplicationResponse(BaseModel):
    """Represent session application response."""

    preferences: SessionPreferencesResponse
    composer: ComposerStateResponse
    dialog: DialogStateResponse
    terminal: TerminalStateResponse
    errors: tuple[ApplicationErrorResponse, ...]
