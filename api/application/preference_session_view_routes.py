# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide session view preference routes."""

from http import HTTPStatus

from fastapi import APIRouter, HTTPException

from api.application.models.preferences.dismiss_goal_request import DismissGoalRequest
from api.application.models.preferences.notifications_muted_request import NotificationsMutedRequest
from api.application.models.preferences.tasks_hidden_request import TasksHiddenRequest
from api.application.models.preferences.view_mode_request import ViewModeRequest
from api.common.models.fields import SessionIdPath
from api.common.models.replies.saved_response import SavedResponse
from app.provider_session_application import SessionApplication
from domain.ids import SessionId

router = APIRouter()


@router.post("/api/sessions/{session_id}/application/dismiss-goal")
def dismiss_goal(
    session_id: SessionIdPath,
    dismiss_goal_request: DismissGoalRequest,
    workspace: SessionApplication,
) -> SavedResponse:
    """Hide a completed goal on all clients.

    Returns:
        The saved response.

    Raises:
        HTTPException: If the goal changed or is not complete.

    """
    try:
        workspace.dismiss_goal(SessionId(session_id), dismiss_goal_request.objective)
    except ValueError as error:
        raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error
    return SavedResponse()


@router.post("/api/sessions/{session_id}/application/view-mode")
def set_view_mode(
    session_id: SessionIdPath,
    view_mode_request: ViewModeRequest,
    workspace: SessionApplication,
) -> SavedResponse:
    """Set the session view mode.

    Returns:
        The saved response.

    """
    workspace.set_view_mode(SessionId(session_id), view_mode_request.view_mode)
    return SavedResponse()


@router.post("/api/sessions/{session_id}/application/notifications-muted")
def set_notifications_muted(
    session_id: SessionIdPath,
    notifications_muted_request: NotificationsMutedRequest,
    workspace: SessionApplication,
) -> SavedResponse:
    """Set the session notification state.

    Returns:
        The saved response.

    """
    workspace.set_notifications_muted(SessionId(session_id), muted=notifications_muted_request.muted)
    return SavedResponse()


@router.post("/api/sessions/{session_id}/application/tasks-hidden")
def set_tasks_hidden(
    session_id: SessionIdPath,
    tasks_hidden_request: TasksHiddenRequest,
    workspace: SessionApplication,
) -> SavedResponse:
    """Set the session task list state.

    Returns:
        The saved response.

    Raises:
        HTTPException: If the task state cannot be changed.

    """
    try:
        workspace.set_tasks_hidden(SessionId(session_id), hidden=tasks_hidden_request.hidden)
    except ValueError as error:
        raise HTTPException(HTTPStatus.CONFLICT, str(error)) from error
    return SavedResponse()
