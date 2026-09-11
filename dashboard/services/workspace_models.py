# Copyright (c) 2026 Zhambyl Yermagambet
"""Value objects returned by the session application service."""

from dataclasses import dataclass

from audit.records import ApplicationError
from domain import composer, dialogs, preferences
from harness.models.probe import TerminalSessionState


@dataclass(frozen=True)
class SessionPreferences:
    """Represent session preferences."""

    view_mode: preferences.ViewMode
    notifications_muted: bool
    tasks_hidden: bool
    goal_hidden: bool = False


@dataclass(frozen=True)
class SessionApplicationSnapshot:
    """Represent one complete session application snapshot."""

    preferences: SessionPreferences
    composer: composer.ComposerState
    dialog: dialogs.DialogState
    terminal: TerminalSessionState
    errors: tuple[ApplicationError, ...]
