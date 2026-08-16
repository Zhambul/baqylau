"""Append-only recording of raw evidence — the one write API every observer uses."""

from __future__ import annotations

from collections.abc import Sequence

from contracts.harness import RawEvent
from runtime.database import connect, initialize


class RawEventRecorderError(RuntimeError):
    pass


class EventIdentityConflict(RawEventRecorderError):
    pass


def _raw_identity(raw_event: RawEvent) -> tuple[object, ...]:
    return (
        str(raw_event.session_id),
        raw_event.harness,
        raw_event.source_type,
        raw_event.source_name,
        raw_event.source_position,
        str(raw_event.actor_id),
        str(raw_event.parent_actor_id) if raw_event.parent_actor_id is not None else None,
        raw_event.encoding,
        raw_event.payload,
    )


class RawEventRecorder:
    """Writes `raw_events` and nothing else.

    Re-recording an identical observation is a no-op (sources re-read their last
    record on resume by design); reusing a `raw_event_id` for DIFFERENT bytes
    raises — that is corruption, not convergence.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = initialize(database_path)

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        if not raw_events:
            return
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            for raw_event in raw_events:
                existing = connection.execute(
                    "SELECT session_id, harness, source_type, source_name, source_position, "
                    "actor_id, parent_actor_id, encoding, payload "
                    "FROM raw_events WHERE raw_event_id=?",
                    (str(raw_event.raw_event_id),),
                ).fetchone()
                if existing is not None:
                    if tuple(existing) != _raw_identity(raw_event):
                        raise EventIdentityConflict(
                            f"raw event identity reused: {raw_event.raw_event_id}"
                        )
                    continue
                connection.execute(
                    "INSERT INTO raw_events("
                    "raw_event_id, session_id, harness, source_type, source_identity, "
                    "source_name, source_position, actor_id, parent_actor_id, "
                    "observed_at, encoding, payload, terminal_window_id, "
                    "harness_process_id, account_id, account_display_name"
                    ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(raw_event.raw_event_id),
                        str(raw_event.session_id),
                        raw_event.harness,
                        raw_event.source_type,
                        raw_event.source_identity or raw_event.source_type,
                        raw_event.source_name,
                        raw_event.source_position,
                        str(raw_event.actor_id),
                        str(raw_event.parent_actor_id)
                        if raw_event.parent_actor_id is not None
                        else None,
                        raw_event.observed_at,
                        raw_event.encoding,
                        raw_event.payload,
                        raw_event.terminal_window_id,
                        raw_event.harness_process_id,
                        raw_event.account_id,
                        raw_event.account_display_name,
                    ),
                )

    def position(self, source_identity: str) -> str | None:
        """The resume position: the last recorded raw event of this source."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT source_position FROM raw_events "
                "WHERE source_identity=? ORDER BY id DESC LIMIT 1",
                (source_identity,),
            ).fetchone()
        return row["source_position"] if row is not None else None
