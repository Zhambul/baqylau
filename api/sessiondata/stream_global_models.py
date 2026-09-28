# Copyright (c) 2026 Zhambyl Yermagambet
"""Define global stream state and source contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from audit.failures import ErrorRecorder
from dashboard.services.preference_models import ApplicationPreferences as ApplicationPreferenceSnapshot

if TYPE_CHECKING:
    from core.change_signal import ChangeSignal
    from repository.contract.session_data import AggregateDelta


@dataclass
class GlobalFrameState:
    """Store one global stream position and application snapshot."""

    cursor: int
    application: ApplicationPreferenceSnapshot
    application_revision: int
    heartbeat_at: float
    view_revision: int | None = None
    reset: bool = False


@dataclass(frozen=True)
class GlobalFrameSources:
    """Contain the sources for one global stream."""

    read_model: AggregateDeltaReader
    audit: ErrorRecorder
    boot_id: str
    application_preferences: ApplicationSnapshotReader
    application_updates: RevisionReader


class AggregateDeltaReader(Protocol):
    """Read changes for all sessions."""

    def changed_after(self, cursor: int) -> AggregateDelta:
        """Return changes after a cursor."""
        ...


class ApplicationSnapshotReader(Protocol):
    """Read the global application state."""

    def snapshot(self) -> ApplicationPreferenceSnapshot:
        """Return the global application state."""
        ...


class RevisionReader(Protocol):
    """Read the global application revision."""

    changes: ChangeSignal

    def revision(self) -> int:
        """Return the current revision."""
        ...
