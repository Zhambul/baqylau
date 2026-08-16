# api/dashboard/telemetry.py — the browser's operational-evidence sinks:
# frontend-audit event batches, optimistic-UI lifecycles, failed gestures.
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.common.models.replies.recorded_response import RecordedResponse
from api.dashboard.models.telemetry.browser_events_request import BrowserEventsRequest
from api.dashboard.models.telemetry.client_failure_request import ClientFailureRequest
from api.dashboard.models.telemetry.optimistic_action_request import (
    OptimisticActionRequest,
)
from api.dependencies import ApplicationGraph
from api.guard import control_plane
from app.telemetry import (
    BrowserEvent,
    BrowserEventBatch,
    ClientFailureReport,
    OptimisticActionReport,
)
from domain.ids import SessionId

router = APIRouter(dependencies=[Depends(control_plane())])


@router.post("/api/application/browser-events")
def record_browser_events(
    body: BrowserEventsRequest, application: ApplicationGraph
) -> RecordedResponse:
    application.browser_telemetry.record_events(
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
    session_id: str, body: OptimisticActionRequest, application: ApplicationGraph
) -> RecordedResponse:
    application.browser_telemetry.record_optimistic_action(
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
    session_id: str, body: ClientFailureRequest, application: ApplicationGraph
) -> RecordedResponse:
    application.browser_telemetry.record_client_failure(
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
