# Copyright (c) 2026 Zhambyl Yermagambet
"""Bound active host scopes and release leases after callback failure."""

from unittest.mock import Mock

import pytest
from baqylau_extension_api.models.scopes import InstallationScope, WorkspaceScope

from extensions.source_scopes import ActiveExtensionScopes

FIRST = WorkspaceScope(workspace_id="first")
SECOND = WorkspaceScope(workspace_id="second")


def test_scope_bound_permits_existing_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full registry permits another owner of an existing scope, but no new scope."""
    monkeypatch.setattr("extensions.source_scopes.MAX_HELD_SCOPES", 1)
    scopes = ActiveExtensionScopes(Mock())
    with scopes.hold_scope(FIRST), scopes.hold_scope(FIRST):
        with pytest.raises(ValueError, match="scope limit"), scopes.hold_scope(SECOND):
            pytest.fail("a distinct scope must not enter a full registry")
        assert set(scopes.source_scopes()) == {InstallationScope(), FIRST}
    assert scopes.source_scopes() == (InstallationScope(),)


def test_failed_notice_releases_the_new_scope() -> None:
    """A failed notification during entry cannot leave a scope with no owner."""
    changed = Mock(side_effect=[RuntimeError("fixture notification failed"), None])
    scopes = ActiveExtensionScopes(changed)
    with pytest.raises(RuntimeError, match="notification"), scopes.hold_scope(FIRST):
        pytest.fail("the failed callback must stop scope entry")
    assert scopes.source_scopes() == (InstallationScope(),)
