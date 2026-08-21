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
from domain.ids import ClientId, DeviceId, SessionId

router = APIRouter()


@router.post("/api/application/browser-events")
def record_browser_events(
    browser_events_request: BrowserEventsRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_events(
        BrowserEventBatch(
            ClientId(browser_events_request.client_id),
            DeviceId(browser_events_request.device_id),
            browser_events_request.connection,
            tuple(
                BrowserEvent(
                    SessionId(event.session_id) if event.session_id else None,
                    event.name,
                    event.timestamp,
                    event.details,
                )
                for event in browser_events_request.events
            ),
        )
    )
    return RecordedResponse()


@router.post("/api/sessions/{session_id}/application/optimistic-actions")
def record_optimistic_action(
    session_id: SessionIdPath, optimistic_action_request: OptimisticActionRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_optimistic_action(
        OptimisticActionReport(
            SessionId(session_id),
            optimistic_action_request.action,
            optimistic_action_request.phase,
            optimistic_action_request.character_count,
            optimistic_action_request.elapsed_milliseconds,
            optimistic_action_request.reason or None,
        )
    )
    return RecordedResponse()


@router.post("/api/sessions/{session_id}/application/client-failures")
def record_client_failure(
    session_id: SessionIdPath, client_failure_request: ClientFailureRequest, telemetry: BrowserTelemetry
) -> RecordedResponse:
    telemetry.record_client_failure(
        ClientFailureReport(
            SessionId(session_id),
            client_failure_request.gesture,
            client_failure_request.failure_kind,
            client_failure_request.error,
            client_failure_request.status_code,
            client_failure_request.character_count,
        )
    )
    return RecordedResponse()
