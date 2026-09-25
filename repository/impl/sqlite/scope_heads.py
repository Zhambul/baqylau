# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the scopes whose canonical head is after one consumer's own cursor."""

import sqlite3
import time
from enum import StrEnum

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from repository.contract.pending_scope_query import PendingScopeQuery


class ConsumerCursorTable(StrEnum):
    """Name the fixed cursor table of each fact consumer."""

    PROJECTION = "extension_projection_cursors"
    OBSERVER = "extension_observer_cursors"

    @property
    def consumer(self) -> str:
        """The consumer name that its floor rows use."""
        return "projection" if self is ConsumerCursorTable.PROJECTION else "observer"


_SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
_KINDS_ADAPTER: TypeAdapter[list[str]] = TypeAdapter(list[str])
_PENDING_SQL = (
    "SELECT heads.scope FROM canonical_scope_heads AS heads "
    "LEFT JOIN {table} AS cursors ON cursors.owner=? AND cursors.scope=heads.scope "
    "AND cursors.history_revision=heads.history_revision AND cursors.generation=? "
    "LEFT JOIN extension_consumer_floors AS floors ON floors.consumer=? AND floors.owner=? "
    "AND floors.history_revision=heads.history_revision AND floors.generation=? "
    "WHERE heads.history_revision=? AND heads.scope_kind IN (SELECT value FROM json_each(?)) "
    "AND heads.head_cursor > COALESCE(cursors.commit_cursor, floors.floor_cursor, 0) "
    "ORDER BY heads.head_cursor, heads.scope LIMIT ?"
)


def pending_scopes(
    connection_handle: sqlite3.Connection,
    consumer_cursor_table: ConsumerCursorTable,
    pending_scope_query: PendingScopeQuery,
) -> tuple[ExtensionScope, ...]:
    """Read the declared scopes with facts after the consumer's cursor, oldest head first.

    Returns:
        At most the requested number of scopes.

    """
    if not pending_scope_query.scope_kinds:
        return ()
    rows = connection_handle.execute(_PENDING_SQL.format(table=consumer_cursor_table), (
        pending_scope_query.owner,
        pending_scope_query.generation,
        consumer_cursor_table.consumer,
        pending_scope_query.owner,
        pending_scope_query.generation,
        pending_scope_query.history_revision,
        _KINDS_ADAPTER.dump_json(sorted(pending_scope_query.scope_kinds)).decode(),
        pending_scope_query.limit,
    )).fetchall()
    return tuple(_SCOPE_ADAPTER.validate_json(row["scope"]) for row in rows)


def consumer_cursor(
    connection_handle: sqlite3.Connection, consumer_cursor_table: ConsumerCursorTable, cursor_key: tuple[str, ...],
) -> int:
    """Read one consumer's cursor for one scope, or its floor, or zero.

    The key is owner, scope, history revision, and generation.

    Returns:
        The stored cursor, the owner's floor before its first cursor, or zero.

    """
    row = connection_handle.execute(
        f"SELECT commit_cursor FROM {consumer_cursor_table} "  # noqa: S608 -- Fixed consumer table.
        "WHERE owner=? AND scope=? AND history_revision=? AND generation=?",
        cursor_key,
    ).fetchone()
    if row is not None:
        return int(row["commit_cursor"])
    owner, _scope, history_revision, generation = cursor_key
    return _floor(connection_handle, consumer_cursor_table, (owner, history_revision, generation))


def ensure_floor(
    connection_handle: sqlite3.Connection, consumer_cursor_table: ConsumerCursorTable, floor_key: tuple[str, ...],
) -> None:
    """Start one live consumer at the canonical head on its first pass; keep an existing floor.

    The key is owner, history revision, and generation.
    """
    owner, history_revision, generation = floor_key
    connection_handle.execute(
        "INSERT OR IGNORE INTO extension_consumer_floors(consumer, owner, history_revision, generation, "
        "floor_cursor, created_at) SELECT ?, ?, ?, ?, COALESCE(MAX(cursor), 0), ? FROM canonical_events "
        "WHERE history_revision=?",
        (consumer_cursor_table.consumer, owner, history_revision, generation, time.time(), history_revision),
    )


def _floor(
    connection_handle: sqlite3.Connection, consumer_cursor_table: ConsumerCursorTable, floor_key: tuple[str, ...],
) -> int:
    floor = connection_handle.execute(
        "SELECT floor_cursor FROM extension_consumer_floors "
        "WHERE consumer=? AND owner=? AND history_revision=? AND generation=?",
        (consumer_cursor_table.consumer, *floor_key),
    ).fetchone()
    return 0 if floor is None else int(floor["floor_cursor"])
