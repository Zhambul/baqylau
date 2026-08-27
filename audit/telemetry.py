"""Typed operational raw events reported by dashboard clients."""

from __future__ import annotations

import time
from typing import Mapping

from audit.models import AuditDocument, StateFileRecord
from domain.ids import ClientId, DeviceId, SessionId
from repository.contract.audit import AuditWriteRepository
from repository.mapper import audit as mapper


Scalar = str | int | float | bool | None


class OptimisticActionReport(AuditDocument):
    session_id: SessionId
    action: str
    phase: str
    character_count: int | None
    elapsed_milliseconds: int | None
    reason: str | None


class ClientFailureReport(AuditDocument):
    session_id: SessionId
    gesture: str
    failure_kind: str
    error: str | None
    status_code: int | None
    character_count: int | None


class BrowserEvent(AuditDocument):
    session_id: SessionId | None
    name: str
    timestamp: int | None
    details: Mapping[str, Scalar]


class BrowserEventBatch(AuditDocument):
    client_id: ClientId
    device_id: DeviceId
    connection: Mapping[str, Scalar]
    events: tuple[BrowserEvent, ...]


class BrowserEventAudit(AuditDocument):
    client_id: ClientId
    device_id: DeviceId
    session_id: SessionId | None
    name: str
    details: Mapping[str, Scalar]
    connection: Mapping[str, Scalar]
    timestamp: int | None


class BrowserTelemetryService:
    """Write browser-only observations to the operational audit."""

    def __init__(self, audit_write_repository: AuditWriteRepository, process_id: int = 0) -> None:
        self.audit_write_repository = audit_write_repository
        self.process_id = process_id

    def _record(self, action: str, audit_document: AuditDocument) -> None:
        self.audit_write_repository.record_state_file(
            StateFileRecord(
                session_id=SessionId(""),
                path="",
                action=action,
                content=mapper.truncated(audit_document),
                script="dashboard",
                process_id=self.process_id,
                timestamp=time.time(),
            )
        )

    def record_optimistic_action(self, optimistic_action_report: OptimisticActionReport) -> None:
        self._record("browser-optimistic-action", optimistic_action_report)

    def record_client_failure(self, client_failure_report: ClientFailureReport) -> None:
        self._record("browser-client-failure", client_failure_report)

    def record_events(self, browser_event_batch: BrowserEventBatch) -> None:
        for event in browser_event_batch.events:
            self._record(
                "browser-event",
                BrowserEventAudit(
                    client_id=browser_event_batch.client_id,
                    device_id=browser_event_batch.device_id,
                    session_id=event.session_id,
                    name=event.name,
                    details=event.details,
                    connection=browser_event_batch.connection,
                    timestamp=event.timestamp,
                ),
            )
