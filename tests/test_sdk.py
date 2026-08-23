"""Hermetic checks for the typed application client."""

from __future__ import annotations

from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from api.sessiondata.models.entry import EntryPageResponse, EntryResponse
from api.sessiondata.models.session_data import SessionDataResponse
from sdk.client import SessionRef, SessionsResource, SessionWatch
from sdk.state import SessionSnapshot
from sdk.transport import ApiFailure, HttpTransport
from tests.e2e.testkit import selectors
from tests.e2e.testkit.references import References


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


def test_named_references_reject_rebinding_and_unknown_names():
    references = References[int]("command")
    references.bind("build", 1)

    with pytest.raises(AssertionError, match="already bound"):
        references.bind("build", 2)
    with pytest.raises(AssertionError, match=r"available names: \['build'\]"):
        references.get("missing")
