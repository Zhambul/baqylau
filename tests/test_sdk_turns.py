# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify the typed SDK client."""

from __future__ import annotations

from typing import cast

from api.sessiondata.models.entry import EntryResponse, MessageBodyResponse
from sdk.client import (
    SessionRef,
    SessionWatch,
)
from sdk.state import SessionSnapshot
from tests import sdk_test_records
from tests.e2e.testkit import selector_common, selector_turns, turns as turn_checks
from tests.e2e.testkit.references import TurnRef
from tests.sdk_test_support import (
    lead_message_entry,
    message_entry,
    prompt_entry,
    session_data,
    turn_finished_entry,
)
from tests.sdk_test_transports import FixedWatch

SESSION_ID_TEXT = "session-one"


SESSION = SessionRef(SESSION_ID_TEXT)


CURSOR_FIELD = "cursor"
COMMIT_CURSOR_FIELD = "commit_cursor"


STATE_FIELD = "state"


LEAD_ACTOR_ID = "lead-one"


ENTRY_ID_FIELD = "entry_id"


TYPE_FIELD = "type"


MESSAGE_ENTRY_TYPE = "message"


ACTOR_ID_FIELD = "actor_id"


PARENT_ACTOR_ID_FIELD = "parent_actor_id"


TURN_ID_FIELD = "turn_id"


FIRST_TURN_ID = "turn-one"


OCCURRED_AT_FIELD = "occurred_at"


SUMMARY_FIELD = "summary"


BODY_FIELD = "body"


MESSAGE_ID_FIELD = "message_id"


ROLE_FIELD = "role"


PHASE_FIELD = "phase"


CONTENT_FIELD = "content"


TEXT_FIELD = "text"


MEDIA_TYPE_FIELD = "media_type"


PLAIN_TEXT_MEDIA_TYPE = "text/plain"


RECIPIENT_ACTOR_ID_FIELD = "recipient_actor_id"


REPLY_TO_FIELD = "reply_to"


PROMPT_PHASE = "prompt"


SUCCEEDED_FIELD = "succeeded"


CHILD_ACTOR_ID = "child-one"


CHILD_TURN_ID = "child-turn"


ASSIGNMENT_ID_FIELD = "assignment_id"


ASSIGNMENT_ID_TEXT = "assignment-one"


ASSIGNED_ACTOR_NAME = "ticker"


ASSIGNMENT_PROMPT = "run a command"


CHILD_MESSAGE_ID = "child-message"


def test_turn_uses_its_prompt_message_when() -> None:
    """Verify a turn uses its prompt message when the harness has no turn identifier."""
    delivered = '[Image #1]Image attachment "visible-marker.png":\nInspect the image. Reply only with its code.'
    reference = TurnRef(
        SESSION,
        "Inspect the image. Reply only with its code.",
        0,
        1,
        actor_id=LEAD_ACTOR_ID,
        native_attachment_names=("visible-marker.png",),
    )
    snapshot = SessionSnapshot(
        session_data(1),
        (prompt_entry(1, delivered, turn_id=None),),
    )

    found = selector_turns.turn(cast("SessionWatch", FixedWatch(snapshot)), reference, timeout=1.0)

    assert found.turn_id is None
    assert found.prompt_cursor == 1
    assert found.prompt_message_id == "prompt-message-1"


def test_lead_turn_boundary_ignores_child_prompt() -> None:
    """Verify a lead turn boundary ignores a child prompt."""
    snapshot = SessionSnapshot(
        session_data(4),
        (
            prompt_entry(1, "lead prompt"),
            prompt_entry(2, "child prompt", actor_id=CHILD_ACTOR_ID, turn_id=CHILD_TURN_ID),
            message_entry(3),
            prompt_entry(4, "next lead prompt", turn_id="turn-two"),
        ),
    )
    reference = TurnRef(
        SESSION,
        "lead prompt",
        0,
        1,
        actor_id=LEAD_ACTOR_ID,
        turn_id=FIRST_TURN_ID,
        prompt_cursor=1,
    )

    assert selector_common.cursor_is_in_turn(snapshot, reference, 3)
    assert not selector_common.cursor_is_in_turn(snapshot, reference, 4)


def test_later_autonomous_completion_does_not_add() -> None:
    """Verify a later autonomous completion does not add an answer to the named turn."""
    reference = TurnRef(
        SESSION,
        "delegate",
        0,
        1,
        actor_id=LEAD_ACTOR_ID,
        turn_id=FIRST_TURN_ID,
        prompt_cursor=1,
    )
    snapshot = SessionSnapshot(
        session_data(5),
        (
            prompt_entry(1, "delegate"),
            turn_finished_entry(2, FIRST_TURN_ID),
            lead_message_entry(3, "launched", turn_id=None),
            turn_finished_entry(4, None),
            lead_message_entry(5, "notification", turn_id=None),
        ),
    )

    ending_messages = [
        entry.body for entry in turn_checks.enders(snapshot, reference) if isinstance(entry.body, MessageBodyResponse)
    ]
    assert [message.content.text for message in ending_messages] == ["launched"]


def test_assignment_uses_actor_that_finishes_it() -> None:
    """Verify an assignment uses the actor that finishes it."""
    started = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "assignment-started",
            TYPE_FIELD: "assignment_started",
            CURSOR_FIELD: 1,
            COMMIT_CURSOR_FIELD: 1,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: 1.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                ASSIGNMENT_ID_FIELD: ASSIGNMENT_ID_TEXT,
                "assigned_actor_name": ASSIGNED_ACTOR_NAME,
                PROMPT_PHASE: {TEXT_FIELD: ASSIGNMENT_PROMPT, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
            },
        },
    )
    finished = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "assignment-finished",
            TYPE_FIELD: "assignment_finished",
            CURSOR_FIELD: 2,
            COMMIT_CURSOR_FIELD: 2,
            ACTOR_ID_FIELD: CHILD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            TURN_ID_FIELD: CHILD_TURN_ID,
            OCCURRED_AT_FIELD: 2.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                ASSIGNMENT_ID_FIELD: ASSIGNMENT_ID_TEXT,
                STATE_FIELD: SUCCEEDED_FIELD,
                "result": {TEXT_FIELD: "gathered", MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
            },
        },
    )

    assignment = SessionSnapshot(session_data(2), (started, finished)).assignments()[0]

    assert (
        assignment.actor_id,
        assignment.owner_actor_id,
        assignment.turn_id,
        assignment.assigned_actor_name,
        assignment.requested_prompt,
        assignment.state,
    ) == (
        CHILD_ACTOR_ID,
        LEAD_ACTOR_ID,
        FIRST_TURN_ID,
        ASSIGNED_ACTOR_NAME,
        ASSIGNMENT_PROMPT,
        SUCCEEDED_FIELD,
    )


def test_team_assignment_uses_its_last_message() -> None:
    """Verify a team assignment uses its last message when idle has no result."""
    started = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "assignment-started",
            TYPE_FIELD: "assignment_started",
            CURSOR_FIELD: 1,
            COMMIT_CURSOR_FIELD: 1,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: 1.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                ASSIGNMENT_ID_FIELD: ASSIGNMENT_ID_TEXT,
                "assigned_actor_name": ASSIGNED_ACTOR_NAME,
                PROMPT_PHASE: {TEXT_FIELD: ASSIGNMENT_PROMPT, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
            },
        },
    )
    final_message = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: CHILD_MESSAGE_ID,
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: 2,
            COMMIT_CURSOR_FIELD: 2,
            ACTOR_ID_FIELD: CHILD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            TURN_ID_FIELD: None,
            OCCURRED_AT_FIELD: 2.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: CHILD_MESSAGE_ID,
                ROLE_FIELD: "assistant",
                PHASE_FIELD: "intermediate",
                CONTENT_FIELD: {TEXT_FIELD: "TEAM_DONE", MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
                REPLY_TO_FIELD: None,
            },
        },
    )
    finished = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "assignment-finished",
            TYPE_FIELD: "assignment_finished",
            CURSOR_FIELD: 3,
            COMMIT_CURSOR_FIELD: 3,
            ACTOR_ID_FIELD: CHILD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            TURN_ID_FIELD: None,
            OCCURRED_AT_FIELD: 3.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                ASSIGNMENT_ID_FIELD: ASSIGNMENT_ID_TEXT,
                STATE_FIELD: SUCCEEDED_FIELD,
                "result": None,
            },
        },
    )

    assignment = SessionSnapshot(
        session_data(3),
        (started, final_message, finished),
    ).assignments()[0]

    assert assignment.actor_id == CHILD_ACTOR_ID
    assert assignment.result == "TEAM_DONE"


def test_claude_assignment_uses_child_prompt() -> None:
    """Verify a claude assignment uses the child prompt before the child finishes."""
    started = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "assignment-started",
            TYPE_FIELD: "assignment_started",
            CURSOR_FIELD: 1,
            COMMIT_CURSOR_FIELD: 1,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: 1.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                ASSIGNMENT_ID_FIELD: ASSIGNMENT_ID_TEXT,
                "assigned_actor_name": ASSIGNED_ACTOR_NAME,
                PROMPT_PHASE: {TEXT_FIELD: ASSIGNMENT_PROMPT, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
            },
        },
    )
    child_prompt = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "child-prompt",
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: 2,
            COMMIT_CURSOR_FIELD: 2,
            ACTOR_ID_FIELD: CHILD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            TURN_ID_FIELD: CHILD_TURN_ID,
            OCCURRED_AT_FIELD: 2.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: CHILD_MESSAGE_ID,
                ROLE_FIELD: "parent",
                PHASE_FIELD: PROMPT_PHASE,
                CONTENT_FIELD: {TEXT_FIELD: ASSIGNMENT_PROMPT, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: None,
                REPLY_TO_FIELD: None,
            },
        },
    )

    assignment = SessionSnapshot(
        session_data(2),
        (started, child_prompt),
    ).assignments()[0]

    assert assignment.owner_actor_id == LEAD_ACTOR_ID
    assert assignment.actor_id == CHILD_ACTOR_ID
    assert assignment.state is None


def test_two_equal_pending_assignments_do_not() -> None:
    """Verify two equal pending assignments do not guess a child actor."""
    child_prompt = EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: "child-prompt",
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: 3,
            COMMIT_CURSOR_FIELD: 3,
            ACTOR_ID_FIELD: CHILD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            TURN_ID_FIELD: CHILD_TURN_ID,
            OCCURRED_AT_FIELD: 3.0,
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: CHILD_MESSAGE_ID,
                ROLE_FIELD: "parent",
                PHASE_FIELD: PROMPT_PHASE,
                CONTENT_FIELD: {TEXT_FIELD: "same work", MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: None,
                REPLY_TO_FIELD: None,
            },
        },
    )

    assignments = SessionSnapshot(
        session_data(3),
        (
            sdk_test_records.assignment_started_entry(ASSIGNMENT_ID_TEXT, 1),
            sdk_test_records.assignment_started_entry("assignment-two", 2),
            child_prompt,
        ),
    ).assignments()

    assert [assignment.actor_id for assignment in assignments] == [None, None]
