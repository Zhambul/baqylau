"""Reading one followed, growing output file as evidence.

A hook that makes a command's output observable cannot follow the file itself —
it must exit immediately. So the gateway records an output-location directive,
the reaction starts a following, and THIS reads the file's chunks as their own
raw events.

It is not a repository and never was: it takes an `OperationOutputFollowing`
value and owns the filesystem side — the reading, and the unlinking of a tee
file we created. Both used to sit inside the store, which is how listing the
followings acquired the power to delete a user's file.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time

from domain.ids import OperationId, RawEventId, SessionId
from domain.operations import OperationOutputFollowing
from harness.contract import HarnessRawEventSource
from harness.models import RawEvent
from repository.contract.operations import OperationOutputRepository
from domain.codec import encode_document
from harness.models.directives import OperationOutputChunk

READ_SIZE = 64 * 1024
MAXIMUM_LIFETIME_SECONDS = 2 * 60 * 60
FINISHED_POSITION = "finished"


def operation_output_source_identity(
    harness: str, session_id: SessionId, operation_id: OperationId
) -> str:
    return f"{harness}:operation_output:{session_id}:{operation_id}"


def delete_source_file(following: OperationOutputFollowing) -> None:
    """Unlink the tee file, when we were the ones who made it."""
    if not following.delete_source:
        return
    try:
        os.remove(following.source_path)
    except FileNotFoundError:
        pass


class OperationOutputRawEventSource(HarnessRawEventSource):
    """Generic chunk reader over one followed, growing file.

    Position encoding: the byte offset AFTER the last emitted chunk, or
    `finished` once a finishing following has been drained. Chunk boundaries are
    arbitrary slices of a growing file, so the position must be the chunk's END
    — resuming from a start offset would re-read different bytes under a
    different identity and duplicate evidence.
    """

    def __init__(
        self,
        following: OperationOutputFollowing,
        operation_output: OperationOutputRepository,
    ) -> None:
        self.following = following
        self.operation_output = operation_output
        self.source_identity = operation_output_source_identity(
            following.harness, following.session_id, following.operation_id
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        following = self.following
        if after_position == FINISHED_POSITION:
            return ()
        if (
            after_position is None
            and following.wait_for_source_change
            and not self._source_changed()
        ):
            return ()
        position = (
            int(after_position)
            if after_position is not None
            else (0 if following.wait_for_source_change else following.initial_size)
        )
        raw_events: list[RawEvent] = []
        if os.path.isfile(following.source_path):
            with open(following.source_path, "rb") as source:
                source.seek(position)
                while True:
                    chunk_position = source.tell()
                    content = source.read(READ_SIZE)
                    if not content:
                        break
                    raw_events.append(self._chunk(chunk_position, source.tell(), content))
        if following.finishing:
            self.operation_output.remove(following.session_id, following.operation_id)
            delete_source_file(following)
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
            source_stat = os.stat(self.following.source_path)
        except FileNotFoundError:
            return False
        return (
            source_stat.st_size != self.following.initial_size
            or source_stat.st_mtime_ns != self.following.initial_modified_at
        )

    def _chunk(self, start: int, end: int, content: bytes) -> RawEvent:
        following = self.following
        document = encode_document(
            OperationOutputChunk(
                content_base64=base64.b64encode(content).decode("ascii"),
                operation_id=following.operation_id,
                ordinal=start,
                stream="output",
            )
        )
        content_hash = hashlib.sha256(content).hexdigest()
        return RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:{start}:{content_hash}"),
            harness=following.harness,
            source_type=following.chunk_source_type,
            source_name=following.source_path,
            source_position=str(end),
            session_id=following.session_id,
            actor_id=following.actor_id,
            parent_actor_id=following.parent_actor_id,
            observed_at=time.time(),
            encoding="json",
            payload=document,
            source_identity=self.source_identity,
        )


def sources_for_session(
    operation_output: OperationOutputRepository,
    session_id: SessionId,
) -> tuple[OperationOutputRawEventSource, ...]:
    """Every following of one session, as readers. A pure read: expiry is the
    interpreter's own call, made once a tick, not a side effect of listing."""
    return tuple(
        OperationOutputRawEventSource(following, operation_output)
        for following in operation_output.find_for_session(session_id)
    )


def expire(operation_output: OperationOutputRepository, now: float) -> None:
    """Drop followings that have outlived their ceiling, and unlink their files."""
    for following in operation_output.remove_expired(now - MAXIMUM_LIFETIME_SECONDS):
        delete_source_file(following)
