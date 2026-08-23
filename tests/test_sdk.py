"""Hermetic checks for the typed application client."""

from __future__ import annotations

from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from api.sessiondata.models.entry import EntryPageResponse, EntryResponse
from api.controls.models.control_outcome_response import ControlResultResponse
from api.sessiondata.models.session_data import SessionDataResponse
from sdk.client import SessionRef, SessionsResource, SessionWatch, UploadsResource
from sdk.state import SessionSnapshot
from sdk.transport import ApiFailure, HttpTransport
from tests.e2e.testkit import selectors
from tests.e2e.testkit.references import References, TurnRef


def session_data(cursor: int = 1001) -> SessionDataResponse:
    return SessionDataResponse.model_validate({
        "cursor": cursor,
        "session": {
            "session_id": "session-one",
            "harness": "codex",
            "title": None,
            "state": "running",
            "working_directory": "/work",
            "started_at": 1.0,
            "finished_at": None,
            "account": None,
            "lead_actor_id": "lead-one",
            "goal": None,
            "tasks": [],
        },
        "actors": [],
        "live": True,
        "repository": None,
    })


def message_entry(cursor: int) -> EntryResponse:
    return EntryResponse.model_validate({
        "entry_id": f"entry-{cursor}",
        "type": "message",
        "cursor": cursor,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": "turn-one",
        "occurred_at": float(cursor),
        "summary": None,
        "body": {
            "message_id": f"message-{cursor}",
            "role": "assistant",
            "phase": "end_turn",
            "content": {"text": str(cursor), "media_type": "text/plain"},
            "recipient_actor_id": None,
            "reply_to": None,
        },
    })


def prompt_entry(
    cursor: int,
    text: str,
    *,
    actor_id: str = "lead-one",
    turn_id: str = "turn-one",
) -> EntryResponse:
    return EntryResponse.model_validate({
        "entry_id": f"prompt-{cursor}",
        "type": "message",
        "cursor": cursor,
        "actor_id": actor_id,
        "parent_actor_id": None if actor_id == "lead-one" else "lead-one",
        "turn_id": turn_id,
        "occurred_at": float(cursor),
        "summary": None,
        "body": {
            "message_id": f"prompt-message-{cursor}",
            "role": "user",
            "phase": "prompt",
            "content": {"text": text, "media_type": "text/plain"},
            "recipient_actor_id": None,
            "reply_to": None,
        },
    })


def shell_entry(cursor: int, shell_id: str) -> EntryResponse:
    return EntryResponse.model_validate({
        "entry_id": f"entry-{cursor}",
        "type": "shell_started",
        "cursor": cursor,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": "turn-one",
        "occurred_at": float(cursor),
        "summary": None,
        "body": {
            "shell_id": shell_id,
            "command": {"text": "echo duplicate", "media_type": "text/plain"},
            "execution": "foreground",
        },
    })


class PagedTransport:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path, _adapter):
        self.paths.append(path)
        if path == "/sessionData/session-one":
            return session_data()
        query = parse_qs(urlsplit(path).query)
        assert query["at"] == ["1001"]
        if "before" not in query:
            return EntryPageResponse(
                items=tuple(message_entry(cursor) for cursor in range(2, 1002)),
                oldest_cursor=2,
                has_more=True,
            )
        assert query["before"] == ["2"]
        return EntryPageResponse(items=(message_entry(1),), oldest_cursor=1, has_more=False)


class FixedWatch:
    def __init__(self, snapshot: SessionSnapshot) -> None:
        self.snapshot = snapshot

    def wait(self, _description, condition, *, timeout):
        del timeout
        return condition(self.snapshot)


class StalledTransport:
    def get(self, path, _adapter):
        if path == "/sessionData/session-one":
            return session_data()
        return EntryPageResponse(items=(), oldest_cursor=0, has_more=True)


class ControlTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, set[int]]] = []

    def get(self, path, _adapter):
        if path == "/sessionData/session-one":
            return session_data()
        return EntryPageResponse(items=(), oldest_cursor=0, has_more=False)

    def post(self, path, document, _adapter, accepted_statuses):
        self.posts.append((path, document, accepted_statuses))
        return 200, ControlResultResponse(
            request_id=document["request_id"],
            status="acknowledged",
            reason=None,
        )


class UploadTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, set[int]]] = []

    def post(self, path, document, _adapter, accepted_statuses):
        self.posts.append((path, document, accepted_statuses))
        return 200, _adapter.validate_python({
            "ok": True,
            "path": "/tmp/upload-context.txt",
            "name": "context.txt",
            "mime": "text/plain",
            "is_image": False,
        })


def test_a_session_snapshot_reads_all_pages_at_one_cursor():
    transport = PagedTransport()
    sessions = SessionsResource(cast(HttpTransport, transport))

    snapshot = sessions.snapshot(SessionRef("session-one"))

    assert [entry.cursor for entry in snapshot.entries] == list(range(1, 1002))
    assert len(transport.paths) == 3


def test_a_session_snapshot_rejects_a_page_that_cannot_make_progress():
    sessions = SessionsResource(cast(HttpTransport, StalledTransport()))

    with pytest.raises(ApiFailure, match="returned no entries"):
        sessions.snapshot(SessionRef("session-one"))


def test_a_selector_rejects_two_matching_commands():
    snapshot = SessionSnapshot(
        data=session_data(3),
        entries=(shell_entry(1, "shell-one"), shell_entry(2, "shell-two")),
    )

    with pytest.raises(AssertionError, match="matched 2 objects"):
        selectors.shell(
            cast(SessionWatch, FixedWatch(snapshot)),
            command_contains="echo duplicate",
            timeout=1.0,
        )


def test_a_launch_turn_uses_the_prompt_that_the_harness_delivered():
    delivered = "/tmp/context.txt\nRead the attachment."
    snapshot = SessionSnapshot(session_data(1), (prompt_entry(1, delivered),))

    found = selectors.launched_turn(cast(SessionWatch, FixedWatch(snapshot)), timeout=1.0)

    assert found.prompt == delivered
    assert found.turn_id == "turn-one"
    assert found.prompt_cursor == 1


def test_a_lead_turn_boundary_ignores_a_child_prompt():
    snapshot = SessionSnapshot(
        session_data(4),
        (
            prompt_entry(1, "lead prompt"),
            prompt_entry(2, "child prompt", actor_id="child-one", turn_id="child-turn"),
            message_entry(3),
            prompt_entry(4, "next lead prompt", turn_id="turn-two"),
        ),
    )
    reference = TurnRef(
        SessionRef("session-one"),
        "lead prompt",
        0,
        1,
        actor_id="lead-one",
        turn_id="turn-one",
        prompt_cursor=1,
    )

    assert selectors.cursor_is_in_turn(snapshot, reference, 3)
    assert not selectors.cursor_is_in_turn(snapshot, reference, 4)


def test_an_assignment_uses_the_actor_that_finishes_it():
    started = EntryResponse.model_validate({
        "entry_id": "assignment-started",
        "type": "assignment_started",
        "cursor": 1,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": "turn-one",
        "occurred_at": 1.0,
        "summary": None,
        "body": {
            "assignment_id": "assignment-one",
            "assigned_actor_name": "ticker",
            "prompt": {"text": "run a command", "media_type": "text/plain"},
        },
    })
    finished = EntryResponse.model_validate({
        "entry_id": "assignment-finished",
        "type": "assignment_finished",
        "cursor": 2,
        "actor_id": "child-one",
        "parent_actor_id": "lead-one",
        "turn_id": "child-turn",
        "occurred_at": 2.0,
        "summary": None,
        "body": {
            "assignment_id": "assignment-one",
            "state": "succeeded",
            "result": {"text": "gathered", "media_type": "text/plain"},
        },
    })

    assignment = SessionSnapshot(session_data(2), (started, finished)).assignments()[0]

    assert assignment.actor_id == "child-one"
    assert assignment.turn_id == "turn-one"
    assert assignment.assigned_actor_name == "ticker"
    assert assignment.state == "succeeded"


def test_named_references_reject_rebinding_and_unknown_names():
    references = References[int]("command")
    references.bind("build", 1)

    with pytest.raises(AssertionError, match="already bound"):
        references.bind("build", 2)
    with pytest.raises(AssertionError, match=r"available names: \['build'\]"):
        references.get("missing")


def test_session_controls_use_one_typed_dispatch_path():
    transport = ControlTransport()
    sessions = SessionsResource(cast(HttpTransport, transport))

    receipt = sessions.select_effort(SessionRef("session-one"), "medium")

    path, document, statuses = transport.posts[0]
    assert path == "/api/sessions/session-one/controls/select-effort"
    assert document["effort"] == "medium"
    assert str(document["request_id"]).startswith("e2e-select-effort-")
    assert statuses == {200, 202, 409}
    assert receipt.cursor_before == 1001
    assert receipt.outcome.status == "acknowledged"


def test_upload_resource_encodes_bytes_and_returns_a_typed_attachment():
    transport = UploadTransport()
    uploads = UploadsResource(cast(HttpTransport, transport))

    staged = uploads.stage(
        name="context.txt",
        media_type="text/plain",
        data=b"sample",
    )

    path, document, statuses = transport.posts[0]
    assert path == "/api/application/uploads"
    assert document == {
        "name": "context.txt",
        "mime": "text/plain",
        "data": "c2FtcGxl",
        "session_id": None,
    }
    assert statuses == {200}
    assert staged.path == "/tmp/upload-context.txt"


def test_question_state_folds_asked_and_answered_entries():
    asked = EntryResponse.model_validate({
        "entry_id": "question-asked",
        "type": "question_asked",
        "cursor": 1,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": "turn-one",
        "occurred_at": 1.0,
        "summary": None,
        "body": {
            "attention_id": "attention-one",
            "questions": [{
                "question_id": "question-one",
                "title": "Colour",
                "question": "Which colour?",
                "multiple": False,
                "choices": [
                    {"label": "Blue", "description": "Use blue"},
                    {"label": "Green", "description": "Use green"},
                ],
            }],
        },
    })
    answered = EntryResponse.model_validate({
        "entry_id": "question-answered",
        "type": "question_answered",
        "cursor": 2,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": "turn-one",
        "occurred_at": 2.0,
        "summary": None,
        "body": {
            "attention_id": "attention-one",
            "answers": [{"question_id": "question-one", "labels": ["Blue"]}],
            "feedback": None,
        },
    })

    questions = SessionSnapshot(session_data(2), (asked, answered)).questions()

    assert len(questions) == 1
    assert questions[0].pending is False
    assert questions[0].questions[0].question == "Which colour?"
    assert questions[0].answers is not None
    assert questions[0].answers[0].labels == ("Blue",)
