# Copyright (c) 2026 Zhambyl Yermagambet
"""Store accepted command and observer jobs with exact deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from baqylau_extension_api.models.scopes import ExtensionScope

type JobKind = Literal["command", "observer"]
type JobState = Literal["accepted", "running", "succeeded", "failed", "canceled", "outcome_unknown"]


@dataclass(frozen=True)
class CommandJobRequest:
    """Name one command job and its exact request key."""

    owner: str
    scope: ExtensionScope
    job_id: str
    request_key: str
    binding: str
    request: str


@dataclass(frozen=True)
class ObserverJobRequest:
    """Name one observer job, its cause, and its consumer cursor."""

    owner: str
    scope: ExtensionScope
    job_id: str
    cause_event_id: str
    binding: str
    request: str
    consumer_cursor: int


@dataclass(frozen=True)
class JobStateChange:
    """Advance one job from its exact stored revision."""

    owner: str
    scope: ExtensionScope
    job_id: str
    expected_revision: int
    state: JobState
    result: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class ExtensionJob:
    """Keep one accepted job, its binding, and its last result."""

    owner: str
    scope: ExtensionScope
    job_id: str
    kind: JobKind
    request_key: str | None
    cause_event_id: str | None
    state: JobState
    revision: int
    binding: str
    request: str
    result: str | None
    diagnostic: str | None
    consumer_cursor: int | None


class ExtensionJobRepository(Protocol):
    """Accept jobs once and advance their state with optimistic checks."""

    def accept_command(self, job: CommandJobRequest) -> ExtensionJob:
        """Store one command, returning the existing job for a repeated request key."""
        ...

    def accept_observer(self, job: ObserverJobRequest) -> ExtensionJob:
        """Store one observer job with its cause and consumer cursor."""
        ...

    def read(self, owner: str, scope: ExtensionScope, job_id: str) -> ExtensionJob | None:
        """Return one stored job, or None when it does not exist."""
        ...

    def update_state(self, change: JobStateChange) -> ExtensionJob:
        """Advance one job when its revision still matches."""
        ...
