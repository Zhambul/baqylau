# Copyright (c) 2026 Zhambyl Yermagambet
"""Reading one followed, growing output file as raw events.

A hook that makes a command's output observable cannot follow the file itself —
it must exit immediately. So the gateway records an output-location directive,
the reaction starts a following, and THIS reads the file's chunks as their own
raw events.

It is not a repository and never was: it takes a `ShellOutputFollowing`
value and owns the filesystem side — the reading, and the unlinking of a tee
file we created. Both used to sit inside the store, which is how listing the
followings acquired the power to delete a user's file.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import pathlib
import time
from typing import TYPE_CHECKING, BinaryIO

from domain import ids, outcomes, shells
from harness.contract import HarnessRawEventSource
from harness.models import directives, raw_events as raw_event_models
from repository.mapper.documents import encode_document

if TYPE_CHECKING:
    from repository.contract.shell_output import ShellOutputRepository

# Each chunk is one raw event, and each raw event has a fixed processing cost.
# Thus a large output must be few chunks. The payload keeps the chunk as base64,
# which is 4/3 of the chunk. Half of the worker content limit (1 MiB) keeps it
# below that limit.
READ_SIZE = 524_288
MAXIMUM_LIFETIME_SECONDS = 7_200
FINISHED_POSITION = "finished"


def shell_output_source_identity(
    harness: ids.HarnessName,
    session_id: ids.SessionId,
    shell_id: ids.ShellId,
    source_path: str,
) -> str:
    """Return the shell output source identity.

    Returns:
        Shell output source identity.

    """
    return f"{harness}:shell_output:{session_id}:{shell_id}:{shells.shell_output_source_key(source_path)}"


def delete_source_file(shell_output_following: shells.ShellOutputFollowing) -> None:
    """Unlink the tee file, when we were the ones who made it."""
    if not shell_output_following.delete_source:
        return
    with contextlib.suppress(FileNotFoundError):
        pathlib.Path(shell_output_following.source_path).unlink()


def _source_changed(following: shells.ShellOutputFollowing) -> bool:
    try:
        source_stat = pathlib.Path(following.source_path).stat()
    except FileNotFoundError:
        return False
    return source_stat.st_size != following.initial_size or source_stat.st_mtime_ns != following.initial_modified_at


class ShellOutputRawEventSource(HarnessRawEventSource):
    """Generic chunk reader over one followed, growing file.

    Position encoding: the byte offset AFTER the last emitted chunk, or
    `finished` once a finishing following has been drained. Chunk boundaries are
    arbitrary slices of a growing file, so the position must be the chunk's END
    — resuming from a start offset would re-read different bytes under a
    different identity and a duplicate raw event.
    """

    def __init__(
        self,
        shell_output_following: shells.ShellOutputFollowing,
        shell_output_repository: ShellOutputRepository,
    ) -> None:
        """Initialize the object."""
        self.following = shell_output_following
        self.shell_output_repository = shell_output_repository
        self.source_identity = shell_output_source_identity(
            shell_output_following.harness,
            shell_output_following.session_id,
            shell_output_following.shell_id,
            shell_output_following.source_path,
        )

    def watch_paths(self) -> tuple[str, ...]:
        """List the followed output file.

        Returns:
            The output path.

        """
        return (self.following.source_path,)

    def read(self, after_position: str | None) -> tuple[raw_event_models.RawEvent, ...]:
        """Return read.

        Returns:
            Read.

        """
        following = self.following
        if after_position == FINISHED_POSITION:
            return ()
        if after_position is None and following.wait_for_source_change and not _source_changed(following):
            return ()
        if after_position is None:
            position = 0 if following.wait_for_source_change else following.initial_size
        else:
            position = int(after_position)
        raw_events = self._read_chunks(position)
        if following.finishing:
            self._finish(raw_events)
        return tuple(raw_events)

    def _read_chunks(self, position: int) -> list[raw_event_models.RawEvent]:
        raw_events: list[raw_event_models.RawEvent] = []
        source_path = pathlib.Path(self.following.source_path)
        if source_path.is_file():
            with source_path.open("rb") as source:
                raw_events.extend(self._read_open_source(source, position))
        return raw_events

    def _read_open_source(self, source: BinaryIO, position: int) -> list[raw_event_models.RawEvent]:
        source.seek(position)
        raw_events: list[raw_event_models.RawEvent] = []
        while True:
            chunk_position = source.tell()
            content = source.read(READ_SIZE)
            if not content:
                break
            raw_events.append(self._chunk(chunk_position, source.tell(), content))
        return raw_events

    def _finish(self, raw_events: list[raw_event_models.RawEvent]) -> None:
        following = self.following
        self.shell_output_repository.remove(
            following.session_id,
            following.shell_id,
            following.source_path,
        )
        delete_source_file(following)
        if raw_events:
            last = raw_events[-1]
            raw_events[-1] = raw_event_models.RawEvent(
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

    def _chunk(self, start: int, end: int, content: bytes) -> raw_event_models.RawEvent:
        following = self.following
        document = encode_document(
            directives.ShellOutputChunk(
                content_base64=base64.b64encode(content).decode("ascii"),
                shell_id=following.shell_id,
                ordinal=start,
                stream=outcomes.ProgressStream.OUTPUT,
                source_key=shells.shell_output_source_key(following.source_path),
            ),
        )
        content_hash = hashlib.sha256(content).hexdigest()
        return raw_event_models.RawEvent(
            raw_event_id=ids.RawEventId(f"{self.source_identity}:{start}:{content_hash}"),
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
    shell_output_repository: ShellOutputRepository,
    session_id: ids.SessionId,
) -> tuple[ShellOutputRawEventSource, ...]:
    """Return the sources for session.

    Every following of one session, as readers. A pure read: expiry is the
        interpreter's own call, made once a tick, not a side effect of listing.

    Returns:
        Sources for session.

    """
    return tuple(
        ShellOutputRawEventSource(following, shell_output_repository)
        for following in shell_output_repository.find_for_session(session_id)
    )


def expire(shell_output_repository: ShellOutputRepository, now: float) -> None:
    """Drop followings that have outlived their ceiling, and unlink their files."""
    for following in shell_output_repository.remove_expired(
        now - MAXIMUM_LIFETIME_SECONDS,
    ):
        delete_source_file(following)
