# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the harness module."""

# api/telemetry/harness.py — the raw event plane's second write endpoint: pushed
# telemetry. Like the hook endpoint beside it, the body is exact bytes and is
# recorded, never parsed here; unlike it, there is no reply to hand back.
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from api.common.models.fields import HarnessNamePath
from api.common.models.replies.recorded_response import RecordedResponse
from app import provider_audit_storage, provider_harness_sessions
from audit.harness_documents import HarnessErrorAudit
from domain.ids import HarnessName
from harness.models.telemetry import (
    TELEMETRY_KIND_HEADER,
    HarnessTelemetryRequest,
)
from harness.services.telemetry import UnknownTelemetryHarnessError
from repository.errors import RepositoryError

router = APIRouter()
TELEMETRY_RECORD_ERRORS = (KeyError, TypeError, ValueError, RepositoryError)


@router.post(
    "/api/harnesses/{harness}/telemetry",
    response_model=RecordedResponse,
)
async def record_telemetry_delivery(
    harness: HarnessNamePath,
    request: Request,
    gateway: provider_harness_sessions.TelemetryGateway,
    audit: provider_audit_storage.Recorder,
) -> RecordedResponse:
    """One pushed telemetry delivery: exact bytes in, a bare acknowledgement out.

    Recording happens on the request, never behind the interpreter tick — a
    wedged tick cannot stop telemetry capture. Errors are audited HERE, because
    the clients that ship these swallow everything (a status-line shim must
    never break the status line), so a delivery the daemon refused would
    otherwise vanish.

    Returns:
        The recorded response.

    """
    try:
        payload = await request.body()
    except ClientDisconnect:
        # Status/OTLP publishers are intentionally best-effort. A publisher
        # leaving mid-body is a missing delivery, not an application error.
        return RecordedResponse(recorded=False)
    delivery = HarnessTelemetryRequest(
        kind=(request.headers.get(TELEMETRY_KIND_HEADER) or "").strip(),
        payload=payload,
    )
    harness_name = HarnessName(harness)
    try:
        # On a worker thread, like the hook endpoint beside it: `record` writes
        # to the store, and this handler is `async` (it awaits the raw body), so
        # a direct call would do that write on the event loop and stall every
        # open stream with it.
        await run_in_threadpool(gateway.record, harness_name, delivery)
    except UnknownTelemetryHarnessError as error:
        audit.error(
            "",
            "telemetry delivery",
            HarnessErrorAudit(harness=harness_name, error=str(error)),
        )
        return RecordedResponse(recorded=False)
    except TELEMETRY_RECORD_ERRORS as error:
        audit.error(
            "",
            "telemetry delivery",
            HarnessErrorAudit(
                harness=harness_name,
                kind=delivery.kind,
                error=repr(error),
                payload_bytes=len(payload),
            ),
        )
        return RecordedResponse(recorded=False)
    return RecordedResponse()
