# Copyright (c) 2026 Zhambyl Yermagambet
"""Give a terminal presenter its inputs: the recorded snapshot, the kept views, and the view query grant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from extensions.presentation_cache import PresentationCache
from extensions.projection_models import GenerationHeads, projection_snapshot
from extensions.query_authority import QueryAuthority

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import ExtensionScope, SnapshotCursor

HISTORY_REVISION = "default"


class CommittedCursors(Protocol):
    """Read an owner's committed projection cursor of one scope."""

    def committed_cursor(self, owner: str, scope: ExtensionScope, history_revision: str, generation: str) -> int:
        """Read the cursor."""
        ...


@dataclass(frozen=True)
class PresentationSnapshots:
    """Show an owner's records at its committed projection cursor in its live generation.

    The same cursor, settings, runtime, size, focus, and view document give the kept view without a
    presenter call. The view query, if any, runs under its own call grant.
    """

    projections: CommittedCursors
    heads: GenerationHeads
    queries: QueryAuthority
    views: PresentationCache = field(default_factory=PresentationCache)

    def snapshot(self, owner: str, scope: ExtensionScope) -> SnapshotCursor:
        """Read the recorded boundary that the view presents.

        Returns:
            The owner's committed projection cursor of the scope.

        """
        generation = self.heads.active_generation(owner)
        cursor = self.projections.committed_cursor(owner, scope, HISTORY_REVISION, generation)
        return projection_snapshot(scope, HISTORY_REVISION, generation, cursor)
