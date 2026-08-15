"""The single point of registering and handing out sessions."""

from __future__ import annotations

import time
from dataclasses import replace

from contracts.harness import Session
from domain.ids import ActorId, SessionId
from runtime.database import connect, initialize
from runtime.harnesses import HarnessRegistry


class SessionRegistryError(RuntimeError):
    pass


class UnknownSession(SessionRegistryError):
    pass


class SessionRegistry:
    """Owns `session_harness`: one insert per session, at launch, then reads.

    Registration is a LAUNCH-TIME act performed by the wrapper that started the
    harness process — never by a hook. The row is the first observation of the
    session and is immutable; everything that changes afterwards (working
    directory, title, model) is a canonical fact.

    Constructed with a `HarnessRegistry`, every session it hands out carries its
    `.plugin`. Recorder processes construct it without one and get plugin-less
    sessions, which is all a recorder may need.
    """

    def __init__(self, database_path: str, harnesses: HarnessRegistry | None = None) -> None:
        self.database_path = initialize(database_path)
        self.harnesses = harnesses

    def register(self, harness: str, session: Session) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT harness FROM session_harness WHERE session_id=?",
                (str(session.session_id),),
            ).fetchone()
            if existing is not None:
                raise SessionRegistryError(
                    f"session is already registered: {session.session_id}"
                )
            connection.execute(
                "INSERT INTO session_harness("
                "session_id, lead_actor_id, harness, native_session_id, "
                "source_reference, working_directory, native_process_id, registered_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(session.session_id),
                    str(session.lead_actor_id),
                    harness,
                    session.native_session_id,
                    session.source_reference,
                    session.working_directory,
                    session.native_process_id,
                    time.time(),
                ),
            )

    def find(self, session_id: SessionId) -> Session | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM session_harness WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return self._session(row) if row is not None else None

    def load(self, session_id: SessionId) -> Session:
        session = self.find(session_id)
        if session is None:
            raise UnknownSession(f"unknown session: {session_id}")
        return session

    def harness(self, session_id: SessionId) -> str | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT harness FROM session_harness WHERE session_id=?",
                (str(session_id),),
            ).fetchone()
        return row["harness"] if row is not None else None

    def watchable(self) -> tuple[Session, ...]:
        """Every session without a committed finish, most recently observed first.

        No count limit by design: liveness is an evidence question (a finish
        fact), never a quota. An up-to-date source costs one stat per pass, so
        the set stays cheap as long as sessions finish — and they finish because
        the wrappers and process sources record it even for killed harnesses.
        """
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT session.* FROM session_harness AS session "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM canonical_events "
                "  WHERE canonical_events.session_id=session.session_id "
                "  AND canonical_events.event_type='session.finished'"
                ") "
                "ORDER BY ("
                "  SELECT MAX(raw_events.observed_at) FROM raw_events "
                "  WHERE raw_events.session_id=session.session_id"
                ") DESC, session.registered_at DESC",
            ).fetchall()
        return tuple(self._session(row) for row in rows)

    def is_finished(self, session_id: SessionId) -> bool:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM canonical_events "
                "WHERE session_id=? AND event_type='session.finished' LIMIT 1",
                (str(session_id),),
            ).fetchone()
        return row is not None

    def _session(self, row) -> Session:
        session = Session(
            session_id=SessionId(row["session_id"]),
            lead_actor_id=ActorId(row["lead_actor_id"]),
            native_session_id=row["native_session_id"],
            source_reference=row["source_reference"],
            working_directory=row["working_directory"],
            native_process_id=row["native_process_id"],
        )
        if self.harnesses is None:
            return session
        return replace(session, plugin=self.harnesses.plugin(row["harness"]))
