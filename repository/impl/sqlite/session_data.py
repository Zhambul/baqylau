"""The read model over SQLite: three tables, one counter, one write method.

Every write goes through `apply`, which is one transaction over all three
tables plus the progress mark. That is the whole concurrency design: one writer
thread stamps rows with one monotonic counter, and every reader asks the same
question — "what changed after C?" — of an index.

The counter is held in memory and initialised from the maximum across the three
tables, so it survives a restart without a table of its own. Correct because
there is exactly one writer: the reaction loop.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from threading import Lock

from domain.codec import encode_document
from domain.entries import (
    ATTENTION_ENTRY_TYPES,
    ENTRY_TYPES,
    SessionEntry,
    pending_attention,
)
from domain.ids import SessionId
from domain.sessiondata import ActorFacts, SessionData, SessionFacts
from repository.contract.session_data import (
    AggregateDelta,
    EntryPage,
    SessionDataChanges,
    SessionDataRepository,
    SessionDelta,
)
from repository.impl.sqlite import rows
from repository.impl.sqlite.connection import SqliteDatabase
from repository.mapper import session_data as mapper

_ENTRY_COLUMNS = (
    "cursor, entry_id, session_id, entry_type, actor_id, parent_actor_id, "
    "turn_id, occurred_at, summary, payload"
)


class SqliteSessionDataRepository(SessionDataRepository):
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database
        self._revision_lock = Lock()
        self._revision: int | None = None

    # --- the write side ------------------------------------------------------

    def apply(
        self,
        session_id: SessionId,
        changes: SessionDataChanges,
        canonical_cursor: int,
    ) -> int:
        # An event that changes nothing still moves the mark, but it does not
        # burn a revision: a cursor with no row behind it is a client's poll that
        # returns nothing, every time, forever.
        revision = 0 if changes.empty else self._next_revision()
        with self.database.write() as connection:
            if changes.entry is not None:
                entry = changes.entry
                connection.execute(
                    f"INSERT OR IGNORE INTO session_entries({_ENTRY_COLUMNS}) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        revision,
                        str(entry.entry_id),
                        str(entry.session_id),
                        ENTRY_TYPES[type(entry.body)],
                        str(entry.actor_id),
                        str(entry.parent_actor_id) if entry.parent_actor_id else None,
                        str(entry.turn_id) if entry.turn_id else None,
                        entry.occurred_at,
                        entry.summary,
                        encode_document(entry.body).decode("utf-8"),
                    ),
                )
            if changes.session is not None:
                connection.execute(
                    "INSERT INTO session_data(session_id, revision, payload) VALUES(?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET revision=excluded.revision, "
                    "payload=excluded.payload",
                    (
                        str(session_id),
                        revision,
                        encode_document(changes.session).decode("utf-8"),
                    ),
                )
            for actor in changes.actors:
                connection.execute(
                    "INSERT INTO session_data_actors(session_id, actor_id, revision, payload) "
                    "VALUES(?, ?, ?, ?) ON CONFLICT(session_id, actor_id) DO UPDATE SET "
                    "revision=excluded.revision, payload=excluded.payload",
                    (
                        str(session_id),
                        str(actor.actor_id),
                        revision,
                        encode_document(actor).decode("utf-8"),
                    ),
                )
            connection.execute(
                "INSERT INTO reaction_progress(id, canonical_cursor, updated_at) "
                "VALUES(1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "canonical_cursor=excluded.canonical_cursor, updated_at=excluded.updated_at",
                (canonical_cursor, time.time()),
            )
        return revision

    def progress(self) -> int:
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT canonical_cursor FROM reaction_progress WHERE id=1"
            ).fetchone()
        return int(found["canonical_cursor"]) if found is not None else 0

    def clear(self) -> None:
        with self.database.write() as connection:
            for table in ("session_entries", "session_data_actors", "session_data"):
                connection.execute(f"DELETE FROM {table}")
            connection.execute("DELETE FROM reaction_progress")
            # The entry cursor is an AUTOINCREMENT column, whose high-water mark
            # outlives the rows; a rebuild that left it in place would start the
            # new feed above every cursor a client already holds.
            connection.execute("DELETE FROM sqlite_sequence WHERE name='session_entries'")
        with self._revision_lock:
            self._revision = 0

    def _next_revision(self) -> int:
        with self._revision_lock:
            if self._revision is None:
                self._revision = self._highest_revision()
            self._revision += 1
            return self._revision

    def _highest_revision(self) -> int:
        with self.database.read() as connection:
            found = connection.execute(
                "SELECT MAX(value) AS value FROM ("
                "SELECT MAX(cursor) AS value FROM session_entries "
                "UNION ALL SELECT MAX(revision) FROM session_data "
                "UNION ALL SELECT MAX(revision) FROM session_data_actors)"
            ).fetchone()
        return int(found["value"] or 0)

    # --- the read side -------------------------------------------------------

    def read(self, session_id: SessionId) -> SessionData | None:
        with self.database.read() as connection:
            session_row = connection.execute(
                "SELECT * FROM session_data WHERE session_id=?", (str(session_id),)
            ).fetchone()
            if session_row is None:
                return None
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE session_id=? ORDER BY actor_id",
                (str(session_id),),
            ).fetchall()
            newest = connection.execute(
                "SELECT MAX(cursor) AS cursor, MAX(occurred_at) AS occurred_at "
                "FROM session_entries WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return _aggregate(session_row, actor_rows, newest["cursor"], newest["occurred_at"])

    def visible(self) -> tuple[SessionData, ...]:
        with self.database.read() as connection:
            session_rows = connection.execute("SELECT * FROM session_data").fetchall()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors ORDER BY session_id, actor_id"
            ).fetchall()
            entry_cursors = connection.execute(
                "SELECT session_id, MAX(cursor) AS cursor, MAX(occurred_at) AS occurred_at "
                "FROM session_entries GROUP BY session_id"
            ).fetchall()
        actors_by_session: dict[str, list[sqlite3.Row]] = {}
        for row in actor_rows:
            actors_by_session.setdefault(row["session_id"], []).append(row)
        newest = {row["session_id"]: row for row in entry_cursors}
        return tuple(
            _aggregate(
                session_row,
                actors_by_session.get(session_row["session_id"], ()),
                _value(newest.get(session_row["session_id"]), "cursor"),
                _value(newest.get(session_row["session_id"]), "occurred_at"),
            )
            for session_row in session_rows
        )

    def entries_page(
        self,
        session_id: SessionId,
        *,
        at: int | None = None,
        before: int | None = None,
        limit: int = 200,
    ) -> EntryPage:
        ceiling = "AND cursor <= ?" if at is not None else ""
        floor = "AND cursor < ?" if before is not None else ""
        arguments: list[object] = [str(session_id)]
        if at is not None:
            arguments.append(at)
        if before is not None:
            arguments.append(before)
        with self.database.read() as connection:
            # One more than asked for: whether there is another page is the same
            # question as whether the row after this page exists.
            found = connection.execute(
                f"SELECT * FROM session_entries WHERE session_id=? {ceiling} {floor} "
                "ORDER BY cursor DESC LIMIT ?",
                (*arguments, limit + 1),
            ).fetchall()
        has_more = len(found) > limit
        page = list(reversed(found[:limit]))
        items = tuple(_entry(row) for row in page)
        return EntryPage(
            items=items,
            oldest_cursor=items[0].cursor if items else 0,
            has_more=has_more,
        )

    def entries_of_types(
        self,
        session_id: SessionId,
        entry_types: Sequence[str],
    ) -> tuple[SessionEntry, ...]:
        if not entry_types:
            return ()
        names = ",".join("?" for _name in entry_types)
        with self.database.read() as connection:
            found = connection.execute(
                f"SELECT * FROM session_entries WHERE session_id=? AND entry_type IN ({names}) "
                "ORDER BY cursor",
                (str(session_id), *entry_types),
            ).fetchall()
        return tuple(_entry(row) for row in found)

    def pending_attention(self, session_id: SessionId) -> tuple[SessionEntry, ...]:
        return pending_attention(self.entries_of_types(session_id, ATTENTION_ENTRY_TYPES))

    def delta(self, session_id: SessionId, cursor: int) -> SessionDelta:
        with self.database.read() as connection:
            entry_rows = connection.execute(
                "SELECT * FROM session_entries WHERE session_id=? AND cursor > ? ORDER BY cursor",
                (str(session_id), cursor),
            ).fetchall()
            session_row = connection.execute(
                "SELECT * FROM session_data WHERE session_id=? AND revision > ?",
                (str(session_id), cursor),
            ).fetchone()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE session_id=? AND revision > ? "
                "ORDER BY revision",
                (str(session_id), cursor),
            ).fetchall()
        revisions = [int(row["revision"]) for row in actor_rows]
        if session_row is not None:
            revisions.append(int(session_row["revision"]))
        revisions.extend(int(row["cursor"]) for row in entry_rows)
        return SessionDelta(
            session=None if session_row is None else _session_facts(session_row),
            actors=tuple(_actor_facts(row) for row in actor_rows),
            entries=tuple(_entry(row) for row in entry_rows),
            cursor=max(revisions) if revisions else cursor,
        )

    def changed_after(self, cursor: int) -> AggregateDelta:
        with self.database.read() as connection:
            session_rows = connection.execute(
                "SELECT * FROM session_data WHERE revision > ? ORDER BY revision", (cursor,)
            ).fetchall()
            actor_rows = connection.execute(
                "SELECT * FROM session_data_actors WHERE revision > ? ORDER BY revision",
                (cursor,),
            ).fetchall()
        revisions = [int(row["revision"]) for row in (*session_rows, *actor_rows)]
        return AggregateDelta(
            sessions=tuple(_session_facts(row) for row in session_rows),
            actors=tuple(_actor_facts(row) for row in actor_rows),
            cursor=max(revisions) if revisions else cursor,
        )


def _session_facts(row: sqlite3.Row) -> SessionFacts:
    return mapper.session_facts(rows.session_data(row))


def _actor_facts(row: sqlite3.Row) -> ActorFacts:
    return mapper.actor_facts(rows.session_data_actor(row))


def _entry(row: sqlite3.Row) -> SessionEntry:
    return mapper.session_entry(rows.session_entry(row))


def _value(row: sqlite3.Row | None, column: str) -> float | None:
    return None if row is None else row[column]


def _aggregate(
    session_row: sqlite3.Row,
    actor_rows: Sequence[sqlite3.Row],
    newest_entry_cursor: float | None,
    newest_entry_at: float | None,
) -> SessionData:
    actors = tuple(_actor_facts(row) for row in actor_rows)
    return SessionData(
        session=_session_facts(session_row),
        actors=actors,
        # The high-water mark across BOTH kinds of change. The aggregate's own
        # revision alone routinely lags the newest entry, and a stream started
        # there would re-send entries the client already has.
        cursor=max(
            [int(session_row["revision"])]
            + [int(row["revision"]) for row in actor_rows]
            + [int(newest_entry_cursor or 0)]
        ),
        last_activity_at=newest_entry_at,
    )
