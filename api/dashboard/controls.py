# api/dashboard/controls.py — the control plane: launching sessions and one
# endpoint per gesture (the URL is the discriminator; the old single funnel
# took a `control_name` field and was a god endpoint).
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from api.dashboard.models.controls.answer_question_request import AnswerQuestionRequest
from api.dashboard.models.controls.apply_rewind_request import ApplyRewindRequest
from api.dashboard.models.controls.auto_name_session_request import AutoNameSessionRequest
from api.dashboard.models.controls.close_session_request import CloseSessionRequest
from api.dashboard.models.controls.compact_request import CompactRequest
from api.dashboard.models.controls.control_request import ControlRequestBody
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
from api.common.models.fields import SessionIdPath
from app.providers import Controls, Launcher
from api.guard import control_plane
from api.dashboard.mapper import controls as mapper
from api.dashboard.models.controls.control_outcome_response import ControlOutcomeResponse
from api.dashboard.models.launch.launch_response import LaunchResponse
from api.responses import GUARDED, with_body
from domain.ids import SessionId
from harness.services.controls import HarnessControlService

router = APIRouter(dependencies=[Depends(control_plane())], responses=GUARDED)

# The route names an api MODEL as its return type and returns one, so the
# published schema and the bytes are the same statement. The harness layer's own
# result dataclasses never reach the wire: `control_outcome` maps one to the
# api model that mirrors it, and nothing here builds a dict.
LAUNCH_STATUS = {"started": 202, "rejected": 409}
CONTROL_STATUS = {"acknowledged": 200, "indeterminate": 202, "rejected": 409}

# ...and the STATUS is the outcome's, so the schema has to name all three or it
# describes a plane that always succeeds. Every one of them carries the SAME body
# as the default: a rejection here is a verdict, not an error.
LAUNCH_RESPONSES = with_body(LaunchResponse, {
    409: "Rejected — nothing was launched.",
})
CONTROL_RESPONSES = with_body(ControlOutcomeResponse, {
    202: "Sent, but the effect is unconfirmed — the browser reconciles from the stream.",
    409: "Rejected — the session cannot take this gesture now.",
})


@router.post("/api/sessions", status_code=202, responses=LAUNCH_RESPONSES)
def launch(
    body: LaunchSessionRequest, launcher: Launcher, response: Response
) -> LaunchResponse:
    result = launcher.launch(body.harness, body.request())
    response.status_code = LAUNCH_STATUS[result.status]
    return mapper.launch(result)


def _execute(
    controls: HarnessControlService,
    session_id: str,
    body: ControlRequestBody,
    response: Response,
) -> ControlOutcomeResponse:
    """One gesture: the request model builds its harness dataclass, the
    audited control service executes it, the outcome's status picks the code.

    The status is set ON the injected response rather than by wrapping the body
    in one, which is what lets the handler return the outcome ITSELF.
    """
    outcome = controls.execute(body.request(SessionId(session_id)))
    response.status_code = CONTROL_STATUS[outcome.status]
    return mapper.control_outcome(outcome)


@router.post("/api/sessions/{session_id}/controls/send-text",
             responses=CONTROL_RESPONSES)
def send_text(
    session_id: SessionIdPath, body: SendTextRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/interrupt",
             responses=CONTROL_RESPONSES)
def interrupt(
    session_id: SessionIdPath, body: InterruptRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/close-session",
             responses=CONTROL_RESPONSES)
def close_session(
    session_id: SessionIdPath, body: CloseSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/rename-session",
             responses=CONTROL_RESPONSES)
def rename_session(
    session_id: SessionIdPath, body: RenameSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/auto-name-session",
             responses=CONTROL_RESPONSES)
def auto_name_session(
    session_id: SessionIdPath, body: AutoNameSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/open-rewind",
             responses=CONTROL_RESPONSES)
def open_rewind(
    session_id: SessionIdPath, body: OpenRewindRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/apply-rewind",
             responses=CONTROL_RESPONSES)
def apply_rewind(
    session_id: SessionIdPath, body: ApplyRewindRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/migrate-account",
             responses=CONTROL_RESPONSES)
def migrate_account(
    session_id: SessionIdPath, body: MigrateAccountRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/compact",
             responses=CONTROL_RESPONSES)
def compact(
    session_id: SessionIdPath, body: CompactRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/select-model",
             responses=CONTROL_RESPONSES)
def select_model(
    session_id: SessionIdPath, body: SelectModelRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/select-effort",
             responses=CONTROL_RESPONSES)
def select_effort(
    session_id: SessionIdPath, body: SelectEffortRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/answer-question",
             responses=CONTROL_RESPONSES)
def answer_question(
    session_id: SessionIdPath, body: AnswerQuestionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/read-plan-choices",
             responses=CONTROL_RESPONSES)
def read_plan_choices(
    session_id: SessionIdPath, body: ReadPlanChoicesRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)


@router.post("/api/sessions/{session_id}/controls/decide-plan",
             responses=CONTROL_RESPONSES)
def decide_plan(
    session_id: SessionIdPath, body: DecidePlanRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, body, response)
