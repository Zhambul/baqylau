# Copyright (c) 2026 Zhambyl Yermagambet
"""The read model over SQLite: three tables, one cursor, one write method.

Every write goes through `apply`, which is one transaction over all three
tables plus the progress mark. That is the whole concurrency design: one writer
thread stamps rows with the event's monotonic cursor, and every reader asks the
same question — "what changed after C?" — of an index.

The canonical event cursor stamps every row that event changes. It is already
global, monotonic, and durable, so a rebuild or a second process cannot make a
live stream move backwards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import entries
from domain.lifecycle import LifecycleState
from repository.contract import session_data as contracts
from repository.impl.sqlite import session_data_aggregate as aggregate_mapper, session_data_write as write_mapper

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from domain.ids import SessionId
    from domain.session_state import SessionData
    from repository.impl.sqlite.connection import SqliteDatabase

SESSION_ID_COLUMN = "session_id"
REVISION_COLUMN = "revision"


class _SqliteSessionDataState(contracts.SessionDataRepository):
    """Store the database for session-data operations."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Initialize the object."""
        self.sqlite_database = sqlite_database


class _SqliteSessionDataWrite(_SqliteSessionDataState):
    """Write session data and read write progress."""

    def apply(
        self,
        session_id: SessionId,
        session_data_changes: contracts.SessionDataChanges,
        canonical_cursor: int,
    ) -> int:
        # The canonical cursor is the stable identity of this change. Using a
        # process-local counter here made a daemon that started during a rebuild
        # keep handing out its stale, lower values after the rebuild finished.
        # Browsers had already passed those values and never received the rows.
        """Apply apply.

        Returns:
            Integer result.

        """
        with self.sqlite_database.write(notify_readers=not session_data_changes.empty) as connection:
            write_mapper.apply_changes(connection, session_id, session_data_changes, canonical_cursor)
        return canonical_cursor

    def progress(self) -> int:
        """Return the progress.

        Returns:
            Progress.

        """
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT canonical_cursor FROM reaction_progress WHERE id=1",
            ).fetchone()
        return 0 if found is None else int(found["canonical_cursor"])

    def clear(self) -> None:
        """Clear clear."""
        with self.sqlite_database.write() as connection:
            write_mapper.clear_read_model(connection)

    def high_water_cursor(self) -> int:
        """Return the high water cursor.

        Returns:
            High water cursor.

        """
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT MAX(value) AS value FROM ("
                "SELECT MAX(cursor) AS value FROM session_entries "
                "UNION ALL SELECT MAX(revision) FROM session_data "
                "UNION ALL SELECT MAX(revision) FROM session_data_actors)",
            ).fetchone()
        return int(found["value"] or 0)


class _SqliteSessionDataAggregateRead(_SqliteSessionDataState):
    """Read session aggregates."""

    def read(self, session_id: SessionId) -> SessionData | None:
        """Return read.

        Returns:
            Read.

        """
        session_id_text = str(session_id)
        with self.sqlite_database.read() as connection:
            session_row = connection.execute(
                "SELECT * FROM session_data WHERE session_id=?",
                (session_id_text,),
            ).fetchone()
            if session_row is None:
                return None
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE session_id=? ORDER BY actor_id",
                (session_id_text,),
            ).fetchall()
            newest = connection.execute(
                "SELECT MAX(cursor) AS cursor, MAX(occurred_at) AS occurred_at FROM session_entries WHERE session_id=?",
                (session_id_text,),
            ).fetchone()
        return aggregate_mapper.aggregate(session_row, actor_rows, newest["cursor"], newest["occurred_at"])

    def visible(self) -> tuple[SessionData, ...]:
        """Return the visible.

        Returns:
            Visible.

        """
        with self.sqlite_database.read() as connection:
            session_rows = connection.execute("SELECT * FROM session_data").fetchall()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors ORDER BY session_id, actor_id",
            ).fetchall()
            entry_cursors = connection.execute(
                "SELECT session_id, MAX(cursor) AS cursor, MAX(occurred_at) AS occurred_at "
                "FROM session_entries GROUP BY session_id",
            ).fetchall()
        return aggregate_mapper.aggregates(session_rows, actor_rows, entry_cursors)

    def running(self) -> tuple[SessionData, ...]:
        """Return the running.

        Returns:
            Running.

        """
        with self.sqlite_database.read() as connection:
            session_rows = connection.execute(
                "SELECT * FROM session_data WHERE json_extract(payload, '$.state') = ?",
                (LifecycleState.RUNNING.value,),
            ).fetchall()
            if not session_rows:
                return ()
            session_ids = tuple(str(row[SESSION_ID_COLUMN]) for row in session_rows)
            actor_rows, entry_cursors = _running_related_rows(connection, session_ids)
        return aggregate_mapper.aggregates(session_rows, actor_rows, entry_cursors)

    def working_directories(self) -> tuple[str, ...]:
        """Return the working directories.

        Returns:
            Working directories.

        """
        with self.sqlite_database.read() as connection:
            rows = connection.execute(
                "SELECT json_extract(payload, '$.working_directory') AS directory, "
                "MAX(COALESCE(json_extract(payload, '$.finished_at'), "
                "json_extract(payload, '$.started_at'), 0)) AS last_used_at "
                "FROM session_data "
                "WHERE json_extract(payload, '$.working_directory') != '' "
                "GROUP BY directory "
                "ORDER BY last_used_at DESC, directory",
            ).fetchall()
        return tuple(str(row["directory"]) for row in rows)

    def lead_sessions(self) -> tuple[contracts.SessionLead, ...]:
        """Return the lead sessions.

        Returns:
            Lead sessions.

        """
        with self.sqlite_database.read() as connection:
            session_rows = connection.execute(
                "SELECT * FROM session_data ORDER BY session_id",
            ).fetchall()
            lead_rows = connection.execute(
                "SELECT actor.* FROM session_data_actors AS actor "
                "JOIN session_data AS session "
                "ON session.session_id = actor.session_id "
                "AND actor.actor_id = json_extract(session.payload, '$.lead_actor_id') "
                "ORDER BY actor.session_id",
            ).fetchall()
        leads = {row[SESSION_ID_COLUMN]: aggregate_mapper.actor_facts(row) for row in lead_rows}
        return tuple(
            contracts.SessionLead(
                session=aggregate_mapper.session_facts(session_row),
                lead=leads.get(session_row[SESSION_ID_COLUMN]),
            )
            for session_row in session_rows
        )


class _SqliteSessionDataEntryRead(_SqliteSessionDataState):
    """Read session entry feeds and deltas."""

    def entries_page(
        self,
        session_id: SessionId,
        *,
        at: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> contracts.EntryPage:
        """Return the entries page.

        Returns:
            Entries page.

        """
        found = self._entry_page_rows(session_id, at, before, limit)
        has_more = len(found) > limit
        page = list(reversed(found[:limit]))
        entries = tuple(aggregate_mapper.entry(row) for row in page)
        return contracts.EntryPage(
            entries=entries,
            oldest_cursor=entries[0].cursor if entries else 0,
            has_more=has_more,
        )

    def entries_of_types(
        self,
        session_id: SessionId,
        entry_types: Sequence[str],
    ) -> tuple[entries.SessionEntry, ...]:
        """Return the entries of types.

        Returns:
            Entries of types.

        """
        if not entry_types:
            return ()
        names = ",".join("?" for _name in entry_types)
        with self.sqlite_database.read() as connection:
            found = connection.execute(
                "SELECT * FROM session_entries "  # noqa: S608 -- Only ? placeholders vary; values are bound.
                f"WHERE session_id=? AND entry_type IN ({names}) ORDER BY cursor",
                (str(session_id), *entry_types),
            ).fetchall()
        return tuple(aggregate_mapper.entry(row) for row in found)

    def pending_attention(self, session_id: SessionId) -> tuple[entries.SessionEntry, ...]:
        """Return the pending attention.

        Returns:
            Pending attention.

        """
        return entries.pending_attention(self.entries_of_types(session_id, entries.PENDING_ATTENTION_ENTRY_TYPES))

    def delta(self, session_id: SessionId, cursor: int) -> contracts.SessionDelta:
        """Return the delta.

        Returns:
            Delta.

        """
        with self.sqlite_database.read() as connection:
            entry_rows = connection.execute(
                "SELECT * FROM session_entries WHERE session_id=? AND cursor > ? ORDER BY cursor",
                (str(session_id), cursor),
            ).fetchall()
            session_row = connection.execute(
                "SELECT * FROM session_data WHERE session_id=? AND revision > ?",
                (str(session_id), cursor),
            ).fetchone()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE session_id=? AND revision > ? ORDER BY revision",
                (str(session_id), cursor),
            ).fetchall()
        revisions = [int(row[REVISION_COLUMN]) for row in actor_rows]
        if session_row is not None:
            revisions.append(int(session_row[REVISION_COLUMN]))
        revisions.extend(int(row["cursor"]) for row in entry_rows)
        return contracts.SessionDelta(
            session=None if session_row is None else aggregate_mapper.session_facts(session_row),
            actors=tuple(aggregate_mapper.actor_facts(row) for row in actor_rows),
            entries=tuple(aggregate_mapper.entry(row) for row in entry_rows),
            cursor=max(revisions) if revisions else cursor,
        )

    def changed_after(self, cursor: int) -> contracts.AggregateDelta:
        """Return the changed after.

        Returns:
            Changed after.

        """
        with self.sqlite_database.read() as connection:
            session_rows = connection.execute(
                "SELECT * FROM session_data WHERE revision > ? ORDER BY revision",
                (cursor,),
            ).fetchall()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE revision > ? ORDER BY revision",
                (cursor,),
            ).fetchall()
        revisions = [int(row[REVISION_COLUMN]) for row in (*session_rows, *actor_rows)]
        return contracts.AggregateDelta(
            sessions=tuple(aggregate_mapper.session_facts(row) for row in session_rows),
            actors=tuple(aggregate_mapper.actor_facts(row) for row in actor_rows),
            cursor=max(revisions) if revisions else cursor,
        )

    def _entry_page_rows(
        self,
        session_id: SessionId,
        at: int | None,
        before: int | None,
        limit: int,
    ) -> list[sqlite3.Row]:
        ceiling = "" if at is None else "AND cursor <= ?"
        floor = "" if before is None else "AND cursor < ?"
        arguments: list[str | int] = [str(session_id)]
        if at is not None:
            arguments.append(at)
        if before is not None:
            arguments.append(before)
        with self.sqlite_database.read() as connection:
            # One more than asked for: whether there is another page is the same
            # question as whether the row after this page exists.
            return connection.execute(
                "SELECT * FROM session_entries "  # noqa: S608 -- Clauses are fixed strings; values are bound.
                f"WHERE session_id=? {ceiling} {floor} ORDER BY cursor DESC LIMIT ?",
                (*arguments, limit + 1),
            ).fetchall()


class SqliteSessionDataRepository(
    _SqliteSessionDataWrite,
    _SqliteSessionDataAggregateRead,
    _SqliteSessionDataEntryRead,
):
    """Store and read session data in SQLite."""


def _running_related_rows(
    connection: sqlite3.Connection,
    session_ids: tuple[str, ...],
) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    placeholders = ",".join("?" for _session_id in session_ids)
    actor_rows = connection.execute(
        "SELECT * FROM session_data_actors "  # noqa: S608 -- Only ? placeholders vary; values are bound.
        f"WHERE session_id IN ({placeholders}) ORDER BY session_id, actor_id",
        session_ids,
    ).fetchall()
    entry_cursors = connection.execute(
        "SELECT session_id, MAX(cursor) AS cursor, "  # noqa: S608 -- Only ? placeholders vary; values are bound.
        "MAX(occurred_at) AS occurred_at FROM session_entries "
        f"WHERE session_id IN ({placeholders}) GROUP BY session_id",
        session_ids,
    ).fetchall()
    return actor_rows, entry_cursors
