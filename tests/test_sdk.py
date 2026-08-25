"""Hermetic checks for the typed application client."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from api.sessiondata.models.entry import EntryPageResponse, EntryResponse
from api.controls.models.control_outcome_response import ControlResultResponse
from api.sessiondata.models.session_data import SessionDataListResponse, SessionDataResponse
from sdk import sse
from sdk.client import (
    AUTOMATIC_NAME_TIMEOUT_SECONDS,
    SessionRef,
    SessionsResource,
    SessionWatch,
    StreamsResource,
    TerminalResource,
    UploadsResource,
)
from sdk.state import SessionSnapshot
from sdk.transport import ApiFailure, HttpTransport
from tests.e2e.testkit import selectors
from tests.e2e.testkit import turns as turn_checks
from tests.e2e.testkit.references import References, TurnRef


def session_data(
    cursor: int = 1001,
    *,
    session_id: str = "session-one",
    continued_from: str | None = None,
    live: bool = True,
) -> SessionDataResponse:
    return SessionDataResponse.model_validate({
        "cursor": cursor,
        "session": {
            "session_id": session_id,
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
            "continued_from": continued_from,
        },
        "actors": [],
        "live": live,
        "project_directory": "/work",
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


def lead_message_entry(
    cursor: int,
    text: str,
    *,
    turn_id: str | None,
) -> EntryResponse:
    return EntryResponse.model_validate({
        "entry_id": f"lead-message-{cursor}",
        "type": "message",
        "cursor": cursor,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": turn_id,
        "occurred_at": float(cursor),
        "summary": None,
        "body": {
            "message_id": f"lead-message-{cursor}",
            "role": "assistant",
            "phase": "end_turn",
            "content": {"text": text, "media_type": "text/plain"},
            "recipient_actor_id": None,
            "reply_to": None,
        },
    })


def turn_finished_entry(cursor: int, turn_id: str | None) -> EntryResponse:
    return EntryResponse.model_validate({
        "entry_id": f"turn-finished-{cursor}",
        "type": "turn_finished",
        "cursor": cursor,
        "actor_id": "lead-one",
        "parent_actor_id": None,
        "turn_id": turn_id,
        "occurred_at": float(cursor),
        "summary": None,
        "body": {"state": "finished"},
    })


def prompt_entry(
    cursor: int,
    text: str,
    *,
    actor_id: str = "lead-one",
    turn_id: str | None = "turn-one",
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


class InvalidPageTransport:
    def __init__(self, data: SessionDataResponse, items: tuple[EntryResponse, ...]) -> None:
        self.data = data
        self.items = items

    def get(self, path, _adapter):
        if path == "/sessionData/session-one":
            return self.data
        return EntryPageResponse(
            items=self.items,
            oldest_cursor=min((item.cursor for item in self.items), default=0),
            has_more=False,
        )


class EventStreamTransport:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.requests: list[tuple[str, dict[str, str] | None]] = []

    @contextmanager
    def event_stream(
        self,
        path: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> Iterator[Iterator[str]]:
        self.requests.append((path, headers))
        yield iter(self.lines)


class ControlTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, set[int]]] = []
        self.timeouts: list[float | None] = []

    def get(self, path, _adapter):
        if path == "/sessionData/session-one":
            return session_data()
        return EntryPageResponse(items=(), oldest_cursor=0, has_more=False)

    def post(self, path, document, _adapter, accepted_statuses, *, timeout=None):
        self.posts.append((path, document, accepted_statuses))
        self.timeouts.append(timeout)
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


class PaneTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, set[int]]] = []

    def post(self, path, document, adapter, accepted_statuses):
        self.posts.append((path, document, accepted_statuses))
        return 200, adapter.validate_python({
            "handled": True,
            "succeeded": True,
            "reason": None,
        })


class LaunchTransport:
    def __init__(self) -> None:
        self.posts: list[tuple[str, object, set[int]]] = []

    def get(self, path, _adapter):
        assert path == "/sessionData"
        return SessionDataListResponse(cursor=0, sessions=())

    def post(self, path, document, adapter, accepted_statuses):
        self.posts.append((path, document, accepted_statuses))
        return 202, adapter.validate_python({
            "status": "started",
            "window_id": "window-one",
            "reason": None,
        })


def test_session_launch_sends_an_explicit_account_selection():
    transport = LaunchTransport()
    sessions = SessionsResource(cast(HttpTransport, transport))

    sessions.launch(
        "claude_code",
        workspace="/work",
        prompt="hello",
        model="haiku",
        effort="low",
        account_id="account-one",
    )

    path, document, statuses = transport.posts[0]
    assert path == "/api/sessions"
    assert document["account_id"] == "account-one"
    assert statuses == {202, 409}


class PromptOwnerSessions(SessionsResource):
    def __init__(self, snapshots: dict[str, SessionSnapshot]) -> None:
        self.snapshots = snapshots

    def list(self) -> SessionDataListResponse:
        return SessionDataListResponse(
            cursor=max(snapshot.cursor for snapshot in self.snapshots.values()),
            sessions=tuple(snapshot.data for snapshot in self.snapshots.values()),
        )

    def snapshot(self, session: SessionRef) -> SessionSnapshot:
        return self.snapshots[session.session_id]


def test_a_session_snapshot_reads_all_pages_at_one_cursor():
    transport = PagedTransport()
    sessions = SessionsResource(cast(HttpTransport, transport))

    snapshot = sessions.snapshot(SessionRef("session-one"))

    assert [entry.cursor for entry in snapshot.entries] == list(range(1, 1002))
    assert len(transport.paths) == 3


def test_a_session_snapshot_read_reports_its_page_count():
    sessions = SessionsResource(cast(HttpTransport, PagedTransport()))

    result = sessions.read_snapshot(SessionRef("session-one"))

    assert result.page_count == 2


def test_a_session_snapshot_rejects_a_repeated_entry():
    repeated = message_entry(1)
    sessions = SessionsResource(cast(
        HttpTransport,
        InvalidPageTransport(session_data(1), (repeated, repeated)),
    ))

    with pytest.raises(ApiFailure, match="repeated entry id"):
        sessions.snapshot(SessionRef("session-one"))


def test_a_session_snapshot_rejects_an_entry_newer_than_its_cursor():
    sessions = SessionsResource(cast(
        HttpTransport,
        InvalidPageTransport(session_data(1), (message_entry(2),)),
    ))

    with pytest.raises(ApiFailure, match="newer than snapshot cursor 1"):
        sessions.snapshot(SessionRef("session-one"))


def test_a_session_snapshot_rejects_a_page_that_cannot_make_progress():
    sessions = SessionsResource(cast(HttpTransport, StalledTransport()))

    with pytest.raises(ApiFailure, match="returned no entries"):
        sessions.snapshot(SessionRef("session-one"))


def test_the_sse_parser_reads_comments_and_multiline_data():
    found = tuple(sse.events([
        ": heartbeat",
        "event: sample",
        "id: 17",
        "data: first",
        "data: second",
        "",
    ]))

    assert found == (sse.SseEvent("sample", "17", "first\nsecond"),)


def test_the_session_stream_returns_a_typed_update_and_sends_the_resume_cursor():
    transport = EventStreamTransport([
        ": heartbeat",
        "event: sessionData",
        "id: 7",
        f"data: {{\"entries\":[{message_entry(7).model_dump_json()}]}}",
        "",
    ])
    streams = StreamsResource(cast(HttpTransport, transport))

    update = streams.next_session_update(
        SessionRef("session-one"),
        after_cursor=2,
        last_event_id=5,
    )

    assert update.cursor == 7
    assert [entry.entry_id for entry in update.frame.entries] == ["entry-7"]
    assert transport.requests == [(
        "/sessionData/session-one/stream?after_cursor=2",
        {"Last-Event-ID": "5"},
    )]


def test_the_global_stream_skips_ready_and_returns_a_typed_update():
    transport = EventStreamTransport([
        "event: ready",
        "data: {\"boot_id\":\"boot-one\"}",
        "",
        "event: sessionData",
        "id: 8",
        f"data: {{\"sessions\":[{session_data(8).session.model_dump_json()}]}}",
        "",
    ])
    streams = StreamsResource(cast(HttpTransport, transport))

    update = streams.next_global_update(after_cursor=3)

    assert update.cursor == 8
    assert [session.session_id for session in update.frame.sessions] == ["session-one"]
    assert transport.requests == [("/sessionData/stream?after_cursor=3", None)]


def test_a_stream_error_frame_becomes_an_api_failure():
    streams = StreamsResource(cast(HttpTransport, EventStreamTransport([
        "event: error",
        "data: {\"error\":\"stream failed\"}",
        "",
    ])))

    with pytest.raises(ApiFailure, match="stream failed"):
        streams.next_global_update(after_cursor=0)


def test_prompt_owner_follows_a_declared_session_continuation():
    source = SessionSnapshot(
        session_data(session_id="session-old", live=False),
        (),
    )
    continuation = SessionSnapshot(
        session_data(
            cursor=1002,
            session_id="session-new",
            continued_from="session-old",
        ),
        (prompt_entry(1002, "Revised prompt"),),
    )
    sessions = PromptOwnerSessions({
        "session-old": source,
        "session-new": continuation,
    })

    owner = sessions.wait_for_prompt_owner(
        SessionRef("session-old"),
        prompt="Revised prompt",
        after_cursor=1001,
        timeout=0.1,
    )

    assert owner == SessionRef("session-new")


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


def test_a_turn_matches_a_named_native_attachment_and_the_exact_prompt_suffix():
    reference = TurnRef(
        SessionRef("session-one"),
        "Inspect the image. Reply only with its code.",
        0,
        1,
        native_attachment_names=("visible-marker.png",),
    )

    assert selectors._prompt_matches(
        reference,
        '[Image #1]Image attachment "visible-marker.png":\n'
        "Inspect the image. Reply only with its code.",
    )
    assert not selectors._prompt_matches(
        reference,
        '[Image #1]Image attachment "other.png":\n'
        "Inspect the image. Reply only with its code.",
    )
    assert not selectors._prompt_matches(
        reference,
        '[Image #1]Image attachment "visible-marker.png":\nInspect a different image.',
    )


def test_a_turn_uses_its_prompt_message_when_the_harness_has_no_turn_id():
    delivered = (
        '[Image #1]Image attachment "visible-marker.png":\n'
        "Inspect the image. Reply only with its code."
    )
    reference = TurnRef(
        SessionRef("session-one"),
        "Inspect the image. Reply only with its code.",
        0,
        1,
        actor_id="lead-one",
        native_attachment_names=("visible-marker.png",),
    )
    snapshot = SessionSnapshot(
        session_data(1),
        (prompt_entry(1, delivered, turn_id=None),),
    )

    found = selectors.turn(cast(SessionWatch, FixedWatch(snapshot)), reference, timeout=1.0)

    assert found.turn_id is None
    assert found.prompt_cursor == 1
    assert found.prompt_message_id == "prompt-message-1"


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


def test_a_later_autonomous_completion_does_not_add_an_answer_to_the_named_turn():
    reference = TurnRef(
        SessionRef("session-one"),
        "delegate",
        0,
        1,
        actor_id="lead-one",
        turn_id="turn-one",
        prompt_cursor=1,
    )
    snapshot = SessionSnapshot(
        session_data(5),
        (
            prompt_entry(1, "delegate"),
            turn_finished_entry(2, "turn-one"),
            lead_message_entry(3, "launched", turn_id=None),
            turn_finished_entry(4, None),
            lead_message_entry(5, "notification", turn_id=None),
        ),
    )

    assert [entry.body.content.text for entry in turn_checks.enders(snapshot, reference)] == [
        "launched"
    ]


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
    assert assignment.owner_actor_id == "lead-one"
    assert assignment.turn_id == "turn-one"
    assert assignment.assigned_actor_name == "ticker"
    assert assignment.requested_prompt == "run a command"
    assert assignment.state == "succeeded"


def test_a_team_assignment_uses_its_last_message_when_idle_has_no_result():
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
    final_message = EntryResponse.model_validate({
        "entry_id": "child-message",
        "type": "message",
        "cursor": 2,
        "actor_id": "child-one",
        "parent_actor_id": "lead-one",
        "turn_id": None,
        "occurred_at": 2.0,
        "summary": None,
        "body": {
            "message_id": "child-message",
            "role": "assistant",
            "phase": "intermediate",
            "content": {"text": "TEAM_DONE", "media_type": "text/plain"},
            "recipient_actor_id": "lead-one",
            "reply_to": None,
        },
    })
    finished = EntryResponse.model_validate({
        "entry_id": "assignment-finished",
        "type": "assignment_finished",
        "cursor": 3,
        "actor_id": "child-one",
        "parent_actor_id": "lead-one",
        "turn_id": None,
        "occurred_at": 3.0,
        "summary": None,
        "body": {
            "assignment_id": "assignment-one",
            "state": "succeeded",
            "result": None,
        },
    })

    assignment = SessionSnapshot(
        session_data(3),
        (started, final_message, finished),
    ).assignments()[0]

    assert assignment.actor_id == "child-one"
    assert assignment.result == "TEAM_DONE"


def test_a_claude_assignment_uses_the_child_prompt_before_the_child_finishes():
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
    child_prompt = EntryResponse.model_validate({
        "entry_id": "child-prompt",
        "type": "message",
        "cursor": 2,
        "actor_id": "child-one",
        "parent_actor_id": "lead-one",
        "turn_id": "child-turn",
        "occurred_at": 2.0,
        "summary": None,
        "body": {
            "message_id": "child-message",
            "role": "parent",
            "phase": "prompt",
            "content": {"text": "run a command", "media_type": "text/plain"},
            "recipient_actor_id": None,
            "reply_to": None,
        },
    })

    assignment = SessionSnapshot(
        session_data(2),
        (started, child_prompt),
    ).assignments()[0]

    assert assignment.owner_actor_id == "lead-one"
    assert assignment.actor_id == "child-one"
    assert assignment.state is None


def test_two_equal_pending_assignments_do_not_guess_a_child_actor():
    def started(assignment_id: str, cursor: int) -> EntryResponse:
        return EntryResponse.model_validate({
            "entry_id": f"assignment-started-{cursor}",
            "type": "assignment_started",
            "cursor": cursor,
            "actor_id": "lead-one",
            "parent_actor_id": None,
            "turn_id": "turn-one",
            "occurred_at": float(cursor),
            "summary": None,
            "body": {
                "assignment_id": assignment_id,
                "assigned_actor_name": assignment_id,
                "prompt": {"text": "same work", "media_type": "text/plain"},
            },
        })

    child_prompt = EntryResponse.model_validate({
        "entry_id": "child-prompt",
        "type": "message",
        "cursor": 3,
        "actor_id": "child-one",
        "parent_actor_id": "lead-one",
        "turn_id": "child-turn",
        "occurred_at": 3.0,
        "summary": None,
        "body": {
            "message_id": "child-message",
            "role": "parent",
            "phase": "prompt",
            "content": {"text": "same work", "media_type": "text/plain"},
            "recipient_actor_id": None,
            "reply_to": None,
        },
    })

    assignments = SessionSnapshot(
        session_data(3),
        (started("assignment-one", 1), started("assignment-two", 2), child_prompt),
    ).assignments()

    assert [item.actor_id for item in assignments] == [None, None]


def test_a_late_generic_plan_rejection_does_not_erase_feedback():
    def plan_entry(cursor: int, entry_type: str, body: dict) -> EntryResponse:
        return EntryResponse.model_validate({
            "entry_id": f"plan-{cursor}",
            "type": entry_type,
            "cursor": cursor,
            "actor_id": "lead-one",
            "parent_actor_id": None,
            "turn_id": "turn-one",
            "occurred_at": float(cursor),
            "summary": None,
            "body": {"attention_id": "plan-one", **body},
        })

    plan = SessionSnapshot(
        session_data(3),
        (
            plan_entry(1, "plan_proposed", {
                "plan": {"text": "Do it", "media_type": "text/markdown"},
            }),
            plan_entry(2, "plan_resolved", {
                "state": "changes_requested",
                "feedback": "start with tests",
                "edited": False,
            }),
            plan_entry(3, "plan_resolved", {
                "state": "rejected",
                "feedback": None,
                "edited": False,
            }),
        ),
    ).plans()[0]

    assert plan.state == "changes_requested"
    assert plan.feedback == "start with tests"


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


def test_automatic_name_allows_two_model_provider_attempts():
    transport = ControlTransport()
    sessions = SessionsResource(cast(HttpTransport, transport))

    sessions.auto_name(SessionRef("session-one"))

    assert transport.timeouts == [AUTOMATIC_NAME_TIMEOUT_SECONDS]


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


def test_terminal_resource_uses_named_pane_gestures():
    transport = PaneTransport()
    terminal = TerminalResource(cast(HttpTransport, transport))

    outcomes = (
        terminal.toggle_panes(window_id="window-one", workspace="/work"),
        terminal.grow_activity_pane(
            window_id="window-one",
            workspace="/work",
            columns=7,
        ),
        terminal.shrink_activity_pane(
            window_id="window-one",
            workspace="/work",
            columns=5,
        ),
        terminal.set_activity_pane_width(
            window_id="window-one",
            workspace="/work",
            percent=35,
        ),
        terminal.reset_activity_pane(window_id="window-one", workspace="/work"),
    )

    assert all(outcome.handled and outcome.succeeded for outcome in outcomes)
    assert transport.posts == [
        (
            "/api/terminal/panes/toggle",
            {"window_id": "window-one", "working_directory": "/work"},
            {200, 409},
        ),
        (
            "/api/terminal/panes/grow",
            {"window_id": "window-one", "working_directory": "/work", "columns": 7},
            {200, 409},
        ),
        (
            "/api/terminal/panes/shrink",
            {"window_id": "window-one", "working_directory": "/work", "columns": 5},
            {200, 409},
        ),
        (
            "/api/terminal/panes/set-percent",
            {"window_id": "window-one", "working_directory": "/work", "percent": 35},
            {200, 409},
        ),
        (
            "/api/terminal/panes/reset",
            {"window_id": "window-one", "working_directory": "/work"},
            {200, 409},
        ),
    ]


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
