# Copyright (c) 2026 Zhambyl Yermagambet
"""Read-only structured diagnostics for application pipeline progress."""

from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from api.diagnostics.models import (
    AuditProblemResponse,
    DiagnosticsCheckpointResponse,
    DiagnosticsReportResponse,
    InterpretationProblemResponse,
    TerminalDiagnosticsResponse,
    TerminalProcessDiagnosticResponse,
    TerminalWindowDiagnosticResponse,
)
from api.diagnostics.raw_event_audit_models import (
    RawEventAuditResponse,
    raw_event_audit_response,
)
from app.provider_databases import Diagnostics, RawEventAudits
from app.provider_runtime import InstalledTerminal
from domain.ids import RawEventId
from terminal.models.viewport import ScreenReadRequest

router = APIRouter(prefix="/api/diagnostics")


@router.get("/checkpoint")
def checkpoint(diagnostics: Diagnostics) -> DiagnosticsCheckpointResponse:
    """Return the checkpoint.

    Returns:
        Checkpoint.

    """
    found = diagnostics.checkpoint()
    return DiagnosticsCheckpointResponse(
        raw_event_cursor=found.raw_event_cursor,
        audit_error_cursor=found.audit_error_cursor,
        canonical_cursor=found.canonical_cursor,
        reaction_cursor=found.reaction_cursor,
        pending_raw_event_count=found.pending_raw_event_count,
    )


@router.get("/report")
def report(
    diagnostics: Diagnostics,
    after_raw_event: Annotated[int, Query(ge=0)] = 0,
    through_raw_event: Annotated[int, Query(ge=0)] = 0,
    after_audit_error: Annotated[int, Query(ge=0)] = 0,
    through_audit_error: Annotated[int, Query(ge=0)] = 0,
) -> DiagnosticsReportResponse:
    """Report.

    Returns:
        The diagnostics report response.

    """
    found = diagnostics.report(
        after_raw_event=after_raw_event,
        through_raw_event=through_raw_event,
        after_audit_error=after_audit_error,
        through_audit_error=through_audit_error,
    )
    return DiagnosticsReportResponse(
        raw_event_count=found.raw_event_count,
        verdict_count=found.verdict_count,
        interpretation_problems=tuple(
            InterpretationProblemResponse(
                raw_event_cursor=problem.raw_event_cursor,
                source_type=problem.source_type,
                source_position=problem.source_position,
                decision=problem.decision,
                reason=problem.reason,
                payload=problem.payload,
            )
            for problem in found.interpretation_problems
        ),
        audit_problems=tuple(
            AuditProblemResponse(
                error_cursor=problem.error_cursor,
                session_id=problem.session_id,
                component=problem.component,
                action=problem.action,
                context=problem.context,
            )
            for problem in found.audit_problems
        ),
    )


@router.get("/raw-events/{raw_event_id}")
def raw_event_audit(
    raw_event_id: str,
    audits: RawEventAudits,
) -> RawEventAuditResponse:
    """Return the bounded audit of one raw event.

    Returns:
        The bounded raw-event audit.

    Raises:
        HTTPException: If no raw event has the selected identity.

    """
    found = audits.audit(RawEventId(raw_event_id))
    if found is None:
        raise HTTPException(HTTPStatus.NOT_FOUND, "raw event not found")
    return raw_event_audit_response(found)


@router.get("/terminal")
def terminal_diagnostics(
    terminal_plugin: InstalledTerminal,
) -> TerminalDiagnosticsResponse:
    """Return bounded visible terminal state for failure diagnosis.

    Returns:
        Bounded visible terminal state for failure diagnosis.

    """
    windows = []
    for window in terminal_plugin.metadata.windows():
        screen = terminal_plugin.viewport.read_screen(
            ScreenReadRequest(window.window_id),
        )
        windows.append(
            TerminalWindowDiagnosticResponse(
                window_id=str(window.window_id),
                processes=tuple(
                    TerminalProcessDiagnosticResponse(
                        process_id=process.process_id,
                        command=process.command,
                    )
                    for process in window.processes
                ),
                screen=screen.text if screen.succeeded else None,
                screen_error=None if screen.succeeded else screen.reason,
            ),
        )
    return TerminalDiagnosticsResponse(windows=tuple(windows))
