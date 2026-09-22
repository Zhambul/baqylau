# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit one extension projection and read its cursor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from baqylau_extension_api.models.scopes import ExtensionScope

from domain.ids import SessionId
from repository.contract.session_data import SessionDataChanges


@dataclass(frozen=True)
class ProjectionCommit:
    """Keep one checked projection result and its boundary together."""

    owner: str
    scope: ExtensionScope
    history_revision: str
    generation: str
    commit_cursor: int
    changes: SessionDataChanges
    session_id: SessionId | None = None


class ExtensionProjectionRepository(Protocol):
    """Own projection cursors and the derived-data commit."""

    def committed_cursor(self, owner: str, scope: ExtensionScope, history_revision: str, generation: str) -> int:
        """Return the last committed projection cursor, or zero."""
        ...

    def scopes_after(
        self, history_revision: str, after_cursor: int, limit: int,
    ) -> tuple[ExtensionScope, ...]:
        """Read the distinct scopes with accepted facts after a cursor."""
        ...

    def apply_projection(self, projection_commit: ProjectionCommit) -> None:
        """Write one projection's entries, records, and cursor in one transaction."""
        ...
