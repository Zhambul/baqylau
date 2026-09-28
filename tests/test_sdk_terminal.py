# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify the typed SDK client."""

from __future__ import annotations

from collections.abc import Set as AbstractSet

from pydantic import BaseModel

from api.sessiondata.models.entry import EntryResponse
from sdk.state import SessionSnapshot
from tests.sdk_test_resources import terminal_resource
from tests.sdk_test_support import session_data
from tests.test_sdk_controls import (
    _all_pane_outcomes_succeeded,
    _pane_outcomes,
)
from tests.test_sdk_resources import (
    PaneTransport,
)

PANE_ACCEPTED_STATUSES = frozenset((200, 409))


CURSOR_FIELD = "cursor"
COMMIT_CURSOR_FIELD = "commit_cursor"


WORKING_DIRECTORY_FIELD = "working_directory"


WORKING_DIRECTORY = "/work"


LEAD_ACTOR_ID = "lead-one"


ENTRY_ID_FIELD = "entry_id"


TYPE_FIELD = "type"


ACTOR_ID_FIELD = "actor_id"


PARENT_ACTOR_ID_FIELD = "parent_actor_id"


TURN_ID_FIELD = "turn_id"


FIRST_TURN_ID = "turn-one"


OCCURRED_AT_FIELD = "occurred_at"


SUMMARY_FIELD = "summary"


BODY_FIELD = "body"


WINDOW_ID_FIELD = "window_id"


WINDOW_ID_TEXT = "window-one"


type TransportPost = tuple[str, BaseModel, set[int]]


type PanePostDocument = tuple[str, dict[str, object], AbstractSet[int]]


def _pane_post_documents(posts: list[TransportPost]) -> list[PanePostDocument]:
    """Return the comparable document form of pane transport posts.

    Returns:
        The comparable document form of pane transport posts.

    """
    return [(path, document.model_dump(), statuses) for path, document, statuses in posts]


def test_terminal_resource_uses_named_pane() -> None:
    """Verify terminal resource uses named pane gestures."""
    transport = PaneTransport()

    outcomes = _pane_outcomes(
        terminal_resource(transport),
    )

    assert _all_pane_outcomes_succeeded(outcomes)
    posted_documents = _pane_post_documents(transport.posts)
    assert posted_documents == [
        (
            "/api/terminal/panes/toggle",
            {WINDOW_ID_FIELD: WINDOW_ID_TEXT, WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY},
            PANE_ACCEPTED_STATUSES,
        ),
        (
            "/api/terminal/panes/grow",
            {WINDOW_ID_FIELD: WINDOW_ID_TEXT, WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY, "columns": 7},
            PANE_ACCEPTED_STATUSES,
        ),
        (
            "/api/terminal/panes/shrink",
            {WINDOW_ID_FIELD: WINDOW_ID_TEXT, WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY, "columns": 5},
            PANE_ACCEPTED_STATUSES,
        ),
        (
            "/api/terminal/panes/set-percent",
            {WINDOW_ID_FIELD: WINDOW_ID_TEXT, WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY, "percent": 35},
            PANE_ACCEPTED_STATUSES,
        ),
        (
            "/api/terminal/panes/reset",
            {WINDOW_ID_FIELD: WINDOW_ID_TEXT, WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY},
            PANE_ACCEPTED_STATUSES,
        ),
    ]


def test_question_state_folds_asked_and_answered() -> None:
    """Verify question state folds asked and answered entries."""
    asked = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "question-asked",
            TYPE_FIELD: "question_asked",
            CURSOR_FIELD: 1,
            COMMIT_CURSOR_FIELD: 1,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: 1.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                "attention_id": "attention-one",
                "questions": [
                    {
                        "question_id": "question-one",
                        "title": "Colour",
                        "question": "Which colour?",
                        "multiple": False,
                        "choices": [
                            {"label": "Blue", "description": "Use blue"},
                            {"label": "Green", "description": "Use green"},
                        ],
                    },
                ],
            },
        },
    )
    answered = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "question-answered",
            TYPE_FIELD: "question_answered",
            CURSOR_FIELD: 2,
            COMMIT_CURSOR_FIELD: 2,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: 2.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                "attention_id": "attention-one",
                "answers": [{"question_id": "question-one", "labels": ["Blue"]}],
                "feedback": None,
            },
        },
    )

    questions = SessionSnapshot(session_data(2), (asked, answered)).questions()

    assert len(questions) == 1
    assert questions[0].pending is False
    assert questions[0].questions[0].question == "Which colour?"
    assert questions[0].answers is not None
    assert questions[0].answers[0].labels == ("Blue",)
