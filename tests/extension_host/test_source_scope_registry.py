# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain exact host scopes without adding sessions or idle wakeups."""

from unittest.mock import Mock

import pytest
from baqylau_extension_api.models.scopes import InstallationScope, RepositoryScope, SessionScope

from extensions.source_scopes import ActiveExtensionScopes

REPOSITORY = RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git")


def test_installation_scope_is_always_active() -> None:
    """An installation view does not add a redundant scope or wake the source loop."""
    changed = Mock()
    registry = ActiveExtensionScopes(changed)
    with registry.hold_scope(InstallationScope()):
        assert registry.source_scopes() == (InstallationScope(),)
    changed.assert_not_called()


def test_scope_owners_share_one_selection() -> None:
    """Nested owners add one source scope and only the last release removes it."""
    changed = Mock()
    registry = ActiveExtensionScopes(changed)
    with registry.hold_scope(REPOSITORY):
        with registry.hold_scope(REPOSITORY):
            assert REPOSITORY in registry.source_scopes()
            changed.assert_called_once()
        assert REPOSITORY in registry.source_scopes()
        changed.assert_called_once()
    assert registry.source_scopes() == (InstallationScope(),)


def test_exception_releases_owned_scope() -> None:
    """An interrupted host view releases its source scope without a special close request.

    Raises:
        RuntimeError: Inside the expected failure context only.

    """
    registry = ActiveExtensionScopes(Mock())
    message = "fixture view failed"
    with pytest.raises(RuntimeError, match="fixture"), registry.hold_scope(REPOSITORY):
        raise RuntimeError(message)
    assert registry.source_scopes() == (InstallationScope(),)


@pytest.mark.parametrize("foreign", [
    REPOSITORY.model_copy(update={"worktree": "/other"}),
    REPOSITORY.model_copy(update={"git_directory": "/other/.git"}),
])
def test_repository_scope_uses_both_paths(foreign: RepositoryScope) -> None:
    """Matching repository labels cannot combine distinct worktrees or Git directories."""
    registry = ActiveExtensionScopes(Mock())
    with registry.hold_scope(REPOSITORY), registry.hold_scope(foreign):
        assert set(registry.source_scopes()) == {InstallationScope(), REPOSITORY, foreign}


def test_actor_scopes_keep_their_full_identity() -> None:
    """Two actors in one session retain independent source lifetimes."""
    first = SessionScope(session_id="session", actor_id="first", harness="test")
    second = first.model_copy(update={"actor_id": "second"})
    registry = ActiveExtensionScopes(Mock())
    with registry.hold_scope(first), registry.hold_scope(second):
        assert set(registry.source_scopes()) == {InstallationScope(), first, second}
