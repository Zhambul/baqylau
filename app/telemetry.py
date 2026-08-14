"""Typed operational evidence reported by dashboard clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from domain.ids import SessionId


Scalar = str | int | float | bool | None


class OperationalAudit(Protocol):
    def state_file(
        self,
        log: str,
        path: str,
        action: str,
        content: dict,
    ) -> None: ...


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

    def __init__(self, audit: OperationalAudit) -> None:
        self.audit = audit

    def record_optimistic_action(self, report: OptimisticActionReport) -> None:
        content = {
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
        self.audit.state_file("", "", "browser-optimistic-action", content)

    def record_client_failure(self, report: ClientFailureReport) -> None:
        content = {
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
        self.audit.state_file("", "", "browser-client-failure", content)

    def record_events(self, batch: BrowserEventBatch) -> None:
        for event in batch.events:
            content = {
                "client_id": batch.client_id,
                "device_id": batch.device_id,
                "session_id": str(event.session_id) if event.session_id else "",
                "name": event.name,
                "details": dict(event.details),
                "connection": dict(batch.connection),
            }
            if event.timestamp is not None:
                content["timestamp"] = event.timestamp
            self.audit.state_file("", "", "browser-event", content)
