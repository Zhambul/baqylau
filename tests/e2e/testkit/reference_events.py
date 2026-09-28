# Copyright (c) 2026 Zhambyl Yermagambet
"""Define E2E references for stream and process events."""

from __future__ import annotations

from dataclasses import dataclass

from sdk.client import SessionRef, SessionSnapshotRead, SessionStreamUpdate


@dataclass(frozen=True)
class TaskRef:
    """Represent one session task."""

    session: SessionRef
    task_id: str


@dataclass(frozen=True)
class CompactionRef:
    """Represent one compaction event."""

    session: SessionRef
    actor_id: str
    started_cursor: int


@dataclass(frozen=True)
class FeedSnapshotRef:
    """Represent one saved feed snapshot."""

    session: SessionRef
    read: SessionSnapshotRead


@dataclass(frozen=True)
class StreamCheckpointRef:
    """Represent a stream checkpoint."""

    session: SessionRef
    session_cursor: int
    global_cursor: int
    # Entries have their own cursor, so a newer entry is compared with this one.
    entry_cursor: int = 0


@dataclass(frozen=True)
class SessionStreamUpdateRef:
    """Represent one session stream update."""

    session: SessionRef
    update: SessionStreamUpdate


@dataclass(frozen=True)
class ApplicationRestartRef:
    """Represent a process before and after restart."""

    before_process_id: int
    after_process_id: int
