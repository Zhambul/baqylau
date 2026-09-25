# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the scopes that a session's settings inherit from, most specific first."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.models.scopes import WorkspaceScope

from extensions.models.registry import RuntimeSettings

if TYPE_CHECKING:
    from baqylau_extension_api.models.documents import EncodedDocument
    from baqylau_extension_api.models.scopes import ExtensionScope


class ScopeRelations(Protocol):
    """Read the related scopes of one scope from host data."""

    def related_scopes(self, scope: ExtensionScope) -> tuple[ExtensionScope, ...]:
        """Name the related scopes, most specific first; a scope with no relation has none."""
        ...


class ScopedSettings(Protocol):
    """Read one package's settings revision and the effective document of a scope."""

    @property
    def revision(self) -> int:
        """The owner settings revision."""
        ...

    def for_scope(self, scope: ExtensionScope) -> EncodedDocument | None:
        """Read the effective document of one scope."""
        ...


@dataclass(frozen=True)
class ResolvedSettings(ScopedSettings):
    """Resolve an exact override, then each related scope's override, then the fallback."""

    settings: RuntimeSettings
    relations: ScopeRelations | None = None

    @property
    def revision(self) -> int:
        """The owner settings revision."""
        return self.settings.revision

    def for_scope(self, scope: ExtensionScope) -> EncodedDocument | None:
        """Read the effective document of one scope, with its related scopes.

        Returns:
            The first matching override, or the fallback.

        """
        related = () if self.relations is None else self.relations.related_scopes(scope)
        return self.settings.for_scope(scope, related)


def workspace_scope(project_directory: str) -> WorkspaceScope:
    """Name the workspace of one resolved project directory.

    Returns:
        The workspace scope with a stable digest of the directory.

    """
    return WorkspaceScope(workspace_id=hashlib.sha256(project_directory.encode("utf-8")).hexdigest())
