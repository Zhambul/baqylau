# api/telemetry/browser.py — the browser's operational-evidence sinks:
# frontend-audit event batches, optimistic-UI lifecycles, failed gestures.
from __future__ import annotations

from fastapi import APIRouter

from api.common.models.replies.recorded_response import RecordedResponse
from api.telemetry.models.browser_events_request import BrowserEventsRequest
from api.telemetry.models.client_failure_request import ClientFailureRequest
from api.telemetry.models.optimistic_action_request import (
    OptimisticActionRequest,
)
from api.common.models.fields import SessionIdPath
from app.providers import BrowserTelemetry
from audit.telemetry import (
    BrowserEvent,
    BrowserEventBatch,
    ClientFailureReport,
    OptimisticActionReport,
)
from domain.ids import SessionId

router = APIRouter()


@router.post("/api/application/browser-events")
def record_browser_events(
    body: BrowserEventsRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_events(
        BrowserEventBatch(
            body.client_id,
            body.device_id,
            body.connection,
            tuple(
                BrowserEvent(
                    SessionId(event.session_id) if event.session_id else None,
                    event.name,
                    event.timestamp,
                    event.details,
                )
                for event in body.events
            ),
        )
    )
    return RecordedResponse()


@router.post("/api/sessions/{session_id}/application/optimistic-actions")
def record_optimistic_action(
    session_id: SessionIdPath, body: OptimisticActionRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_optimistic_action(
        OptimisticActionReport(
            SessionId(session_id),
            body.action,
            body.phase,
            body.character_count,
            body.elapsed_milliseconds,
            body.reason or None,
        )
    )
    return RecordedResponse()


@router.post("/api/sessions/{session_id}/application/client-failures")
def record_client_failure(
    session_id: SessionIdPath, body: ClientFailureRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_client_failure(
        ClientFailureReport(
            SessionId(session_id),
            body.gesture,
            body.failure_kind,
            body.error,
            body.status_code,
            body.character_count,
        )
    )
    return RecordedResponse()
