# Copyright (c) 2026 Zhambyl Yermagambet
"""Define session stream state and service contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from audit.failures import ErrorRecorder as ErrorRecorder
from core.change_signal import ChangeSignal
from dashboard.services.workspace import SessionApplicationSnapshot

if TYPE_CHECKING:
    from domain.ids import SessionId
    from repository.contract.session_data import SessionDelta


@dataclass(frozen=True)
class SessionStreamPosition:
    """Name where a client resumes: its cursor and, when it knows it, its view revision."""

    cursor: int
    view_revision: int | None = None
    entry_cursor: int | None = None


@dataclass(frozen=True)
class SessionStreamQuery:
    """Keep the session stream query parameters together."""

    after_cursor: int
    include_application: bool
    view_revision: int | None
    after_entry: int | None = None


@dataclass
class SessionFrameState:
    """Store one session stream position and application snapshot."""

    cursor: int
    application: SessionApplicationSnapshot | None
    heartbeat_at: float
    application_read_at: float
    view_revision: int | None = None
    reset: bool = False
    entry_cursor: int | None = None


@dataclass(frozen=True)
class SessionStreamServices:
    """Contain the services for one session stream route."""

    read_model: SessionDeltaReader
    audit: ErrorRecorder
    session_application: SessionSnapshotReader | None = None
    changes: ChangeSignal = field(default_factory=ChangeSignal)


class SessionDeltaReader(Protocol):
    """Read changes for one session."""

    def delta(self, session_id: SessionId, cursor: int, entry_cursor: int | None = None) -> SessionDelta:
        """Return changes after a cursor."""
        ...


class SessionSnapshotReader(Protocol):
    """Read the application state for one session."""

    def snapshot(self, session_id: SessionId) -> SessionApplicationSnapshot:
        """Return the session application state."""
        ...
