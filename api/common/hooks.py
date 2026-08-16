# api/common/hooks.py — the evidence plane's one write endpoint: pushed
# hook deliveries. The body is the hook's exact stdin bytes and is recorded,
# never parsed here; the reply rides the HTTP response back to the harness.
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from api.guard import control_plane
from harness.hooks.gateway import UnknownHookHarness
from harness.models import HarnessHookRequest
from diagnostics import record as A
from harness.hooks.headers import (
    ACCOUNT_ID_HEADER,
    ACCOUNT_NAME_HEADER,
    HARNESS_PROCESS_HEADER,
    LAUNCH_EFFORT_HEADER,
    LAUNCH_MODEL_HEADER,
    TERMINAL_WINDOW_HEADER,
    HOOK_MAX,
)
from engine.store.recorder import RawEventRecorderError

router = APIRouter()


@router.post(
    "/api/harnesses/{harness}/hooks",
    dependencies=[Depends(control_plane(HOOK_MAX))],
)
async def record_hook_delivery(harness: str, request: Request) -> Response:
    """One pushed hook delivery: exact stdin bytes in, the reply bytes out.

    Recording happens on the request, never behind the interpreter tick — a
    wedged tick cannot stop hook capture. Errors are audited HERE: the hook
    client swallows everything (a hook must never fail its harness), so a
    delivery the daemon refused would otherwise vanish."""
    payload = await request.body()
    application = request.app.state.canonical_application
    try:
        process_header = (request.headers.get(HARNESS_PROCESS_HEADER) or "").strip()
        delivery = HarnessHookRequest(
            payload=payload,
            terminal_window_id=request.headers.get(TERMINAL_WINDOW_HEADER) or None,
            harness_process_id=int(process_header) if process_header else None,
            account_id=request.headers.get(ACCOUNT_ID_HEADER) or None,
            account_display_name=request.headers.get(ACCOUNT_NAME_HEADER) or None,
            launch_model=request.headers.get(LAUNCH_MODEL_HEADER) or None,
            launch_effort=request.headers.get(LAUNCH_EFFORT_HEADER) or None,
        )
        output = await run_in_threadpool(application.hook_gateway.record, harness, delivery)
    except UnknownHookHarness as error:
        return JSONResponse({"error": str(error)}, 404)
    except (KeyError, TypeError, ValueError) as error:
        A.error("", "hook delivery", {
            "harness": harness,
            "error": repr(error),
            "payload_bytes": len(payload),
        })
        return JSONResponse({"error": str(error)}, 400)
    except RawEventRecorderError as error:
        A.error("", "hook delivery", {"harness": harness, "error": repr(error)})
        return JSONResponse({"error": str(error)}, 409)
    return Response(content=output, media_type="application/json")
