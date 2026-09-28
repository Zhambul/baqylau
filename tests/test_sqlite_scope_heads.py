# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the scopes with facts after one consumer's own cursor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope

from repository.contract.extension_observers import ObserverCursor
from repository.contract.pending_scope_query import PendingScopeQuery
from repository.impl.sqlite.extension_observers import SqliteExtensionObserverRepository
from tests import sqlite_test_fixtures as core
from tests.extension_host import canonical_history_fixture as history

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

OWNER = "test.observer"
DEFAULT = "default"
SESSION_KINDS = frozenset(("session",))
INSTALLATION_KINDS = frozenset(("installation",))


def pending(database: SqliteDatabase, kinds: frozenset[str] = SESSION_KINDS) -> tuple[ExtensionScope, ...]:
    """Read the pending default-history scopes for the test owner.

    Returns:
        The pending scopes.

    """
    return SqliteExtensionObserverRepository(database).pending_scopes(PendingScopeQuery(
        owner=OWNER, scope_kinds=kinds, history_revision=DEFAULT, generation=DEFAULT, limit=10,
    ))


def advance(database: SqliteDatabase, scope: ExtensionScope, commit_cursor: int) -> None:
    """Move the test owner's cursor for one scope."""
    SqliteExtensionObserverRepository(database).advance(ObserverCursor(
        owner=OWNER, scope=scope, history_revision=DEFAULT, generation=DEFAULT, commit_cursor=commit_cursor,
    ))


def test_scope_is_pending_until_the_head(main: SqliteDatabase) -> None:
    """The consumer sees a scope after its own cursor, not after another consumer's progress."""
    first = history.insert_core(main, core.a_started_event("started-one"), DEFAULT)

    scope = pending(main)[0]
    advance(main, scope, first)

    assert pending(main) == ()
    history.insert_core(main, core.a_started_event("started-two"), DEFAULT)
    assert pending(main) == (scope,)


def test_only_declared_scope_kinds_are_pending(main: SqliteDatabase) -> None:
    """A consumer that declares installation scope does not see session scopes."""
    history.insert_core(main, core.a_started_event("started-one"), DEFAULT)
    history.insert_extension(main, "extension-one")

    assert pending(main, INSTALLATION_KINDS) == (InstallationScope(),)
    assert all(scope.kind == "session" for scope in pending(main))
    assert pending(main, frozenset()) == ()


def test_candidate_fact_is_not_pending_in_default(main: SqliteDatabase) -> None:
    """A fact in another history does not make a default-history scope pending."""
    history.insert_core(main, core.a_started_event("candidate-only"))

    assert pending(main) == ()


def test_first_live_pass_starts_at_the_head(main: SqliteDatabase) -> None:
    """A consumer enabled after a fact does not see that fact; it sees the facts after its floor."""
    history.insert_core(main, core.a_started_event("before-enable"), DEFAULT)
    SqliteExtensionObserverRepository(main).ensure_floor(OWNER, DEFAULT, DEFAULT)

    assert pending(main) == ()
    history.insert_core(main, core.a_started_event("after-enable"), DEFAULT)
    assert len(pending(main)) == 1
