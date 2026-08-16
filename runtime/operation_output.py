"""Operation output files: located by facts, read to completion by the collect phase."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time

from contracts.harness import HarnessRawEventSource, RawEvent
from domain.events import OperationOutputLocated
from domain.ids import ActorId, RawEventId, SessionId
from runtime.database import connect, initialize

READ_SIZE = 64 * 1024
MAXIMUM_LIFETIME_SECONDS = 2 * 60 * 60
FINISHED_POSITION = "finished"


def operation_output_source_identity(
    harness: str, session_id: SessionId, operation_id: str
) -> str:
    return f"{harness}:operation_output:{session_id}:{operation_id}"


class OperationOutputStore:
    """Owns the `operation_output` table: one row = one operation's output file
    being followed. Rows are written by the reaction to the committed
    `operation.output_located` fact, marked finishing by the reaction to
    `operation.finished` (foreground rows only), and drained + removed when the
    reader reaches the end of a finishing file or the session finishes.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = initialize(database_path)

    def save(
        self,
        session_id: SessionId,
        harness: str,
        actor_id: ActorId,
        parent_actor_id: ActorId | None,
        located: OperationOutputLocated,
    ) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO operation_output("
                "session_id, harness, operation_id, actor_id, parent_actor_id, "
                "source_path, chunk_source_type, delete_source, initial_size, "
                "initial_modified_at, wait_for_source_change, until, state, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (
                    str(session_id),
                    harness,
                    str(located.operation_id),
                    str(actor_id),
                    str(parent_actor_id) if parent_actor_id is not None else None,
                    located.source_path,
                    located.chunk_source_type,
                    1 if located.delete_source else 0,
                    located.initial_size,
                    located.initial_modified_at,
                    1 if located.wait_for_source_change else 0,
                    located.until,
                    time.time(),
                ),
            )

    def finish(self, session_id: SessionId, operation_id: str) -> None:
        """Mark a foreground following finished; a background row's launch
        reports "finished" while output keeps flowing, so it is untouched here
        — the session's end is its end."""
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE operation_output SET state='finishing' "
                "WHERE session_id=? AND operation_id=? AND until='operation_finished'",
                (str(session_id), operation_id),
            )

    def for_session(self, session_id: SessionId) -> tuple[OperationOutputRawEventSource, ...]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM operation_output WHERE session_id=? "
                "ORDER BY created_at, operation_id",
                (str(session_id),),
            ).fetchall()
        sources = []
        for row in rows:
            if time.time() - row["created_at"] >= MAXIMUM_LIFETIME_SECONDS:
                self.remove(session_id, row["operation_id"], bool(row["delete_source"]), row["source_path"])
                continue
            sources.append(OperationOutputRawEventSource(self, row))
        return tuple(sources)

    def remove(
        self,
        session_id: SessionId,
        operation_id: str,
        delete_source: bool,
        source_path: str,
    ) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM operation_output WHERE session_id=? AND operation_id=?",
                (str(session_id), operation_id),
            )
        if delete_source:
            try:
                os.remove(source_path)
            except FileNotFoundError:
                pass


class OperationOutputRawEventSource(HarnessRawEventSource):
    """Generic chunk reader over one followed, growing file.

    Position encoding: the byte offset AFTER the last emitted chunk, or
    `finished` once a finishing row has been drained. Chunk boundaries are
    arbitrary slices of a growing file, so the position must be the chunk's END
    — resuming from a start offset would re-read different bytes under a
    different identity and duplicate evidence.
    """

    def __init__(self, store: OperationOutputStore, row) -> None:
        self.store = store
        self.session_id = SessionId(row["session_id"])
        self.harness = row["harness"]
        self.operation_id = row["operation_id"]
        self.actor_id = ActorId(row["actor_id"])
        self.parent_actor_id = (
            ActorId(row["parent_actor_id"]) if row["parent_actor_id"] is not None else None
        )
        self.source_path = row["source_path"]
        self.chunk_source_type = row["chunk_source_type"]
        self.delete_source = bool(row["delete_source"])
        self.initial_size = int(row["initial_size"])
        self.initial_modified_at = int(row["initial_modified_at"])
        self.wait_for_source_change = bool(row["wait_for_source_change"])
        self.finishing = row["state"] == "finishing"
        self.source_identity = operation_output_source_identity(
            self.harness, self.session_id, self.operation_id
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        if after_position == FINISHED_POSITION:
            return ()
        if after_position is None and self.wait_for_source_change and not self._source_changed():
            return ()
        position = (
            int(after_position)
            if after_position is not None
            else (0 if self.wait_for_source_change else self.initial_size)
        )
        raw_events: list[RawEvent] = []
        if os.path.isfile(self.source_path):
            with open(self.source_path, "rb") as source:
                source.seek(position)
                while True:
                    chunk_position = source.tell()
                    content = source.read(READ_SIZE)
                    if not content:
                        break
                    raw_events.append(self._chunk(chunk_position, source.tell(), content))
        if self.finishing:
            self.store.remove(
                self.session_id, self.operation_id, self.delete_source, self.source_path
            )
            if raw_events:
                last = raw_events[-1]
                raw_events[-1] = RawEvent(
                    raw_event_id=last.raw_event_id,
                    harness=last.harness,
                    source_type=last.source_type,
                    source_name=last.source_name,
                    source_position=FINISHED_POSITION,
                    session_id=last.session_id,
                    actor_id=last.actor_id,
                    parent_actor_id=last.parent_actor_id,
                    observed_at=last.observed_at,
                    encoding=last.encoding,
                    payload=last.payload,
                    source_identity=last.source_identity,
                )
        return tuple(raw_events)

    def _source_changed(self) -> bool:
        try:
            source_stat = os.stat(self.source_path)
        except FileNotFoundError:
            return False
        return (
            source_stat.st_size != self.initial_size
            or source_stat.st_mtime_ns != self.initial_modified_at
        )

    def _chunk(self, start: int, end: int, content: bytes) -> RawEvent:
        document = json.dumps(
            {
                "operation_id": self.operation_id,
                "ordinal": start,
                "stream": "output",
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        content_hash = hashlib.sha256(content).hexdigest()
        return RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:{start}:{content_hash}"),
            harness=self.harness,
            source_type=self.chunk_source_type,
            source_name=self.source_path,
            source_position=str(end),
            session_id=self.session_id,
            actor_id=self.actor_id,
            parent_actor_id=self.parent_actor_id,
            observed_at=time.time(),
            encoding="json",
            payload=document,
            source_identity=self.source_identity,
        )
