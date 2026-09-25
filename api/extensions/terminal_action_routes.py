# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one action of a presented terminal view as the registered command it names."""

from http import HTTPStatus

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.terminal import models as terminal_view_models
from fastapi import APIRouter, Depends, HTTPException

from api.extensions import admission, command_models, command_service, job_models, job_service, terminal_models
from api.extensions.command_routes import CommandDependencies
from api.extensions.scope_documents import request_scope
from app.provider_extension_executor import JobExecution
from app.provider_extension_terminal import Snapshots
from extensions import terminal_actions, terminal_presentation, terminal_views
from extensions.job_requests import JobKey
from repository.contract.extension_jobs import ExtensionJob

router = APIRouter()


@router.post(
    "/api/extension-terminal/actions", dependencies=[Depends(admission.require_extension_json)],
    status_code=HTTPStatus.ACCEPTED,
)
def run_terminal_action(
    extension_action_request: terminal_models.ExtensionActionRequest, services: CommandDependencies,
    snapshots: Snapshots, executor: JobExecution,
) -> job_models.ExtensionJobResponse:
    """Present the view again at the same focus and size, then accept the command of the chosen action.

    The client sends only the action ID; the command, its arguments, and its
    expected state revision come from the view that the host presents.

    Returns:
        The accepted job before its final state.

    """
    request = extension_action_request
    selection = _view_selection(request)
    with services.registry.read_snapshot() as read:
        dispatch = command_service.CommandDispatch(read.snapshot.packages, services.jobs, services.policy)
        accepted = _accept(dispatch, snapshots, selection, request)
    if accepted.state == "accepted":
        executor.submit(JobKey(request.extension_id, selection.scope, accepted.job_id))
    return job_service.job_response(accepted)


def _accept(
    dispatch: command_service.CommandDispatch, snapshots: Snapshots,
    selection: terminal_presentation.ViewSelection, request: terminal_models.ExtensionActionRequest,
) -> ExtensionJob:
    try:
        action = terminal_actions.presented_action(dispatch.packages, snapshots, selection, request.action_id)
    except terminal_views.TerminalViewNotFoundError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    command = command_models.ExtensionCommandRequest(
        scope=request.scope, request_key=request.request_key, arguments=action.arguments.json_text,
        expected_state_revision=action.expected_state_revision,
    )
    try:
        return command_service.accept_command(
            dispatch, request.extension_id, action.command_id, selection.scope, command,
        )
    except command_service.CommandNotFoundError as error:
        raise HTTPException(HTTPStatus.NOT_FOUND, str(error)) from error
    except ExtensionContractError as error:
        raise HTTPException(HTTPStatus.BAD_REQUEST, str(error)) from error


def _view_selection(request: terminal_models.ExtensionActionRequest) -> terminal_presentation.ViewSelection:
    focus = None
    if request.block_id is not None:
        focus = terminal_view_models.TerminalSelection(block_id=request.block_id, item_id=request.item_id)
    viewport = terminal_view_models.TerminalViewport(columns=request.columns, rows=request.rows)
    scope = request_scope(request.scope)
    return terminal_presentation.ViewSelection(request.extension_id, request.view_id, scope, viewport, focus)
