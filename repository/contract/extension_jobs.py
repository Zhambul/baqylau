# Copyright (c) 2026 Zhambyl Yermagambet
"""Store accepted command and observer jobs with exact deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from baqylau_extension_api.models.scopes import ExtensionScope

from domain.extension_jobs import JobKind, JobState
from domain.ids import CanonicalEventId, ExtensionJobId


@dataclass(frozen=True)
class CommandJobRequest:
    """Name one command job and its exact request key."""

    owner: str
    scope: ExtensionScope
    job_id: ExtensionJobId
    request_key: str
    binding: str
    request: str


@dataclass(frozen=True)
class ObserverJobRequest:
    """Name one observer job, its cause, and its consumer cursor."""

    owner: str
    scope: ExtensionScope
    job_id: ExtensionJobId
    cause_event_id: CanonicalEventId
    binding: str
    request: str
    consumer_cursor: int


@dataclass(frozen=True)
class JobStateChange:
    """Advance one job from its exact stored revision; a claim can also replace its binding and request."""

    owner: str
    scope: ExtensionScope
    job_id: ExtensionJobId
    expected_revision: int
    state: JobState
    result: str | None = None
    diagnostic: str | None = None
    binding: str | None = None
    request: str | None = None


@dataclass(frozen=True)
class ExtensionJob:
    """Keep one accepted job, its binding, and its last result."""

    owner: str
    scope: ExtensionScope
    job_id: ExtensionJobId
    kind: JobKind
    request_key: str | None
    cause_event_id: CanonicalEventId | None
    state: JobState
    revision: int
    binding: str
    request: str
    result: str | None
    diagnostic: str | None
    consumer_cursor: int | None


class ExtensionJobRepository(Protocol):
    """Accept jobs once and advance their state with optimistic checks."""

    def accept_command(self, command_job_request: CommandJobRequest) -> ExtensionJob:
        """Store one command, returning the existing job for a repeated request key."""
        ...

    def accept_observer(self, observer_job_request: ObserverJobRequest) -> ExtensionJob:
        """Store one observer job with its cause and consumer cursor."""
        ...

    def read(self, owner: str, scope: ExtensionScope, job_id: ExtensionJobId) -> ExtensionJob | None:
        """Return one stored job, or None when it does not exist."""
        ...

    def jobs_in_state(self, job_state: JobState, limit: int) -> tuple[ExtensionJob, ...]:
        """Return the oldest jobs in one state, for recovery and scheduling."""
        ...

    def open_jobs(self, owner: str) -> int:
        """Count the owner's jobs that are accepted or running."""
        ...

    def update_state(self, job_state_change: JobStateChange) -> ExtensionJob:
        """Advance one job when its revision still matches."""
        ...
