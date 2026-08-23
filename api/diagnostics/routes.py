"""Read-only structured diagnostics for application pipeline progress."""

from fastapi import APIRouter, Query

from api.diagnostics.models import (
    AuditProblemResponse,
    DiagnosticsCheckpointResponse,
    DiagnosticsReportResponse,
    InterpretationProblemResponse,
)
from app.providers import Diagnostics

router = APIRouter(prefix="/api/diagnostics")


@router.get("/checkpoint")
def checkpoint(diagnostics: Diagnostics) -> DiagnosticsCheckpointResponse:
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
    after_raw_event: int = Query(0, ge=0),
    through_raw_event: int = Query(0, ge=0),
    after_audit_error: int = Query(0, ge=0),
    through_audit_error: int = Query(0, ge=0),
) -> DiagnosticsReportResponse:
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
