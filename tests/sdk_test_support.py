# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify the typed SDK client."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.sessiondata.models.entry import EntryResponse
from api.sessiondata.models.session_data import SessionDataResponse

if TYPE_CHECKING:
    from pydantic import TypeAdapter

INITIAL_CURSOR = 1_001


SESSION_ID_TEXT = "session-one"


CURSOR_FIELD = "cursor"
COMMIT_CURSOR_FIELD = "commit_cursor"


STATE_FIELD = "state"


WORKING_DIRECTORY_FIELD = "working_directory"


WORKING_DIRECTORY = "/work"


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


def session_data(
    cursor: int = INITIAL_CURSOR,
    *,
    session_id: str = SESSION_ID_TEXT,
    continued_from: str | None = None,
    live: bool = True,
) -> SessionDataResponse:
    """Create a running session response for an SDK test.

    Returns:
        The response with the requested cursor, session, continuation, and live state.

    """
    return SessionDataResponse.model_validate(
        {
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            "session": {
                "session_id": session_id,
                "harness": "codex",
                "title": None,
                STATE_FIELD: "running",
                WORKING_DIRECTORY_FIELD: WORKING_DIRECTORY,
                "started_at": 1.0,
                "finished_at": None,
                "account": None,
                "lead_actor_id": LEAD_ACTOR_ID,
                "goal": None,
                "tasks": [],
                "continued_from": continued_from,
            },
            "actors": [],
            "live": live,
            "project_directory": WORKING_DIRECTORY,
            "repository": None,
        },
    )


def message_entry(cursor: int) -> EntryResponse:
    """Create a final assistant message from a cursor.

    Returns:
        The message entry with the cursor as its text.

    """
    return EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: f"entry-{cursor}",
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: float(cursor),
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: f"message-{cursor}",
                ROLE_FIELD: "assistant",
                PHASE_FIELD: "end_turn",
                CONTENT_FIELD: {TEXT_FIELD: str(cursor), MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: None,
                REPLY_TO_FIELD: None,
            },
        },
    )


def lead_message_entry(
    cursor: int,
    text: str,
    *,
    turn_id: str | None,
) -> EntryResponse:
    """Create a final message from the lead actor.

    Returns:
        The entry with the supplied text and turn identifier.

    """
    return EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: f"lead-message-{cursor}",
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: turn_id,
            OCCURRED_AT_FIELD: float(cursor),
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: f"lead-message-{cursor}",
                ROLE_FIELD: "assistant",
                PHASE_FIELD: "end_turn",
                CONTENT_FIELD: {TEXT_FIELD: text, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: None,
                REPLY_TO_FIELD: None,
            },
        },
    )


def turn_finished_entry(cursor: int, turn_id: str | None) -> EntryResponse:
    """Create a turn completion entry for the lead actor.

    Returns:
        The finished entry at the supplied cursor.

    """
    return EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: f"turn-finished-{cursor}",
            TYPE_FIELD: "turn_finished",
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: turn_id,
            OCCURRED_AT_FIELD: float(cursor),
            SUMMARY_FIELD: None,
            BODY_FIELD: {STATE_FIELD: "finished"},
        },
    )


def prompt_entry(
    cursor: int,
    text: str,
    *,
    actor_id: str = LEAD_ACTOR_ID,
    turn_id: str | None = FIRST_TURN_ID,
) -> EntryResponse:
    """Create a user prompt for the selected actor and turn.

    Returns:
        The prompt entry, with the lead as parent for a child actor.

    """
    return EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: f"prompt-{cursor}",
            TYPE_FIELD: MESSAGE_ENTRY_TYPE,
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            ACTOR_ID_FIELD: actor_id,
            PARENT_ACTOR_ID_FIELD: None if actor_id == LEAD_ACTOR_ID else LEAD_ACTOR_ID,
            TURN_ID_FIELD: turn_id,
            OCCURRED_AT_FIELD: float(cursor),
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                MESSAGE_ID_FIELD: f"prompt-message-{cursor}",
                ROLE_FIELD: "user",
                PHASE_FIELD: PROMPT_PHASE,
                CONTENT_FIELD: {TEXT_FIELD: text, MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                RECIPIENT_ACTOR_ID_FIELD: None,
                REPLY_TO_FIELD: None,
            },
        },
    )


def shell_entry(cursor: int, shell_id: str) -> EntryResponse:
    """Create a foreground shell start entry.

    Returns:
        The entry with the supplied cursor and shell identifier.

    """
    return EntryResponse.model_validate(
        {
            ENTRY_ID_FIELD: f"entry-{cursor}",
            TYPE_FIELD: "shell_started",
            CURSOR_FIELD: cursor,
            COMMIT_CURSOR_FIELD: cursor,
            ACTOR_ID_FIELD: LEAD_ACTOR_ID,
            PARENT_ACTOR_ID_FIELD: None,
            TURN_ID_FIELD: FIRST_TURN_ID,
            OCCURRED_AT_FIELD: float(cursor),
            SUMMARY_FIELD: None,
            BODY_FIELD: {
                "shell_id": shell_id,
                "command": {TEXT_FIELD: "echo duplicate", MEDIA_TYPE_FIELD: PLAIN_TEXT_MEDIA_TYPE},
                "execution": "foreground",
            },
        },
    )


def _transport_response[Response](adapter: TypeAdapter[Response], fixture_document: object) -> Response:
    """Return one fixture through a generic transport method.

    Returns:
        One fixture through a generic transport method.

    """
    return adapter.validate_python(fixture_document)
