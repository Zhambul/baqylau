"""Typed operational evidence reported by dashboard clients."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from diagnostics.models import StateFileRecord
from domain.ids import SessionId
from repository.contract.diagnostics import DiagnosticWriteRepository
from repository.mapper import diagnostics as mapper


Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class OptimisticActionReport:
    session_id: SessionId
    action: str
    phase: str
    character_count: int | None
    elapsed_milliseconds: int | None
    reason: str | None


@dataclass(frozen=True)
class ClientFailureReport:
    session_id: SessionId
    gesture: str
    failure_kind: str
    error: str | None
    status_code: int | None
    character_count: int | None


@dataclass(frozen=True)
class BrowserEvent:
    session_id: SessionId | None
    name: str
    timestamp: int | None
    details: Mapping[str, Scalar]


@dataclass(frozen=True)
class BrowserEventBatch:
    client_id: str
    device_id: str
    connection: Mapping[str, Scalar]
    events: tuple[BrowserEvent, ...]


class BrowserTelemetryService:
    """Write browser-only observations to the operational audit."""

    def __init__(self, audit: DiagnosticWriteRepository, process_id: int = 0) -> None:
        self.audit = audit
        self.process_id = process_id

    def _record(self, action: str, content: Mapping[str, object]) -> None:
        self.audit.record_state_file(
            StateFileRecord(
                session_id="",
                path="",
                action=action,
                content=mapper.truncated(dict(content)),
                script="dashboard",
                process_id=self.process_id,
                timestamp=time.time(),
            )
        )

    def record_optimistic_action(self, report: OptimisticActionReport) -> None:
        content: dict[str, object] = {
            "session_id": str(report.session_id),
            "action": report.action,
            "phase": report.phase,
        }
        if report.character_count is not None:
            content["character_count"] = report.character_count
        if report.elapsed_milliseconds is not None:
            content["elapsed_milliseconds"] = report.elapsed_milliseconds
        if report.reason:
            content["reason"] = report.reason
        self._record("browser-optimistic-action", content)

    def record_client_failure(self, report: ClientFailureReport) -> None:
        content: dict[str, object] = {
            "session_id": str(report.session_id),
            "gesture": report.gesture,
            "failure_kind": report.failure_kind,
        }
        if report.error:
            content["error"] = report.error
        if report.status_code is not None:
            content["status_code"] = report.status_code
        if report.character_count is not None:
            content["character_count"] = report.character_count
        self._record("browser-client-failure", content)

    def record_events(self, batch: BrowserEventBatch) -> None:
        for event in batch.events:
            content: dict[str, object] = {
                "client_id": batch.client_id,
                "device_id": batch.device_id,
                "session_id": str(event.session_id) if event.session_id else "",
                "name": event.name,
                "details": dict(event.details),
                "connection": dict(batch.connection),
            }
            if event.timestamp is not None:
                content["timestamp"] = event.timestamp
            self._record("browser-event", content)
