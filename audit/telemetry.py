"""Typed operational evidence reported by dashboard clients."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from audit.models import StateFileRecord
from domain.ids import ClientId, DeviceId, SessionId
from repository.contract.audit import AuditWriteRepository
from repository.mapper import audit as mapper


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
    client_id: ClientId
    device_id: DeviceId
    connection: Mapping[str, Scalar]
    events: tuple[BrowserEvent, ...]


class BrowserTelemetryService:
    """Write browser-only observations to the operational audit."""

    def __init__(self, audit_write_repository: AuditWriteRepository, process_id: int = 0) -> None:
        self.audit_write_repository = audit_write_repository
        self.process_id = process_id

    def _record(self, action: str, content: Mapping[str, object]) -> None:
        self.audit_write_repository.record_state_file(
            StateFileRecord(
                session_id=SessionId(""),
                path="",
                action=action,
                content=mapper.truncated(dict(content)),
                script="dashboard",
                process_id=self.process_id,
                timestamp=time.time(),
            )
        )

    def record_optimistic_action(self, optimistic_action_report: OptimisticActionReport) -> None:
        content: dict[str, object] = {
            "session_id": str(optimistic_action_report.session_id),
            "action": optimistic_action_report.action,
            "phase": optimistic_action_report.phase,
        }
        if optimistic_action_report.character_count is not None:
            content["character_count"] = optimistic_action_report.character_count
        if optimistic_action_report.elapsed_milliseconds is not None:
            content["elapsed_milliseconds"] = optimistic_action_report.elapsed_milliseconds
        if optimistic_action_report.reason:
            content["reason"] = optimistic_action_report.reason
        self._record("browser-optimistic-action", content)

    def record_client_failure(self, client_failure_report: ClientFailureReport) -> None:
        content: dict[str, object] = {
            "session_id": str(client_failure_report.session_id),
            "gesture": client_failure_report.gesture,
            "failure_kind": client_failure_report.failure_kind,
        }
        if client_failure_report.error:
            content["error"] = client_failure_report.error
        if client_failure_report.status_code is not None:
            content["status_code"] = client_failure_report.status_code
        if client_failure_report.character_count is not None:
            content["character_count"] = client_failure_report.character_count
        self._record("browser-client-failure", content)

    def record_events(self, browser_event_batch: BrowserEventBatch) -> None:
        for event in browser_event_batch.events:
            content: dict[str, object] = {
                "client_id": browser_event_batch.client_id,
                "device_id": browser_event_batch.device_id,
                "session_id": str(event.session_id) if event.session_id else "",
                "name": event.name,
                "details": dict(event.details),
                "connection": dict(browser_event_batch.connection),
            }
            if event.timestamp is not None:
                content["timestamp"] = event.timestamp
            self._record("browser-event", content)
