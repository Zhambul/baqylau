# api/dashboard/controls.py — the control plane: launching sessions and one
# endpoint per gesture (the URL is the discriminator; the old single funnel
# took a `control_name` field and was a god endpoint).
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from api.dashboard.models.controls.answer_question_request import AnswerQuestionRequest
from api.dashboard.models.controls.apply_rewind_request import ApplyRewindRequest
from api.dashboard.models.controls.auto_name_session_request import AutoNameSessionRequest
from api.dashboard.models.controls.close_session_request import CloseSessionRequest
from api.dashboard.models.controls.compact_request import CompactRequest
from api.dashboard.models.controls.decide_plan_request import DecidePlanRequest
from api.dashboard.models.controls.interrupt_request import InterruptRequest
from api.dashboard.models.controls.migrate_account_request import MigrateAccountRequest
from api.dashboard.models.controls.open_rewind_request import OpenRewindRequest
from api.dashboard.models.controls.read_plan_choices_request import ReadPlanChoicesRequest
from api.dashboard.models.controls.rename_session_request import RenameSessionRequest
from api.dashboard.models.controls.select_effort_request import SelectEffortRequest
from api.dashboard.models.controls.select_model_request import SelectModelRequest
from api.dashboard.models.controls.send_text_request import SendTextRequest
from api.dashboard.models.launch.launch_session_request import LaunchSessionRequest
from api.dependencies import ApplicationGraph
from api.guard import control_plane
from dashboard.render.serialize import json_ready
from domain.ids import SessionId
from harness.models import ControlOutcome, LaunchResult

router = APIRouter(dependencies=[Depends(control_plane())])

# The BODY is `json_ready`'s, not pydantic's: these replies are frozen
# dataclasses whose encoding the browser already depends on (a cost is a string,
# not a float). `response_model` declares the shape for OpenAPI without changing
# a byte of it — the contract becomes readable, the wire stays put.
LAUNCH_STATUS = {"started": 202, "rejected": 409}
CONTROL_STATUS = {"acknowledged": 200, "indeterminate": 202, "rejected": 409}


@router.post("/api/sessions", response_model=LaunchResult)
def launch(body: LaunchSessionRequest, application: ApplicationGraph) -> JSONResponse:
    result = application.launcher.launch(body.harness, body.request())
    return JSONResponse(json_ready(result), LAUNCH_STATUS[result.status])


def _execute(application, session_id: str, body) -> JSONResponse:
    """One gesture: the request model builds its harness dataclass, the
    audited control service executes it, the outcome's status picks the code."""
    outcome = application.controls.execute(body.request(SessionId(session_id)))
    return JSONResponse(json_ready(outcome), CONTROL_STATUS[outcome.status])


@router.post("/api/sessions/{session_id}/controls/send-text", response_model=ControlOutcome)
def send_text(session_id: str, body: SendTextRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/interrupt", response_model=ControlOutcome)
def interrupt(session_id: str, body: InterruptRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/close-session", response_model=ControlOutcome)
def close_session(session_id: str, body: CloseSessionRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/rename-session", response_model=ControlOutcome)
def rename_session(session_id: str, body: RenameSessionRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/auto-name-session", response_model=ControlOutcome)
def auto_name_session(session_id: str, body: AutoNameSessionRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/open-rewind", response_model=ControlOutcome)
def open_rewind(session_id: str, body: OpenRewindRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/apply-rewind", response_model=ControlOutcome)
def apply_rewind(session_id: str, body: ApplyRewindRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/migrate-account", response_model=ControlOutcome)
def migrate_account(session_id: str, body: MigrateAccountRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/compact", response_model=ControlOutcome)
def compact(session_id: str, body: CompactRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/select-model", response_model=ControlOutcome)
def select_model(session_id: str, body: SelectModelRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/select-effort", response_model=ControlOutcome)
def select_effort(session_id: str, body: SelectEffortRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/answer-question", response_model=ControlOutcome)
def answer_question(session_id: str, body: AnswerQuestionRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/read-plan-choices", response_model=ControlOutcome)
def read_plan_choices(session_id: str, body: ReadPlanChoicesRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)


@router.post("/api/sessions/{session_id}/controls/decide-plan", response_model=ControlOutcome)
def decide_plan(session_id: str, body: DecidePlanRequest, application: ApplicationGraph) -> JSONResponse:
    return _execute(application, session_id, body)
