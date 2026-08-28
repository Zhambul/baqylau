"""Structured application pipeline diagnostics."""

from pydantic import BaseModel


class DiagnosticsCheckpointResponse(BaseModel):
    raw_event_cursor: int
    audit_error_cursor: int
    canonical_cursor: int
    reaction_cursor: int
    pending_raw_event_count: int


class InterpretationProblemResponse(BaseModel):
    raw_event_cursor: int
    source_type: str
    source_position: str
    decision: str | None
    reason: str | None
    payload: str


class AuditProblemResponse(BaseModel):
    error_cursor: int
    session_id: str
    component: str
    action: str
    context: str


class DiagnosticsReportResponse(BaseModel):
    raw_event_count: int
    verdict_count: int
    interpretation_problems: tuple[InterpretationProblemResponse, ...]
    audit_problems: tuple[AuditProblemResponse, ...]


class TerminalProcessDiagnosticResponse(BaseModel):
    process_id: int | None
    command: tuple[str, ...]


class TerminalWindowDiagnosticResponse(BaseModel):
    window_id: str
    processes: tuple[TerminalProcessDiagnosticResponse, ...]
    screen: str | None
    screen_error: str | None


class TerminalDiagnosticsResponse(BaseModel):
    windows: tuple[TerminalWindowDiagnosticResponse, ...]
