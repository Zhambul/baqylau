# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify the typed SDK client."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest

from api.controls.models.select_effort_request import SelectEffortRequest
from sdk.client import (
    AUTOMATIC_NAME_TIMEOUT_SECONDS,
    SessionRef,
    TerminalResource,
)
from sdk.state import SessionSnapshot
from tests import sdk_test_records, sdk_test_support, sdk_test_transports
from tests.e2e.testkit.references import References
from tests.sdk_test_resources import (
    sessions_resource,
    uploads_resource,
)

if TYPE_CHECKING:

    from api.terminal.models.panes.pane_command_response import PaneCommandResponse


# The receipt carries the newest entry cursor, and the fake session has no entries.
NO_ENTRY_CURSOR = 0


PANE_WIDTH_PERCENT = 35


SESSION_ID_TEXT = "session-one"


SESSION = SessionRef(SESSION_ID_TEXT)


STATE_FIELD = "state"


WORKING_DIRECTORY = "/work"


TEXT_FIELD = "text"


MEDIA_TYPE_FIELD = "media_type"


PLAIN_TEXT_MEDIA_TYPE = "text/plain"


WINDOW_ID_TEXT = "window-one"


def test_late_generic_plan_rejection_does_not() -> None:
    """Verify a late generic plan rejection does not erase feedback."""
    plan = SessionSnapshot(
        sdk_test_support.session_data(3),
        (
            sdk_test_records.plan_entry(
                1,
                "plan_proposed",
                {
                    "plan": {TEXT_FIELD: "Do it", MEDIA_TYPE_FIELD: "text/markdown"},
                },
            ),
            sdk_test_records.plan_entry(
                2,
                "plan_resolved",
                {
                    STATE_FIELD: "changes_requested",
                    "feedback": "start with tests",
                    "edited": False,
                },
            ),
            sdk_test_records.plan_entry(
                3,
                "plan_resolved",
                {
                    STATE_FIELD: "rejected",
                    "feedback": None,
                    "edited": False,
                },
            ),
        ),
    ).plans()[0]

    assert plan.state == "changes_requested"
    assert plan.feedback == "start with tests"


def test_named_references_reject_rebinding() -> None:
    """Verify named references reject rebinding and unknown names."""
    references = References[int]("command")
    references.bind("build", 1)

    with pytest.raises(AssertionError, match="already bound"):
        references.bind("build", 2)
    with pytest.raises(AssertionError, match=r"available names: \['build'\]"):
        references.get("missing")


def test_session_controls_use_one_typed_dispatch() -> None:
    """Verify session controls use one typed dispatch path."""
    transport = sdk_test_transports.ControlTransport()

    receipt = sessions_resource(transport).select_effort(
        SESSION,
        "medium",
    )

    path, document, statuses = transport.posts[0]
    assert isinstance(document, SelectEffortRequest)
    assert (
        path,
        document.effort,
        str(document.request_id).startswith("e2e-select-effort-"),
        statuses,
        receipt.cursor_before,
        receipt.outcome.status,
    ) == (
        "/api/sessions/session-one/controls/select-effort",
        "medium",
        True,
        {HTTPStatus.OK, HTTPStatus.ACCEPTED, HTTPStatus.CONFLICT},
        NO_ENTRY_CURSOR,
        "acknowledged",
    )


def test_auto_name_allows_two_model_provider() -> None:
    """Verify automatic name allows two model provider attempts."""
    transport = sdk_test_transports.ControlTransport()
    sessions = sessions_resource(transport)

    sessions.auto_name(SESSION)

    assert transport.timeouts == [AUTOMATIC_NAME_TIMEOUT_SECONDS]


def test_upload_resource_encodes_bytes() -> None:
    """Verify upload resource encodes bytes and returns a typed attachment."""
    transport = sdk_test_transports.UploadTransport()

    staged = uploads_resource(transport).stage(
        name="context.txt",
        media_type=PLAIN_TEXT_MEDIA_TYPE,
        file_content=b"sample",
    )

    path, document, statuses = transport.posts[0]
    assert path == "/api/application/uploads"
    assert document.model_dump(by_alias=True) == {
        "name": "context.txt",
        "mime": PLAIN_TEXT_MEDIA_TYPE,
        "data": "c2FtcGxl",
        "session_id": None,
    }
    assert statuses == {200}
    assert staged.path == "/test-data/upload-context.txt"


def _all_pane_outcomes_succeeded(outcomes: tuple[PaneCommandResponse, ...]) -> bool:
    return all(outcome.handled and outcome.succeeded for outcome in outcomes)


def _pane_outcomes(terminal: TerminalResource) -> tuple[PaneCommandResponse, ...]:
    return (
        terminal.toggle_panes(window_id=WINDOW_ID_TEXT, workspace=WORKING_DIRECTORY),
        terminal.grow_activity_pane(
            window_id=WINDOW_ID_TEXT,
            workspace=WORKING_DIRECTORY,
            columns=7,
        ),
        terminal.shrink_activity_pane(
            window_id=WINDOW_ID_TEXT,
            workspace=WORKING_DIRECTORY,
            columns=5,
        ),
        terminal.set_activity_pane_width(
            window_id=WINDOW_ID_TEXT,
            workspace=WORKING_DIRECTORY,
            percent=PANE_WIDTH_PERCENT,
        ),
        terminal.reset_activity_pane(window_id=WINDOW_ID_TEXT, workspace=WORKING_DIRECTORY),
    )
