"""Read-only progress and problem records for the application pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.ids import SessionId


@dataclass(frozen=True)
class DiagnosticsCheckpoint:
    raw_event_cursor: int
    audit_error_cursor: int
    canonical_cursor: int
    reaction_cursor: int
    pending_raw_event_count: int


@dataclass(frozen=True)
class InterpretationProblem:
    raw_event_cursor: int
    source_type: str
    source_position: str
    decision: str | None
    reason: str | None
    payload: str


@dataclass(frozen=True)
class AuditProblem:
    error_cursor: int
    session_id: SessionId
    component: str
    action: str
    context: str


@dataclass(frozen=True)
class DiagnosticsReport:
    raw_event_count: int
    verdict_count: int
    interpretation_problems: tuple[InterpretationProblem, ...]
    audit_problems: tuple[AuditProblem, ...]


class DiagnosticsRepository(Protocol):
    def checkpoint(self) -> DiagnosticsCheckpoint: ...

    def report(
        self,
        *,
        after_raw_event: int,
        through_raw_event: int,
        after_audit_error: int,
        through_audit_error: int,
    ) -> DiagnosticsReport: ...
