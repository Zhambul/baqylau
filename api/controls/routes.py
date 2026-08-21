# api/controls/routes.py — the control plane: launching sessions and one
# endpoint per gesture (the URL is the discriminator; the old single funnel
# took a `control_name` field and was a god endpoint).
from __future__ import annotations

from fastapi import APIRouter, Response

from api.controls.models.answer_question_request import AnswerQuestionRequest
from api.controls.models.apply_rewind_request import ApplyRewindRequest
from api.controls.models.auto_name_session_request import AutoNameSessionRequest
from api.controls.models.background_request import BackgroundRequest
from api.controls.models.close_session_request import CloseSessionRequest
from api.controls.models.compact_request import CompactRequest
from api.controls.models.control_request import ControlRequestBody
from api.controls.models.decide_plan_request import DecidePlanRequest
from api.controls.models.interrupt_request import InterruptRequest
from api.controls.models.open_rewind_request import OpenRewindRequest
from api.controls.models.read_plan_choices_request import ReadPlanChoicesRequest
from api.controls.models.rename_session_request import RenameSessionRequest
from api.controls.models.select_effort_request import SelectEffortRequest
from api.controls.models.select_model_request import SelectModelRequest
from api.controls.models.send_text_request import SendTextRequest
from api.controls.models.launch_session_request import LaunchSessionRequest
from api.common.models.fields import SessionIdPath
from app.providers import Controls, Launcher
from api.controls import mapper
from api.controls.models.control_outcome_response import ControlOutcomeResponse
from api.controls.models.launch_response import LaunchResponse
from api.responses import with_body
from domain.ids import HarnessName, SessionId
from harness.models import ControlAcknowledgement, LaunchStatus
from harness.services.controls import HarnessControlService

router = APIRouter()

# The route names an api MODEL as its return type and returns one, so the
# published schema and the bytes are the same statement. The harness layer's own
# result dataclasses never reach the HTTP boundary: `control_outcome` maps one to the
# api model that mirrors it, and nothing here builds a dict.
LAUNCH_STATUS = {LaunchStatus.STARTED: 202, LaunchStatus.REJECTED: 409}
CONTROL_STATUS = {
    ControlAcknowledgement.ACKNOWLEDGED: 200,
    ControlAcknowledgement.INDETERMINATE: 202,
    ControlAcknowledgement.REJECTED: 409,
}

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
    launch_session_request: LaunchSessionRequest, launcher: Launcher, response: Response
) -> LaunchResponse:
    result = launcher.launch(
        HarnessName(launch_session_request.harness), launch_session_request.request()
    )
    response.status_code = LAUNCH_STATUS[result.status]
    return mapper.launch(result)


def _execute(
    harness_control_service: HarnessControlService,
    session_id: str,
    control_request_body: ControlRequestBody,
    response: Response,
) -> ControlOutcomeResponse:
    """One gesture: the request model builds its harness dataclass, the
    audited control service executes it, the outcome's status picks the code.

    The status is set ON the injected response rather than by wrapping the body
    in one, which is what lets the handler return the outcome ITSELF.
    """
    outcome = harness_control_service.execute(control_request_body.request(SessionId(session_id)))
    response.status_code = CONTROL_STATUS[outcome.status]
    return mapper.control_outcome(outcome)


@router.post("/api/sessions/{session_id}/controls/send-text",
             responses=CONTROL_RESPONSES)
def send_text(
    session_id: SessionIdPath, send_text_request: SendTextRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, send_text_request, response)


@router.post("/api/sessions/{session_id}/controls/interrupt",
             responses=CONTROL_RESPONSES)
def interrupt(
    session_id: SessionIdPath, interrupt_request: InterruptRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, interrupt_request, response)


@router.post("/api/sessions/{session_id}/controls/background",
             responses=CONTROL_RESPONSES)
def background(
    session_id: SessionIdPath, background_request: BackgroundRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    """Move the command the harness is blocked on into the background.

    A 409 here is the normal answer to asking at the wrong moment: nothing is
    running, or the TUI is not offering the gesture yet. The handler waits for the
    harness's own offer before pressing anything, so an acknowledgement means the
    keystroke reached a program that was ready to receive it.
    """
    return _execute(controls, session_id, background_request, response)


@router.post("/api/sessions/{session_id}/controls/close-session",
             responses=CONTROL_RESPONSES)
def close_session(
    session_id: SessionIdPath, close_session_request: CloseSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, close_session_request, response)


@router.post("/api/sessions/{session_id}/controls/rename-session",
             responses=CONTROL_RESPONSES)
def rename_session(
    session_id: SessionIdPath, rename_session_request: RenameSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, rename_session_request, response)


@router.post("/api/sessions/{session_id}/controls/auto-name-session",
             responses=CONTROL_RESPONSES)
def auto_name_session(
    session_id: SessionIdPath, auto_name_session_request: AutoNameSessionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, auto_name_session_request, response)


@router.post("/api/sessions/{session_id}/controls/open-rewind",
             responses=CONTROL_RESPONSES)
def open_rewind(
    session_id: SessionIdPath, open_rewind_request: OpenRewindRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, open_rewind_request, response)


@router.post("/api/sessions/{session_id}/controls/apply-rewind",
             responses=CONTROL_RESPONSES)
def apply_rewind(
    session_id: SessionIdPath, apply_rewind_request: ApplyRewindRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, apply_rewind_request, response)


@router.post("/api/sessions/{session_id}/controls/compact",
             responses=CONTROL_RESPONSES)
def compact(
    session_id: SessionIdPath, compact_request: CompactRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, compact_request, response)


@router.post("/api/sessions/{session_id}/controls/select-model",
             responses=CONTROL_RESPONSES)
def select_model(
    session_id: SessionIdPath, select_model_request: SelectModelRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, select_model_request, response)


@router.post("/api/sessions/{session_id}/controls/select-effort",
             responses=CONTROL_RESPONSES)
def select_effort(
    session_id: SessionIdPath, select_effort_request: SelectEffortRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, select_effort_request, response)


@router.post("/api/sessions/{session_id}/controls/answer-question",
             responses=CONTROL_RESPONSES)
def answer_question(
    session_id: SessionIdPath, answer_question_request: AnswerQuestionRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, answer_question_request, response)


@router.post("/api/sessions/{session_id}/controls/read-plan-choices",
             responses=CONTROL_RESPONSES)
def read_plan_choices(
    session_id: SessionIdPath, read_plan_choices_request: ReadPlanChoicesRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, read_plan_choices_request, response)


@router.post("/api/sessions/{session_id}/controls/decide-plan",
             responses=CONTROL_RESPONSES)
def decide_plan(
    session_id: SessionIdPath, decide_plan_request: DecidePlanRequest, controls: Controls, response: Response
) -> ControlOutcomeResponse:
    return _execute(controls, session_id, decide_plan_request, response)
