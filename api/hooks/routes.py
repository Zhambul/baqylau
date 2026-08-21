# api/hooks/routes.py — the raw event plane's one write endpoint: pushed
# hook deliveries. The body is the hook's exact stdin bytes and is recorded,
# never parsed here; the reply rides the HTTP response back to the harness.
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from api.common.models.fields import HarnessNamePath
from api.responses import errors
from app.providers import HookGateway, Recorder
from domain.ids import AccountId, WindowId
from harness.hooks.gateway import UnknownHookHarness
from harness.models import HarnessHookRequest
from harness.hooks.headers import (
    ACCOUNT_ID_HEADER,
    ACCOUNT_NAME_HEADER,
    CLIENT_PROCESS_HEADER,
    LAUNCH_EFFORT_HEADER,
    LAUNCH_MODEL_HEADER,
    TERMINAL_WINDOW_HEADER,
)
from repository.errors import RepositoryError

router = APIRouter()


@router.post(
    "/api/harnesses/{harness}/hooks",
    responses={
        **errors({
            404: "No such harness, or one that accepts no hooks.",
            409: "That raw event id was reused for DIFFERENT bytes.",
        }),
    },
)
async def record_hook_delivery(
    harness: HarnessNamePath, request: Request, gateway: HookGateway, audit: Recorder
) -> Response:
    """One pushed hook delivery: exact stdin bytes in, the reply bytes out.

    Recording happens on the request, never behind the interpreter tick — a
    wedged tick cannot stop hook capture. Errors are audited HERE, and now
    exclusively here: a hook client in `client/` records nothing at all, so a
    delivery the daemon refused would otherwise vanish.

    The headers are read verbatim — every value is what the client OBSERVED, and
    the interpretation of it (the CLI pid behind a client pid, a valid account
    slug) happens below this, where the vocabulary lives."""
    payload = await request.body()
    try:
        process_header = (request.headers.get(CLIENT_PROCESS_HEADER) or "").strip()
        delivery = HarnessHookRequest(
            payload=payload,
            terminal_window_id=(
                WindowId(request.headers[TERMINAL_WINDOW_HEADER])
                if request.headers.get(TERMINAL_WINDOW_HEADER)
                else None
            ),
            harness_process_id=None,
            client_process_id=int(process_header) if process_header else None,
            account_id=(
                AccountId(request.headers[ACCOUNT_ID_HEADER])
                if request.headers.get(ACCOUNT_ID_HEADER)
                else None
            ),
            account_display_name=request.headers.get(ACCOUNT_NAME_HEADER) or None,
            launch_model=request.headers.get(LAUNCH_MODEL_HEADER) or None,
            launch_effort=request.headers.get(LAUNCH_EFFORT_HEADER) or None,
        )
        output = await run_in_threadpool(gateway.record, harness, delivery)
    except UnknownHookHarness as error:
        # Raised, not built: every refusal this server sends is rendered by the
        # one handler in api/app.py, from the one ErrorResponse model.
        raise HTTPException(404, str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        audit.error("", "hook delivery", {
            "harness": harness,
            "error": repr(error),
            "payload_bytes": len(payload),
        })
        raise HTTPException(400, str(error)) from error
    except RepositoryError as error:
        audit.error("", "hook delivery", {"harness": harness, "error": repr(error)})
        raise HTTPException(409, str(error)) from error
    return Response(content=output, media_type="application/json")
