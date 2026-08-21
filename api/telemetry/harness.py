# api/telemetry/harness.py — the evidence plane's second write endpoint: pushed
# telemetry. Like the hook endpoint beside it, the body is exact bytes and is
# recorded, never parsed here; unlike it, there is no reply to hand back.
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from api.common.models.fields import HarnessNamePath
from api.common.models.replies.recorded_response import RecordedResponse
from app.providers import TelemetryGateway, Recorder
from harness.models import TELEMETRY_KIND_HEADER, HarnessTelemetryRequest
from harness.services.telemetry import UnknownTelemetryHarness
from repository.errors import RepositoryError

router = APIRouter()


@router.post(
    "/api/harnesses/{harness}/telemetry",
)
async def record_telemetry_delivery(
    harness: HarnessNamePath, request: Request, gateway: TelemetryGateway, audit: Recorder
) -> RecordedResponse:
    """One pushed telemetry delivery: exact bytes in, a bare acknowledgement out.

    Recording happens on the request, never behind the interpreter tick — a
    wedged tick cannot stop telemetry capture. Errors are audited HERE, because
    the clients that ship these swallow everything (a status-line shim must
    never break the status line), so a delivery the daemon refused would
    otherwise vanish.
    """
    payload = await request.body()
    delivery = HarnessTelemetryRequest(
        kind=(request.headers.get(TELEMETRY_KIND_HEADER) or "").strip(),
        payload=payload,
    )
    try:
        # On a worker thread, like the hook endpoint beside it: `record` writes
        # to the store, and this handler is `async` (it awaits the raw body), so
        # a direct call would do that write on the event loop and stall every
        # open stream with it.
        await run_in_threadpool(gateway.record, harness, delivery)
    except UnknownTelemetryHarness as error:
        audit.error("", "telemetry delivery", {"harness": harness, "error": str(error)})
        return RecordedResponse(recorded=False)
    except (KeyError, TypeError, ValueError, RepositoryError) as error:
        audit.error("", "telemetry delivery", {
            "harness": harness,
            "kind": delivery.kind,
            "error": repr(error),
            "payload_bytes": len(payload),
        })
        return RecordedResponse(recorded=False)
    return RecordedResponse()
