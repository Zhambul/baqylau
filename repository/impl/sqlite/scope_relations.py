# Copyright (c) 2026 Zhambyl Yermagambet
"""Relate a session scope to the workspace of its project directory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import SessionScope

from extensions.models.scope_relations import workspace_scope
from repository.impl.sqlite import connection

if TYPE_CHECKING:
    import sqlite3

    from baqylau_extension_api.models.scopes import ExtensionScope


def related_scopes(connection_handle: sqlite3.Connection, scope: ExtensionScope) -> tuple[ExtensionScope, ...]:
    """Name the related scopes of one scope in the caller's transaction.

    A session relates to the workspace of its stored project directory. The
    repository relation comes with the Git package, before the workspace.

    Returns:
        The related scopes, most specific first.

    """
    if not isinstance(scope, SessionScope):
        return ()
    row = connection_handle.execute(
        "SELECT project_directory FROM sessions WHERE session_id=?", (scope.session_id,),
    ).fetchone()
    if row is None or row["project_directory"] is None:
        return ()
    return (workspace_scope(row["project_directory"]),)


@dataclass(frozen=True)
class SqliteScopeRelations:
    """Read scope relations from the session rows of the main database."""

    database: connection.SqliteDatabase

    def related_scopes(self, scope: ExtensionScope) -> tuple[ExtensionScope, ...]:
        """Name the related scopes of one scope.

        Returns:
            The related scopes, most specific first.

        """
        with self.database.read() as connection_handle:
            return related_scopes(connection_handle, scope)
