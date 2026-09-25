# Copyright (c) 2026 Zhambyl Yermagambet
"""A web view reads its session's workspace from the relation that settings resolution uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import SessionScope
from fastapi import Response

from api.extensions.web_routes import extension_related_scopes
from extensions.models.scope_relations import workspace_scope

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import ExtensionScope

SESSION = SessionScope(session_id="session-one", actor_id="lead", harness="claude")
WORKSPACE = workspace_scope("/work/project")


@dataclass(frozen=True)
class FixedRelations:
    """Relate the fixture session to the fixture workspace."""

    def related_scopes(self, scope: ExtensionScope) -> tuple[ExtensionScope, ...]:
        """Name the workspace of the fixture session.

        Returns:
            The workspace for the session, or nothing.

        """
        return (WORKSPACE,) if scope == SESSION else ()


def test_session_names_its_workspace() -> None:
    """The route answers the session's workspace and is never cached."""
    response = Response()

    related = extension_related_scopes(FixedRelations(), response, SESSION.model_dump_json())

    assert related.scopes == (WORKSPACE,)
    assert response.headers["Cache-Control"] == "no-store"


def test_absent_scope_selects_the_installation() -> None:
    """An absent scope is the installation, which relates to nothing."""
    assert not extension_related_scopes(FixedRelations(), Response()).scopes
