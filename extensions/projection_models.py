# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the fact and generation reads of a projection, and build its captured binding."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.models import events, projections, scopes

if TYPE_CHECKING:
    from extensions.models.interpretation_reads import CanonicalPage
    from extensions.models.scope_relations import ScopedSettings


class ProjectionFacts(Protocol):
    """Read committed facts for one scope after a cursor."""

    def facts_for_scope(
        self, history_revision: str, scope: scopes.ExtensionScope, after_cursor: int, limit: int,
    ) -> CanonicalPage:
        """Read an indexed scope page in accepted order."""
        ...


class GenerationHeads(Protocol):
    """Read each owner's live projection generation."""

    def active_generation(self, owner: str) -> str:
        """Read one owner's active projection generation."""
        ...


class ProcessingPackageIdentity(Protocol):
    """Name one enabled package and its processing revisions."""

    @property
    def extension_id(self) -> str:
        """The package owner."""
        ...

    @property
    def runtime_revision(self) -> str:
        """The active runtime revision."""
        ...

    @property
    def settings(self) -> ScopedSettings:
        """The captured settings selection."""
        ...


def projection_snapshot(
    scope: scopes.ExtensionScope, history_revision: str, generation: str, commit_cursor: int,
) -> scopes.SnapshotCursor:
    """Build the captured projection boundary for one processing batch.

    Returns:
        The complete snapshot cursor.

    """
    return scopes.SnapshotCursor(
        scope=scope,
        history_revision=history_revision,
        projection_generation=generation,
        commit_cursor=commit_cursor,
    )


def projection_binding(
    package: ProcessingPackageIdentity,
    snapshot: scopes.SnapshotCursor,
    input_cursor: int,
) -> projections.ProjectionBinding:
    """Build the exact binding a projector must echo back.

    Returns:
        The complete projection binding.

    """
    return projections.ProjectionBinding(
        context=events.ProcessingContext(
            extension_id=package.extension_id,
            runtime_revision=package.runtime_revision,
            history_revision=snapshot.history_revision,
            scope=snapshot.scope,
            input_cursor=input_cursor,
            settings_revision=package.settings.revision,
            settings=package.settings.for_scope(snapshot.scope),
        ),
        snapshot=snapshot,
        after_input_cursor=snapshot.commit_cursor,
    )
