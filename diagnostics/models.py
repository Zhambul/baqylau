"""The operational vocabulary: what the MACHINERY did, as types.

Harness facts live in `main.db` and belong to `domain/`. Everything named here
is application mechanics instead: a swallowed exception, a state file we wrote,
a child we spawned, a stream that opened and closed.

The write side used to be positional arguments to five free functions and the
read side a separately-shaped dataclass; one column list, spelled twice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationErrorRecord:
    """A swallowed exception, on the way in."""

    session_id: str
    script: str
    function: str
    traceback: str
    context: str
    process_id: int
    timestamp: float


@dataclass(frozen=True)
class ApplicationError:
    """A swallowed exception, on the way out — what the dashboard renders."""

    error_id: int
    timestamp: float
    component: str
    action: str
    traceback: str
    context: str


@dataclass(frozen=True)
class StateFileRecord:
    session_id: str
    path: str
    action: str
    content: str
    script: str
    process_id: int
    timestamp: float


@dataclass(frozen=True)
class SpawnRecord:
    session_id: str
    parent_script: str
    child_process_id: int
    argv: str
    purpose: str
    timestamp: float


@dataclass(frozen=True)
class StreamOpened:
    session_id: str
    kind: str
    agent_id: str
    task_id: str
    source_path: str
    process_id: int
    started_at: float


@dataclass(frozen=True)
class StreamHandle:
    """What a caller holds between opening a stream row and closing it.

    A typed handle rather than a bare `lastrowid`, so the close cannot be
    called with some other integer that happens to be in scope.
    """

    stream_id: int

