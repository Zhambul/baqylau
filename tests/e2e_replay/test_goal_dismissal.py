# Copyright (c) 2026 Zhambyl Yermagambet
"""Check durable goal dismissal through the backend API."""

from dataclasses import replace
from http import HTTPStatus

import pytest

from domain.ids import SessionId
from domain.session_state import SessionGoal
from domain.work_state import GoalState
from repository.contract.session_data import SessionDataChanges
from tests import canonical_sessiondata_api_values as fixture, http_test_assets, http_test_controls
from tests.provider_graph import ProviderGraph

SESSION = SessionId("session-one")
PATH = "/api/sessions/session-one/application/dismiss-goal"
APPLICATION_PATH = "/api/sessions/session-one/application"
OBJECTIVE = "Check the job"
OBJECTIVE_FIELD = "objective"
RESUME_CURSOR = 2
COMPLETED_CURSOR = 3


def _goal(application: ProviderGraph, state: GoalState, cursor: int, objective: str = OBJECTIVE) -> None:
    application.session_data.apply(
        SESSION,
        SessionDataChanges(session=replace(fixture.FACTS, goal=SessionGoal(objective, state, None))),
        cursor,
    )


def test_dismissal_survives_a_new_server() -> None:
    """Read the dismissal after the server and provider graph are replaced."""
    application = ProviderGraph()
    _goal(application, GoalState.COMPLETED, 1)
    with (
        http_test_assets.running_server(application) as server,
        application.application_update_state.changes.subscribe_thread() as changed,
    ):
        saved = http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE})
        assert saved.status == HTTPStatus.OK
        assert changed.is_set()
    with http_test_assets.running_server(ProviderGraph()) as server:
        response = http_test_controls.get(server, APPLICATION_PATH)
        assert '"goal_hidden":true' in response.body.raw.decode()


@pytest.mark.parametrize("state", [GoalState.ACTIVE, GoalState.CLEARED])
def test_resumed_or_cleared_goal_returns(state: GoalState) -> None:
    """A goal that becomes complete again must need a new dismissal."""
    application = ProviderGraph()
    _goal(application, GoalState.COMPLETED, 1)
    with http_test_assets.running_server(application) as server:
        assert http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE}).status == HTTPStatus.OK
        _goal(application, state, RESUME_CURSOR)
        _goal(application, GoalState.COMPLETED, COMPLETED_CURSOR)
        response = http_test_controls.get(server, APPLICATION_PATH)
        assert '"goal_hidden":false' in response.body.raw.decode()


def test_changed_goal_and_stale_request() -> None:
    """Do not let a stale client hide a different goal."""
    application = ProviderGraph()
    _goal(application, GoalState.COMPLETED, 1)
    with http_test_assets.running_server(application) as server:
        assert http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE}).status == HTTPStatus.OK
        _goal(application, GoalState.COMPLETED, RESUME_CURSOR, "Next job")
        assert http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE}).status == HTTPStatus.CONFLICT
        response = http_test_controls.get(server, APPLICATION_PATH)
        assert '"goal_hidden":false' in response.body.raw.decode()


def test_incomplete_goal_cannot_be_dismissed() -> None:
    """Reject dismissal while the goal is active."""
    application = ProviderGraph()
    _goal(application, GoalState.ACTIVE, 1)
    with http_test_assets.running_server(application) as server:
        assert http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE}).status == HTTPStatus.CONFLICT


def test_old_rebuild_steps_keep_dismissal() -> None:
    """Only a later goal transition can clear a saved dismissal."""
    application = ProviderGraph()
    _goal(application, GoalState.COMPLETED, COMPLETED_CURSOR)
    with http_test_assets.running_server(application) as server:
        assert http_test_controls.post(server, PATH, {OBJECTIVE_FIELD: OBJECTIVE}).status == HTTPStatus.OK
        _goal(application, GoalState.ACTIVE, 1)
        _goal(application, GoalState.COMPLETED, COMPLETED_CURSOR)
        _goal(application, GoalState.COMPLETED, COMPLETED_CURSOR + 1)
        response = http_test_controls.get(server, APPLICATION_PATH)
        assert '"goal_hidden":true' in response.body.raw.decode()
