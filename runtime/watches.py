"""File watches: hook-recorded directives the interpreter pulls to completion."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time

from contracts.harness import (
    HarnessRawEventSource,
    RawEvent,
    WATCH_SOURCE_TYPE,
)
from domain.ids import ActorId, RawEventId, SessionId
from runtime.database import connect, initialize

READ_SIZE = 64 * 1024
MAXIMUM_LIFETIME_SECONDS = 2 * 60 * 60
FINISHED_POSITION = "finished"


def watch_source_identity(harness: str, session_id: SessionId, operation_id: str) -> str:
    return f"{harness}:watch:{session_id}:{operation_id}"


class WatchRegistry:
    """Owns the `watches` table.

    A `watch` raw event is a directive-as-evidence: the interpreter applies it
    here (start → an active row, finish → the row marked finishing) and pulls
    each active row's file with the generic source below until it has drained a
    finishing watch to EOF, at which point the row and its files are removed.
    """

    def __init__(self, database_path: str) -> None:
        self.database_path = initialize(database_path)

    def apply(self, raw_event: RawEvent) -> None:
        if raw_event.source_type != WATCH_SOURCE_TYPE:
            raise ValueError(f"not a watch directive: {raw_event.raw_event_id}")
        document = json.loads(raw_event.payload)
        action = document.get("action")
        if action == "start":
            self._start(raw_event, document)
        elif action == "finish":
            self._finish(raw_event, str(document.get("operation_id") or ""))
        else:
            raise ValueError(f"unknown watch action: {action!r}")

    def _start(self, raw_event: RawEvent, document: dict) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO watches("
                "session_id, harness, operation_id, actor_id, parent_actor_id, "
                "source_path, chunk_source_type, delete_source, initial_size, "
                "initial_modified_at, wait_for_source_change, state, created_at"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (
                    str(raw_event.session_id),
                    raw_event.harness,
                    str(document["operation_id"]),
                    str(raw_event.actor_id),
                    str(raw_event.parent_actor_id)
                    if raw_event.parent_actor_id is not None
                    else None,
                    str(document["source_path"]),
                    str(document["chunk_source_type"]),
                    1 if document.get("delete_source") else 0,
                    int(document.get("initial_size") or 0),
                    int(document.get("initial_modified_at") or 0),
                    1 if document.get("wait_for_source_change") else 0,
                    raw_event.observed_at,
                ),
            )

    def _finish(self, raw_event: RawEvent, operation_id: str) -> None:
        with connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE watches SET state='finishing' WHERE session_id=? AND operation_id=?",
                (str(raw_event.session_id), operation_id),
            )

    def for_session(self, session_id: SessionId) -> tuple[FileWatchRawEventSource, ...]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM watches WHERE session_id=? ORDER BY created_at, operation_id",
                (str(session_id),),
            ).fetchall()
        sources = []
        for row in rows:
            if time.time() - row["created_at"] >= MAXIMUM_LIFETIME_SECONDS:
                self.remove(session_id, row["operation_id"], bool(row["delete_source"]), row["source_path"])
                continue
            sources.append(FileWatchRawEventSource(self, row))
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
                "DELETE FROM watches WHERE session_id=? AND operation_id=?",
                (str(session_id), operation_id),
            )
        if delete_source:
            try:
                os.remove(source_path)
            except FileNotFoundError:
                pass


class FileWatchRawEventSource(HarnessRawEventSource):
    """Generic chunk reader over one watched, growing file.

    Position encoding: the byte offset AFTER the last emitted chunk, or
    `finished` once a finishing watch has been drained. Chunk boundaries are
    arbitrary slices of a growing file, so the position must be the chunk's END
    — resuming from a start offset would re-read different bytes under a
    different identity and duplicate evidence.
    """

    def __init__(self, registry: WatchRegistry, row) -> None:
        self.registry = registry
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
        self.source_identity = watch_source_identity(
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
            self.registry.remove(
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
