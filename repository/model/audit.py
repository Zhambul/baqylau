"""Row shapes for the four audit tables."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import ActorId, SessionId, TaskId


@dataclass(frozen=True)
class ErrorRow:
    id: int
    ts: float
    session_id: SessionId
    script: str
    func: str
    traceback: str
    context: str
    pid: int


@dataclass(frozen=True)
class StateFileRow:
    id: int
    ts: float
    session_id: SessionId
    path: str
    action: str
    content: str
    script: str
    pid: int


@dataclass(frozen=True)
class SpawnRow:
    id: int
    ts: float
    session_id: SessionId
    parent_script: str
    child_pid: int
    argv: str
    purpose: str


@dataclass(frozen=True)
class StreamRow:
    id: int
    session_id: SessionId
    kind: str
    agent_id: ActorId
    task_id: TaskId
    src_path: str
    pid: int
    started_at: float
    ended_at: float | None
    end_reason: str | None
    lines_emitted: int | None
