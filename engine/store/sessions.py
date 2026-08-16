"""The single owner of the `sessions` table: one writer, one write method."""

from __future__ import annotations

import time
from dataclasses import replace

from harness.models import Session
from domain.ids import ActorId, SessionId
from engine.store.database import connect, initialize
from harness.registry import HarnessRegistry


class SessionStore:
    """Owns `sessions`. Sessions are read-models of committed facts: the one
    writer is the interpreter's session-upsert reaction, which derives birth
    from the session's own `session.started` fact and keeps the two live
    columns current from later evidence.

    Constructed with a `HarnessRegistry`, every session it hands out carries its
    `.plugin`. Recorder processes construct it without one and get plugin-less
    sessions, which is all a recorder may need.
    """

    def __init__(self, database_path: str, harnesses: HarnessRegistry | None = None) -> None:
        self.database_path = initialize(database_path)
        self.harnesses = harnesses

    def save(self, harness: str, session: Session) -> None:
        """Upsert: identity columns written once, live columns overwritten."""
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO sessions("
                "  session_id, lead_actor_id, harness, harness_session_id,"
                "  source_reference, working_directory,"
                "  terminal_window_id, harness_process_id, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "  terminal_window_id = excluded.terminal_window_id,"
                "  harness_process_id = excluded.harness_process_id",
                (
                    str(session.session_id),
                    str(session.lead_actor_id),
                    harness,
                    session.harness_session_id,
                    session.source_reference,
                    session.working_directory,
                    session.terminal_window_id,
                    session.harness_process_id,
                    time.time(),
                ),
            )

    def find_by_id(self, session_id: SessionId) -> Session | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return self._session(row) if row is not None else None

    def watchable(self) -> tuple[Session, ...]:
        """Every session without a committed finish, most recently observed first.

        No count limit by design: liveness is an evidence question (a finish
        fact), never a quota. An up-to-date source costs one stat per pass, so
        the set stays cheap as long as sessions finish — and they finish because
        the liveness source records the CLI process's exit.
        """
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT sessions.* FROM sessions "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM canonical_events "
                "  WHERE canonical_events.session_id = sessions.session_id "
                "  AND canonical_events.event_type = 'session.finished'"
                ") ORDER BY ("
                "  SELECT MAX(raw_events.observed_at) FROM raw_events "
                "  WHERE raw_events.session_id = sessions.session_id"
                ") DESC, sessions.created_at DESC",
            ).fetchall()
        return tuple(self._session(row) for row in rows)

    def _session(self, row) -> Session:
        session = Session(
            session_id=SessionId(row["session_id"]),
            lead_actor_id=ActorId(row["lead_actor_id"]),
            harness_session_id=row["harness_session_id"],
            source_reference=row["source_reference"],
            working_directory=row["working_directory"],
            terminal_window_id=row["terminal_window_id"],
            harness_process_id=row["harness_process_id"],
        )
        if self.harnesses is None:
            return session
        return replace(session, plugin=self.harnesses.plugin(row["harness"]))
