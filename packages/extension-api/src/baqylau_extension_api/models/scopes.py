# Copyright (c) 2026 Zhambyl Yermagambet
"""Identify the data scope without requiring a coding session."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Identifier, OpaqueId, Revision, WireModel
from baqylau_extension_api.paths import AbsolutePath


class SessionScope(WireModel):
    """Name a session and the actor that produced an observation."""

    kind: Literal["session"] = "session"
    session_id: OpaqueId
    actor_id: OpaqueId
    harness: OpaqueId


class WorkspaceScope(WireModel):
    """Name a workspace independently of its display path."""

    kind: Literal["workspace"] = "workspace"
    workspace_id: Identifier


class RepositoryScope(WireModel):
    """Name one repository worktree and its resolved Git directory."""

    kind: Literal["repository"] = "repository"
    repository_id: Identifier
    worktree: AbsolutePath
    git_directory: AbsolutePath


class InstallationScope(WireModel):
    """Select data shared by the current host installation."""

    kind: Literal["installation"] = "installation"


ExtensionScope = Annotated[
    SessionScope | WorkspaceScope | RepositoryScope | InstallationScope,
    Field(discriminator="kind"),
]


class SnapshotCursor(WireModel):
    """Select one complete projection boundary."""

    scope: ExtensionScope
    history_revision: Identifier
    projection_generation: Identifier
    commit_cursor: Revision


class EntryPageCursor(WireModel):
    """Resume a page within a commit that produced several entries."""

    snapshot: SnapshotCursor
    entry_position: Revision
