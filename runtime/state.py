"""Small runtime coordination state that is not canonical activity."""

from __future__ import annotations

from contracts.harness import SourceCheckpoint
from runtime.database import connect
from runtime.event_store import EventStore


class SqliteCheckpointStore:
    def __init__(self, event_store: EventStore) -> None:
        self.event_store = event_store

    def load(self, source_identity: str) -> SourceCheckpoint | None:
        with connect(self.event_store.database_path) as connection:
            row = connection.execute(
                "SELECT session_id, position FROM source_checkpoints WHERE source_identity=?",
                (source_identity,),
            ).fetchone()
        return (
            SourceCheckpoint(row["session_id"], source_identity, row["position"])
            if row is not None
            else None
        )

    def commit(self, checkpoint: SourceCheckpoint) -> None:
        with connect(self.event_store.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO source_checkpoints(source_identity, session_id, position) VALUES(?, ?, ?) "
                "ON CONFLICT(source_identity) DO UPDATE SET "
                "session_id=excluded.session_id, position=excluded.position",
                (str(checkpoint.source_identity), str(checkpoint.session_id), checkpoint.position),
            )
