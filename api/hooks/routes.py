# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the routes module."""

# api/hooks/routes.py — the raw event plane's one write endpoint: pushed
# hook deliveries. The body is the hook's exact stdin bytes and is recorded,
# never parsed here; the reply rides the HTTP response back to the harness.
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, concurrency, status
from starlette.requests import ClientDisconnect

from api.common.models.fields import HarnessNamePath
from api.responses import errors
from app import provider_audit_storage, provider_harness_sessions
from audit.harness_documents import HarnessErrorAudit
from domain.ids import AccountId, HarnessName, WindowId
from harness.hooks import gateway as hook_gateway, headers
from harness.models.hooks import (
    HarnessHookRequest,
)
from repository.errors import RepositoryError

router = APIRouter()
CLIENT_CLOSED_REQUEST = 499

HOOK_RESPONSES = errors(
    {
        404: "No such harness, or one that accepts no hooks.",
        409: "That raw event id was reused for DIFFERENT bytes.",
    },
)


def _hook_request(request: Request, payload: bytes) -> HarnessHookRequest:
    process_header = (request.headers.get(headers.CLIENT_PROCESS_HEADER) or "").strip()
    return HarnessHookRequest(
        payload=payload,
        terminal_window_id=(
            WindowId(request.headers[headers.TERMINAL_WINDOW_HEADER])
            if request.headers.get(headers.TERMINAL_WINDOW_HEADER)
            else None
        ),
        harness_process_id=None,
        client_process_id=int(process_header) if process_header else None,
        account_id=(
            AccountId(request.headers[headers.ACCOUNT_ID_HEADER])
            if request.headers.get(headers.ACCOUNT_ID_HEADER)
            else None
        ),
        account_display_name=request.headers.get(headers.ACCOUNT_NAME_HEADER) or None,
        launch_model=request.headers.get(headers.LAUNCH_MODEL_HEADER) or None,
        launch_effort=request.headers.get(headers.LAUNCH_EFFORT_HEADER) or None,
    )


@router.post(
    "/api/harnesses/{harness}/hooks",
    responses=HOOK_RESPONSES,
)
async def record_hook_delivery(
    harness: HarnessNamePath,
    request: Request,
    gateway: provider_harness_sessions.HookGateway,
    audit: provider_audit_storage.Recorder,
) -> Response:
    """One pushed hook delivery: exact stdin bytes in, the reply bytes out.

    Recording happens on the request, never behind the interpreter tick — a
    wedged tick cannot stop hook capture. Errors are audited HERE, and now
    exclusively here: a hook client in `client/` records nothing at all, so a
    delivery the daemon refused would otherwise vanish.

    The headers are read verbatim — every value is what the client OBSERVED, and
    the interpretation of it (the CLI pid behind a client pid, a valid account
    slug) happens below this, where the vocabulary lives.

    Returns:
        The response.

    Raises:
        HTTPException: If the request cannot be completed.

    """
    try:
        payload = await request.body()
    except ClientDisconnect:
        # The bounded hook client may disappear while this process is
        # descheduled. No complete delivery exists to record or audit.
        return Response(status_code=CLIENT_CLOSED_REQUEST)
    harness_name = HarnessName(harness)
    try:
        output = await concurrency.run_in_threadpool(
            gateway.record,
            harness_name,
            _hook_request(request, payload),
        )
    except hook_gateway.UnknownHookHarnessError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (KeyError, TypeError, ValueError) as error:
        audit.error(
            "",
            "hook delivery",
            HarnessErrorAudit(
                harness=harness_name,
                error=repr(error),
                payload_bytes=len(payload),
            ),
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except RepositoryError as error:
        audit.error(
            "",
            "hook delivery",
            HarnessErrorAudit(harness=harness_name, error=repr(error)),
        )
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return Response(content=output, media_type="application/json")
