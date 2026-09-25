# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve a session's settings through its related workspace, most specific scope first (P05-T05)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import InstallationScope, SessionScope, SnapshotCursor

from extensions.models.registry import RuntimeSettings, ScopedRuntimeSettings
from extensions.models.scope_relations import ResolvedSettings, workspace_scope
from extensions.projection_models import projection_binding
from repository.impl.sqlite.scope_relations import SqliteScopeRelations
from tests.extension_api import service_samples

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import ExtensionScope

    from repository.impl.sqlite.connection import SqliteDatabase

OWNER = service_samples.ALPHA
DIRECTORY = "/work/project"
SESSION = SessionScope(session_id="session-one", actor_id="lead", harness="claude")
WORKSPACE = workspace_scope(DIRECTORY)


def document(text: str) -> EncodedDocument:
    """Encode one fixture setting value.

    Returns:
        The document in the fixture's settings schema.

    """
    return EncodedDocument(schema_ref=service_samples.schema(OWNER).reference, json_text=f'"{text}"')


FALLBACK = document("installation")
WORKSPACE_VALUE = document("workspace")
SETTINGS = RuntimeSettings(revision=3, default=FALLBACK, scopes=(
    ScopedRuntimeSettings(scope=WORKSPACE, settings=WORKSPACE_VALUE),
))


@dataclass(frozen=True)
class FixedRelations:
    """Relate the fixture session to the fixture workspace."""

    def related_scopes(self, scope: ExtensionScope) -> tuple[ExtensionScope, ...]:
        """Name the workspace of the fixture session.

        Returns:
            The workspace for the session, or nothing.

        """
        return (WORKSPACE,) if scope == SESSION else ()


@dataclass(frozen=True)
class PackageIdentity:
    """Name the fixture package, its runtime, and its resolving settings."""

    extension_id: str
    runtime_revision: str
    settings: ResolvedSettings


def test_exact_then_related_then_fallback() -> None:
    """An exact override wins, then a related override, then the fallback."""
    exact = SETTINGS.model_copy(update={"scopes": (
        *SETTINGS.scopes, ScopedRuntimeSettings(scope=SESSION, settings=document("session")),
    )})

    assert exact.for_scope(SESSION, (WORKSPACE,)) == document("session")
    assert SETTINGS.for_scope(SESSION, (WORKSPACE,)) == WORKSPACE_VALUE
    assert SETTINGS.for_scope(SESSION) == FALLBACK
    assert SETTINGS.for_scope(InstallationScope(), (WORKSPACE,)) == WORKSPACE_VALUE


def test_session_relates_to_its_project_directory(main: SqliteDatabase) -> None:
    """A stored session names the workspace of its project directory; another scope has no relation."""
    with main.write() as connection:
        connection.execute(
            "INSERT INTO sessions(session_id, lead_actor_id, harness, harness_session_id, source_reference, "
            "created_at, project_directory) VALUES(?, 'lead', 'claude', ?, 'fixture', 1.0, ?)",
            (SESSION.session_id, SESSION.session_id, DIRECTORY),
        )
    relations = SqliteScopeRelations(main)

    assert relations.related_scopes(SESSION) == (WORKSPACE,)
    assert not relations.related_scopes(InstallationScope())
    assert not relations.related_scopes(SESSION.model_copy(update={"session_id": "unknown"}))


def test_projection_uses_the_workspace_value() -> None:
    """A projector in a session scope receives its workspace's override."""
    identity = PackageIdentity(OWNER, "runtime-one", ResolvedSettings(SETTINGS, FixedRelations()))
    snapshot = SnapshotCursor(
        scope=SESSION, history_revision="default", projection_generation="default", commit_cursor=0,
    )

    binding = projection_binding(identity, snapshot, 1)

    assert binding.context.settings == WORKSPACE_VALUE
    assert binding.context.settings_revision == SETTINGS.revision
