# Copyright (c) 2026 Zhambyl Yermagambet
"""Store completed-goal visibility."""

from typing import Protocol

from domain.ids import SessionId


class GoalDismissalRepository(Protocol):
    """Keep a dismissal until the goal changes."""

    def hidden(self, session_id: SessionId) -> bool:
        """Return whether the current goal is hidden."""
        ...

    def dismiss(self, session_id: SessionId, objective: str) -> None:
        """Hide the matching completed goal."""
        ...
