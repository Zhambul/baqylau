"""Real HTTP routing over the canonical application services."""

from __future__ import annotations

import asyncio
import dataclasses
import http.client
import json
import socket
import sqlite3
import threading
import time
from urllib.parse import quote

from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from api import config as api_config
from api import dependencies
from api.app import build_web_application
from api.dashboard.models.controls.send_text_request import SendTextRequest
from api.server import build_server
from app import providers
from canonical_runtime import ProviderGraph
from diagnostics.models import ApplicationErrorRecord
from diagnostics.recorder import AuditRecorder
from diagnostics.telemetry import BrowserTelemetryService
from harness.models import RawEvent, Session, TranslationResult
from dashboard.services.notices import DashboardNotificationState
from dashboard.services.overview import GlobalApplicationService, NewSessionPreferences
from notify.presence import Presence
from domain.events import CanonicalEvent, EventPayload, MessageCreated, SessionStarted
from domain.ids import ActorId, CanonicalEventId, MessageId, RawEventId, SessionId
from domain.values import MessageRole, TextContent

SESSION_ID = SessionId("session-one")
ACTOR_ID = ActorId("actor-one")

SERVER_START_TIMEOUT_SECONDS = 5.0


def _event(event_id: str, payload):
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SESSION_ID,
        actor_id=ACTOR_ID,
        turn_id=None,
        parent_actor_id=None,
        harness="codex",
        occurred_at=10.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )


def _record(application, raw_event, translator_version, translation):
    application.raw_events.record((raw_event,))
    application.canonical_events.record_translation(
        raw_event, translator_version, translation, 10.0
    )


def _application():
    """A seeded graph: the registry the app under test is handed, plus attribute
    access to the nodes this suite asserts on. The databases are the ones
    conftest's environment points at, per test."""
    application = ProviderGraph()
    application.sessions.save(
        "codex",
        Session(SESSION_ID, ACTOR_ID, "native", "fixture", "/work"),
    )
    events = (
        _event("session", SessionStarted("/work", "fixture.jsonl", None, None, None, None, None)),
        _event(
            "message",
            MessageCreated(
                MessageId("message-one"),
                "assistant",
                TextContent("hello"),
                "final",
                None,
            ),
        ),
    )
    for index, event in enumerate(events):
        _record(
            application,
            RawEvent(
                RawEventId(f"raw-{index}"),
                "codex",
                "fixture",
                "fixture",
                str(index),
                SESSION_ID,
                ACTOR_ID,
                None,
                100.0 + index,
                "json",
                b"{}",
            ),
            "1",
            TranslationResult((event,), "translated"),
        )
    return application


class _RunningDaemon:
    """The daemon's real engine (api.server.build_server) on an ephemeral
    port, with the shutdown verbs the tests always used."""

    def __init__(self, server, bound_socket):
        self.server = server
        self.bound_socket = bound_socket
        self.server_port = bound_socket.getsockname()[1]

    def shutdown(self):
        # force: the SSE streams never close on their own, and a test
        # teardown must not serve the graceful grace period.
        self.server.force_exit = True
        self.server.should_exit = True

    def server_close(self):
        # The engine owns the socket once run() takes it and closes it during
        # its own shutdown; closing here too races that and trips the loop.
        pass


def _fixed(value):
    """An override provider that takes NO parameters. A closure with a default
    argument would make FastAPI read that default as a query parameter."""

    def provider():
        return value

    return provider


def _server(application, overrides=None):
    bound_socket = socket.create_server(("127.0.0.1", 0))
    web = build_web_application(application.instances)
    for provider, value in (overrides or {}).items():
        # FastAPI's own seam: one node substituted, the rest of the graph real.
        web.dependency_overrides[provider] = _fixed(value)
    server = build_server(web)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [bound_socket]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.01)
    return _RunningDaemon(server, bound_socket), thread


def _get(server, path: str):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader("Content-Type"), body


def _get_response(server, path: str, read_body: bool = True):
    """One GET, exposing the response HEADERS as well as its body.

    `read_body=False` is for the event streams, whose body never ends: the
    headers are already out by then, which is the whole point of a stream.
    """
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read() if read_body else b""
        # response.headers, not dict(getheaders()): a header name is
        # case-insensitive and the wire spells these lowercase, so a plain dict
        # would make every lookup below a lie about what was sent.
        return response.status, response.headers, body
    finally:
        connection.close()


def _post_without_a_declared_length(server, path: str, body: bytes) -> int:
    """A chunked POST, spoken by hand because neither http.client nor fetch will
    build one for a body it can measure. Returns the status code only."""
    head = (
        "POST %s HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        "Content-Type: application/json\r\nX-Baqylau: 1\r\n"
        "Transfer-Encoding: chunked\r\n\r\n" % path
    ).encode()
    with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as raw:
        raw.sendall(head + b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body))
        return int(raw.recv(4096).split(b" ")[1])


def _post(server, path: str, body: dict):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    encoded = json.dumps(body).encode()
    connection.request(
        "POST",
        path,
        body=encoded,
        headers={"Content-Type": "application/json", "X-Baqylau": "1"},
    )
    response = connection.getresponse()
    response_body = response.read()
    connection.close()
    return response.status, response_body


def test_plural_session_resources_use_the_canonical_snapshot_and_activity(tmp_path):
    application = _application()
    # The reader and the writer address the SAME file now, so the error can be
    # recorded through the graph instead of by hand-making a table beside it.
    application.audit.record_error(
        ApplicationErrorRecord(str(SESSION_ID), "dashboard", "render", "trace", "{}", 1, 12.5)
    )
    server, thread = _server(application)
    try:
        status, _content_type, body = _get(server, "/api/sessions")
        assert status == 200
        session_item = json.loads(body)[0]
        assert session_item["session"]["session_id"] == "session-one"
        assert "window_id" in session_item["terminal"]

        status, _content_type, body = _get(server, "/api/sessions/session-one")
        page_snapshot = json.loads(body)
        assert status == 200
        assert page_snapshot["canonical"]["cursor"] == 2
        assert page_snapshot["canonical"]["session"]["working_directory"] == "/work"
        assert "window_id" in page_snapshot["application"]["terminal"]
        assert "memory" not in page_snapshot["application"]
        assert page_snapshot["application"]["errors"] == [
            {
                "error_id": 1,
                "timestamp": 12.5,
                "component": "dashboard",
                "action": "render",
                "traceback": "trace",
                "context": "{}",
            }
        ]

        status, _content_type, body = _get(server, "/api/harnesses")
        harnesses = {row["name"]: row for row in json.loads(body)}
        assert status == 200
        assert "supports_memory" not in harnesses["claude_code"]

        status, _content_type, body = _get(
            server, "/api/sessions/session-one/memory"
        )
        assert status == 404
        assert json.loads(body)["error"] == "not found"

        for legacy_path in (
            "/api/session/session-one",
            "/api/session/session-one/errors",
            "/api/session/session-one/ops",
            "/api/session/session-one/history",
            "/api/session/session-one/backlog",
            "/api/session/session-one/copy/group/out",
            "/api/session/session-one/view/group",
        ):
            status, _content_type, _body = _get(server, legacy_path)
            assert status == 404

        status, _content_type, body = _get(
            server,
            "/api/sessions/session-one/activity?block_count=10",
        )
        page = json.loads(body)
        assert status == 200
        assert page["latest_cursor"] == 2
        assert page["items"][0]["plain_text"] == "hello"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_main_scope_shows_only_lead_activity_and_actor_scope_shows_the_actor(tmp_path):
    application = _application()

    def record_message(
        event_id: str,
        actor_id: ActorId,
        parent_actor_id: ActorId | None,
        role: MessageRole,
        text: str,
    ) -> None:
        event: CanonicalEvent[EventPayload] = CanonicalEvent(
            event_id=CanonicalEventId(event_id),
            session_id=SESSION_ID,
            actor_id=actor_id,
            turn_id=None,
            parent_actor_id=parent_actor_id,
            harness="codex",
            occurred_at=20.0,
            terminal_window_id=None,
            harness_process_id=None,
            payload=MessageCreated(MessageId(event_id), role, TextContent(text), None, None),
        )
        _record(
            application,
            RawEvent(
                RawEventId(f"raw-{event_id}"),
                "codex",
                "fixture",
                "fixture",
                event_id,
                SESSION_ID,
                actor_id,
                parent_actor_id,
                20.0,
                "json",
                b"{}",
            ),
            "1",
            TranslationResult((event,), "translated"),
        )

    record_message("system-message", ACTOR_ID, None, "system", "instructions")
    record_message("child-message", ActorId("child-one"), ACTOR_ID, "assistant", "child reply")
    server, thread = _server(application)
    try:
        status, _content_type, body = _get(
            server,
            "/api/sessions/session-one/activity?block_count=10",
        )
        assert status == 200
        assert [item["plain_text"] for item in json.loads(body)["items"]] == [
            "hello",
            "instructions",
        ]

        status, _content_type, body = _get(
            server,
            "/api/sessions/session-one/activity?block_count=10&actor_id=child-one",
        )
        assert status == 200
        assert [item["plain_text"] for item in json.loads(body)["items"]] == [
            "child reply",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_read_only_mode_refuses_every_control_plane_post(tmp_path):
    """BAQYLAU_DASHBOARD_READONLY: remote eyes, no remote hands.

    The switch was untestable while it was an import-time module constant — it
    was decided before the first test imported anything. It is a field of the
    injected policy now, so this asserts the thing the deployment actually
    relies on: reads still answer, every mutation is a 403.
    """
    application = _application()
    read_only = dataclasses.replace(api_config.settings(), readonly=True)
    server, thread = _server(application, {dependencies.policy: read_only})
    try:
        status, _content_type, _body = _get(server, "/api/sessions")
        assert status == 200

        status, body = _post(
            server,
            "/api/sessions/session-one/controls/interrupt",
            {"reason": "user"},
        )
        assert status == 403
        assert json.loads(body) == {"error": "control plane disabled (read-only)"}

        status, body = _post(
            server, "/api/terminal/panes/toggle",
            {"window_id": "1", "working_directory": "/work"},
        )
        assert status == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_content_resource_resolves_a_canonical_field(tmp_path):
    server, thread = _server(_application())
    try:
        reference = quote("message:content", safe="")
        status, content_type, body = _get(server, f"/api/content/{reference}")
        assert status == 200
        assert content_type == "text/plain; charset=utf-8"
        assert body == b"hello"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_insights_use_typed_canonical_application_data(tmp_path):
    server, thread = _server(_application())
    try:
        status, _content_type, body = _get(server, "/api/insights")
        assert status == 200
        insights = json.loads(body)
        assert insights["total_session_count"] == 1
        assert insights["all_time"]["session_count"] == 1
        assert insights["all_time"]["finished_session_count"] == 0
        assert insights["projects"][0]["working_directory"] == "/work"
        assert insights["daily_sessions"][0]["session_count"] == 1
        assert set(insights["hourly_sessions"][0]) == {
            "day_of_week",
            "hour",
            "session_count",
        }

        status, _content_type, _body = _get(server, "/api/stats")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_resumable_sessions_come_from_canonical_session_summaries(tmp_path):
    server, thread = _server(_application())
    try:
        status, _content_type, body = _get(
            server,
            "/api/resumable-sessions?working_directory=%2Fwork&search=session-one",
        )
        assert status == 200
        assert json.loads(body) == [
            {
                "session_id": "session-one",
                "title": None,
                "last_activity_at": 10.0,
                "active": False,
                "harness": "codex",
                "model": None,
                "effort": None,
                "account": None,
            }
        ]

        status, _content_type, body = _get(
            server,
            "/api/resumable-sessions?working_directory=%2Fother",
        )
        assert status == 200
        assert json.loads(body) == []

        status, _content_type, _body = _get(server, "/api/resumable?cwd=%2Fwork")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_stream_uses_the_canonical_cursor_as_the_sse_identity(tmp_path):
    server, thread = _server(_application())
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/sessions/session-one/stream?after_cursor=0")
        response = connection.getresponse()
        lines = [response.readline().decode().rstrip("\n") for _ in range(4)]

        assert response.status == 200
        assert lines[0] == "id: 2"
        assert lines[1] == "event: activity"
        frame = json.loads(lines[2].removeprefix("data: "))
        assert frame["cursor"] == frame["snapshot"]["cursor"] == 2
        assert frame["items"][0]["plain_text"] == "hello"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_stream_last_event_id_is_authoritative(tmp_path):
    server, thread = _server(_application())
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "GET",
            "/api/sessions/session-one/stream?after_cursor=2",
            headers={"Last-Event-ID": "0"},
        )
        response = connection.getresponse()
        lines = [response.readline().decode().rstrip("\n") for _ in range(4)]
        assert lines[:2] == ["id: 2", "event: activity"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_application_stream_updates_view_mode_without_activity(tmp_path):
    application = _application()
    application.session_application.set_view_mode(SESSION_ID, "focus")
    server, thread = _server(application)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/sessions/session-one/stream?after_cursor=2")
        response = connection.getresponse()
        initial = [response.readline().decode().rstrip("\n") for _ in range(3)]
        assert initial[0] == "event: application"
        assert (
            json.loads(initial[1].removeprefix("data: "))["preferences"]["view_mode"]
            == "focus"
        )

        application.session_application.set_view_mode(SESSION_ID, "verbose")
        changed = [response.readline().decode().rstrip("\n") for _ in range(3)]
        assert changed[0] == "event: application"
        assert (
            json.loads(changed[1].removeprefix("data: "))["preferences"]["view_mode"]
            == "verbose"
        )
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_session_application_routes_publish_complete_composer_state(tmp_path):
    application = _application()
    server, thread = _server(application)
    try:
        status, body = _post(
            server,
            "/api/sessions/session-one/application/composer-draft",
            {"text": "half written", "origin": "browser-one", "sequence": 20},
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}

        status, body = _post(
            server,
            "/api/sessions/session-one/application/composer-draft",
            {"text": "older", "origin": "browser-two", "sequence": 10},
        )
        assert status == 200
        assert json.loads(body) == {"saved": False}

        status, body = _post(
            server,
            "/api/sessions/session-one/application/composer-queue",
            {"items": [{"text": "next message"}], "origin": "browser-one"},
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}

        status, _content_type, body = _get(server, "/api/sessions/session-one")
        assert status == 200
        state = json.loads(body)["application"]
        assert state["composer"] == {
            "draft": {
                "text": "half written",
                "origin": "browser-one",
                "sequence": 20.0,
            },
            "queue": {
                "items": [{"text": "next message"}],
                "origin": "browser-one",
            },
        }
        assert state["dialog"] == {"draft": None}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_global_stream_sends_complete_current_application_snapshots(tmp_path):
    application = _application()
    state = DashboardNotificationState()
    overview = GlobalApplicationService(
        application.dashboard_sessions,
        application.usage_state,
        state,
        application.new_sessions,
        application.notification_settings,
        application.hidden_directories,
        application.push_subscriptions,
        application.presence,
    )
    server, thread = _server(application, {providers.global_application: overview})
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/stream")
        response = connection.getresponse()

        ready = [response.readline().decode().rstrip("\n") for _ in range(3)]
        initial = [response.readline().decode().rstrip("\n") for _ in range(3)]
        assert ready[0] == "event: ready"
        assert initial[0] == "event: application"
        initial_snapshot = json.loads(initial[1].removeprefix("data: "))
        assert initial_snapshot["sessions"][0]["session"]["session_id"] == "session-one"
        assert initial_snapshot["preferences"] == {
            "new_session": {
                "working_directory": None,
                "harness": None,
                "model": None,
                "effort": None,
            },
            "new_session_drafts": [],
            "hidden_directories": {},
            "limits": {
                "upload_bytes": initial_snapshot["preferences"]["limits"]["upload_bytes"],
                "rename_characters": initial_snapshot["preferences"]["limits"]["rename_characters"],
                "presence_seconds": initial_snapshot["preferences"]["limits"]["presence_seconds"],
            },
        }
        assert initial_snapshot["preferences"]["limits"]["upload_bytes"] > 0
        assert initial_snapshot["preferences"]["limits"]["rename_characters"] > 0
        assert initial_snapshot["preferences"]["limits"]["presence_seconds"] > 0

        state.publish_notification("session-one", "done", "work", "finished")
        changed = [response.readline().decode().rstrip("\n") for _ in range(3)]
        assert changed[0] == "event: application"
        changed_snapshot = json.loads(changed[1].removeprefix("data: "))
        assert changed_snapshot["notifications"]["latest"] == {
            "revision": 1,
            "session_id": "session-one",
            "kind": "done",
            "project": "work",
            "title": "finished",
        }
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_global_application_routes_replace_field_specific_preferences_routes(
    tmp_path, monkeypatch
):
    presence_calls = []

    class RecordingPresence(Presence):
        """The presence node, recording what the routes report to it. A subclass
        rather than three patched module functions: presence is an object now,
        and overriding the node is how a test substitutes one."""

        def mark_device(self, device):
            presence_calls.append(("device", device))

        def mark_viewing(self, session_id):
            presence_calls.append(("viewing", session_id))

        def mark_away(self, device, session_id=None):
            presence_calls.append(("away", device, session_id))

    application = _application()
    server, thread = _server(application, {providers.presence: RecordingPresence()})
    try:
        status, body = _post(
            server,
            "/api/application/new-session-preferences",
            {
                "working_directory": "/project",
                "harness": "codex",
                "model": "gpt-5",
                "effort": "high",
            },
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}

        status, body = _post(
            server,
            "/api/application/new-session-drafts",
            {
                "working_directory": "/project",
                "text": "unfinished",
                "sequence": 25,
            },
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}

        status, body = _post(
            server,
            "/api/application/hidden-directories",
            {"working_directory": "/parked"},
        )
        assert status == 200
        assert "/parked" in json.loads(body)["hidden"]

        snapshot = application.global_application.snapshot()
        assert snapshot.preferences.new_session == NewSessionPreferences(
            "/project", "codex", "gpt-5", "high"
        )
        assert snapshot.preferences.new_session_drafts[0].working_directory == "/project"
        assert snapshot.preferences.new_session_drafts[0].text == "unfinished"
        assert "/parked" in snapshot.preferences.hidden_directories

        status, body = _post(
            server,
            "/api/application/push-subscriptions",
            {
                "subscription": {
                    "endpoint": "https://push.example/subscription",
                    "keys": {"p256dh": "public", "auth": "secret"},
                },
                "device_id": "browser-one",
                "device_label": "Tablet",
            },
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}
        stored = application.push_subscriptions.subscriptions()
        assert [
            (item.endpoint, item.public_key, item.authentication_secret,
             item.device_id, item.device_label)
            for item in stored
        ] == [
            ("https://push.example/subscription", "public", "secret", "browser-one", "Tablet")
        ]

        status, body = _post(
            server,
            "/api/application/presence",
            {"device_id": "browser-one", "session_id": "session-one"},
        )
        assert status == 200
        assert json.loads(body) == {"saved": True}
        status, _body = _post(
            server,
            "/api/application/presence",
            {
                "device_id": "browser-one",
                "session_id": "session-one",
                "away": True,
            },
        )
        assert status == 200
        assert presence_calls == [
            ("device", "browser-one"),
            ("viewing", "session-one"),
            ("away", "browser-one", "session-one"),
        ]

        for legacy_path in (
            "/api/ns-prefs",
            "/api/ns-draft",
            "/api/dirs/hidden",
            "/api/limits",
        ):
            status, _content_type, _body = _get(server, legacy_path)
            assert status == 404
        for legacy_path in ("/api/ns-prefs", "/api/ns-draft", "/api/dirs/hide"):
            status, _body = _post(server, legacy_path, {})
            assert status == 404
        status, _body = _post(server, "/api/push/subscribe", {})
        assert status == 404
        status, _body = _post(server, "/api/presence", {})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_control_request_uses_complete_names_and_structured_attachments():
    request = SendTextRequest.model_validate(
        {
            "request_id": "request-one",
            "text": "inspect",
            "attachments": [
                {
                    "local_path": "/tmp/image.png",
                    "display_name": "image.png",
                    "media_type": "image/png",
                }
            ],
        },
    ).request(SESSION_ID)

    assert request.session_id == SESSION_ID
    assert request.attachments[0].local_path == "/tmp/image.png"
    assert request.attachments[0].display_name == "image.png"

    attachment_only = SendTextRequest.model_validate(
        {
            "request_id": "request-two",
            "text": "",
            "attachments": [
                {"local_path": "/tmp/image.png", "display_name": "image.png"}
            ],
        },
    ).request(SESSION_ID)
    assert attachment_only.text == ""


def test_invalid_canonical_post_is_a_client_error_not_an_old_route(tmp_path):
    server, thread = _server(_application())
    try:
        status, body = _post(
            server,
            "/api/sessions/session-one/controls/send-text",
            {"request_id": "request-one"},
        )
        assert status == 400
        assert "text" in json.loads(body)["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_browser_telemetry_uses_named_application_resources(tmp_path):
    class RecordingAudit:
        """The write repository, counted rather than stored."""

        def __init__(self):
            self.records = []

        def record_state_file(self, state_file):
            self.records.append(
                (state_file.session_id, state_file.path, state_file.action, state_file.content)
            )

    audit = RecordingAudit()
    application = _application()
    server, thread = _server(
        application, {providers.browser_telemetry: BrowserTelemetryService(audit)}
    )
    try:
        status, body = _post(
            server,
            "/api/sessions/session-one/application/optimistic-actions",
            {
                "action": "composer",
                "phase": "reconciled",
                "character_count": 12,
                "elapsed_milliseconds": 40,
            },
        )
        assert status == 200
        assert json.loads(body) == {"recorded": True}

        status, body = _post(
            server,
            "/api/sessions/session-one/application/client-failures",
            {
                "gesture": "send",
                "failure_kind": "transport",
                "error": "connection closed",
            },
        )
        assert status == 200
        assert json.loads(body) == {"recorded": True}

        status, body = _post(
            server,
            "/api/application/browser-events",
            {
                "client_id": "client-one",
                "device_id": "browser-one",
                "connection": {"online": True, "stream_count": 2},
                "events": [
                    {
                        "session_id": "session-one",
                        "name": "send.started",
                        "timestamp": 50,
                        "details": {"character_count": 12},
                    }
                ],
            },
        )
        assert status == 200
        assert json.loads(body) == {"recorded": True}
        assert [record[2] for record in audit.records] == [
            "browser-optimistic-action",
            "browser-client-failure",
            "browser-event",
        ]
        # The content is a diagnostic blob by design — recorded, never queried.
        assert json.loads(audit.records[0][3])["session_id"] == "session-one"

        for legacy_path in (
            "/api/session/session-one/hint-audit",
            "/api/session/session-one/client-fail",
            "/api/clientlog",
        ):
            status, _body = _post(server, legacy_path, {})
            assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# Every control gesture's outcome must leave a row: an `indeterminate` one is a
# FAILURE the transport cannot see (served as HTTP 202, which the browser calls
# success), and before this it left nothing but the driver's reason string in a
# response body nobody records.
def _audited_control(monkeypatch, outcome):
    from harness.services import controls as services
    from harness.models import SelectModel

    rows = []

    class RowRecorder(AuditRecorder):
        def __init__(self):
            pass

        def state_file(self, log, path, action, content=""):
            rows.append((log, action, content))

    service = object.__new__(services.HarnessControlService)
    service.audit = RowRecorder()
    request = SelectModel(SESSION_ID, "request-one", model_id="gpt-5.6-sol")

    def run(_request):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(service, "_execute", run)
    raised = None
    try:
        service.execute(request)
    except Exception as error:      # noqa: BLE001 — the raised-path assertion
        raised = error
    return rows, raised


def test_an_unconfirmed_control_is_audited_with_its_reason(monkeypatch):
    from harness.models import ControlResult

    rows, raised = _audited_control(
        monkeypatch,
        ControlResult("request-one", "indeterminate", "row: no 'all models'"),
    )

    assert raised is None
    assert len(rows) == 1
    log, action, content = rows[0]
    # the session id in its OWN column: browser-event rows bury it in the JSON,
    # which is what made this gesture read as "no audit at all"
    assert log == str(SESSION_ID)
    assert action == "control"
    assert content["control"] == "select_model"
    assert content["status"] == "indeterminate"
    assert content["reason"] == "row: no 'all models'"
    assert isinstance(content["ms"], int)


def test_an_acknowledged_control_is_audited_too(monkeypatch):
    from harness.models import ControlResult

    rows, _ = _audited_control(
        monkeypatch, ControlResult("request-one", "acknowledged")
    )
    assert rows[0][2]["status"] == "acknowledged"
    assert rows[0][2]["reason"] == ""


def test_a_raised_control_is_audited_before_it_propagates(monkeypatch):
    rows, raised = _audited_control(monkeypatch, RuntimeError("driver exploded"))

    assert isinstance(raised, RuntimeError)
    assert rows[0][2]["status"] == "raised"


def test_a_broken_audit_never_takes_down_the_gesture(monkeypatch):
    from harness.services import controls as services
    from harness.models import ControlResult, SelectModel

    class BrokenAudit(AuditRecorder):
        def __init__(self):
            pass

        def state_file(self, log, path, action, content=""):
            raise sqlite3.OperationalError("database is locked")

    service = object.__new__(services.HarnessControlService)
    service.audit = BrokenAudit()
    monkeypatch.setattr(
        service, "_execute", lambda r: ControlResult(r.request_id, "acknowledged")
    )

    outcome = service.execute(SelectModel(SESSION_ID, "request-one", model_id="x"))
    assert outcome.status == "acknowledged"


# --- terminal pane clients ------------------------------------------------------
#
# The pane processes, the keybinding and the click handlers are thin HTTP/SSE
# clients; these routes are the whole surface they stand on.


def _read_sse_event(response):
    """One SSE event as (event, data); server tick comments are skipped."""
    event = None
    data = None
    while True:
        line = response.readline().decode().rstrip("\n")
        if line.startswith(":"):
            continue
        if not line:
            if event is not None:
                return event, data
            continue
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data = json.loads(line.removeprefix("data: "))


def _record_agent_message(application):
    """The mirror hides the lead actor's own messages (the TUI already shows
    them), so pane assertions need a child agent's message."""
    event = CanonicalEvent(
        event_id=CanonicalEventId("agent-message"),
        session_id=SESSION_ID,
        actor_id=ActorId("actor-two"),
        turn_id=None,
        parent_actor_id=ACTOR_ID,
        harness="codex",
        occurred_at=11.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=MessageCreated(
            MessageId("message-two"),
            "assistant",
            TextContent("hello from the agent"),
            "final",
            None,
        ),
    )
    _record(
        application,
        RawEvent(
            RawEventId("raw-agent"),
            "codex",
            "fixture",
            "fixture",
            "9",
            SESSION_ID,
            ActorId("actor-two"),
            ACTOR_ID,
            110.0,
            "json",
            b"{}",
        ),
        "1",
        TranslationResult((event,), "translated"),
    )


def test_pane_mirror_stream_announces_the_session_and_streams_frames(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    application = _application()
    _record_agent_message(application)
    server, thread = _server(application)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/sessions/session-one/panes/mirror/stream?width=100")
        response = connection.getresponse()
        assert response.status == 200
        assert _read_sse_event(response) == ("session", {"session_id": "session-one"})
        event, frame = _read_sse_event(response)
        assert event == "frame"
        assert frame["ansi"].startswith("\033[H\033[2J\033[3J")
        assert "hello from the agent" in frame["ansi"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pane_scoreboard_stream_renders_the_session_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    server, thread = _server(_application())
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "GET", "/api/sessions/session-one/panes/scoreboard/stream?width=100"
        )
        response = connection.getresponse()
        assert response.status == 200
        assert _read_sse_event(response) == ("session", {"session_id": "session-one"})
        event, frame = _read_sse_event(response)
        assert event == "frame"
        assert frame["ansi"].startswith("\033[H\033[2J\033[3J")
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pane_stream_rejects_a_missing_width_before_streaming(tmp_path):
    server, thread = _server(_application())
    try:
        status, _content_type, body = _get(
            server, "/api/sessions/session-one/panes/mirror/stream"
        )
        assert status == 400
        assert "width" in json.loads(body)["error"]
        status, _content_type, _body = _get(
            server, "/api/sessions/session-one/panes/elsewhere/stream?width=80"
        )
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pane_command_route_carries_the_keypress_environment(tmp_path):
    from terminal.panes.commands import PaneCommandOutcome

    class PaneCommands:
        def __init__(self):
            self.calls = []
            self.outcome = PaneCommandOutcome(True, True)

        def execute(self, command, window_id, working_directory, columns=None, percent=None):
            self.calls.append((command, window_id, working_directory, columns, percent))
            return self.outcome

    pane_commands = PaneCommands()
    server, thread = _server(_application(), {providers.pane_commands: pane_commands})
    try:
        status, body = _post(
            server,
            "/api/terminal/panes/set-percent",
            {"window_id": "77", "working_directory": "/work", "percent": 40},
        )
        assert status == 200
        assert json.loads(body) == {"handled": True, "succeeded": True, "reason": None}
        assert pane_commands.calls == [("setpct", "77", "/work", None, 40)]

        pane_commands.outcome = PaneCommandOutcome(True, False, "no pane")
        status, body = _post(
            server,
            "/api/terminal/panes/toggle",
            {"working_directory": "/work"},
        )
        assert status == 409
        assert json.loads(body)["reason"] == "no pane"

        pane_commands.outcome = PaneCommandOutcome(False, True)
        status, body = _post(
            server,
            "/api/terminal/panes/toggle",
            {"working_directory": "/work"},
        )
        assert status == 200
        assert json.loads(body)["handled"] is False

        status, _body = _post(server, "/api/terminal/panes/toggle", {})
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_view_route_owns_the_open_closed_state(tmp_path):
    application = _application()
    views = application.content_views

    server, thread = _server(application)
    try:
        status, body = _post(
            server, "/api/terminal/views", {"content_reference": "event-one:field"}
        )
        assert (status, json.loads(body)) == (200, {"opened": True})
        assert views.opened() == frozenset({"event-one:field"})
        status, body = _post(
            server, "/api/terminal/views", {"content_reference": "event-one:field"}
        )
        assert (status, json.loads(body)) == (200, {"opened": False})
        assert views.opened() == frozenset()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_mirror_frames_share_one_model_across_client_widths(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    application = _application()
    _record_agent_message(application)

    version, wide = application.pane_streams.mirror_frame(SESSION_ID, 120, None)
    assert "hello from the agent" in wide
    assert application.pane_streams.mirror_frame(SESSION_ID, 120, version) is None

    narrow = application.pane_streams.mirror_frame(SESSION_ID, 40, None)
    assert narrow is not None and narrow[0] == version
    assert "hello from the agent" in narrow[1]
    assert len(application.pane_streams._models) == 1


def _post_hook(server, harness: str, payload: bytes, observed: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    headers = {"Content-Type": "application/json", "X-Baqylau": "1"}
    headers.update(observed or {})
    connection.request("POST", f"/api/harnesses/{quote(harness)}/hooks", body=payload, headers=headers)
    response = connection.getresponse()
    response_body = response.read()
    connection.close()
    return response.status, response_body


def test_hook_delivery_records_exact_evidence_and_returns_the_reply(tmp_path):
    application = _application()
    server, thread = _server(application)
    payload = json.dumps({
        "session_id": "hook-session",
        "transcript_path": str(tmp_path / "hook-session.jsonl"),
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "hook_event_id": "pre-one",
        "tool_name": "Bash",
        "tool_use_id": "tool-one",
        "tool_input": {"command": "printf hello"},
    }).encode()
    try:
        status, body = _post_hook(
            server, "claude_code", payload,
            {"X-Baqylau-Terminal-Window": "1114", "X-Baqylau-Harness-Process": "4242"},
        )
        assert status == 200
        assert b"updatedInput" in body

        evidence = application.evidence.evidence_for_session(SessionId("hook-session"))
        assert [raw.source_type for raw in evidence] == ["hook", "output_location"]
        assert evidence[0].payload == payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hook_delivery_ships_the_hooks_observations_not_the_daemons(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "daemon-account")
    application = _application()
    server, thread = _server(application)
    payload = json.dumps({
        "session_id": "hook-session",
        "transcript_path": str(tmp_path / "hook-session.jsonl"),
        "hook_event_name": "SessionStart",
        "hook_event_id": "start-one",
    }).encode()
    try:
        status, _body = _post_hook(
            server, "claude_code", payload, {"X-Baqylau-Account-Id": "c2"}
        )
        assert status == 200
        hook_row = application.evidence.evidence_for_session(SessionId("hook-session"))[0]
        assert hook_row.account_id == "c2"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hook_delivery_rejections_leave_no_evidence(tmp_path):
    application = _application()
    server, thread = _server(application)
    try:
        status, body = _post_hook(server, "mystery", b"{}")
        assert status == 404

        status, body = _post_hook(server, "claude_code", b"not json")
        assert status == 400
        assert "error" in json.loads(body)

        # no transcript path: the gateway refuses, so nothing was recorded
        status, _body = _post_hook(
            server, "claude_code", json.dumps({"session_id": "hook-session"}).encode()
        )
        assert status == 400
        assert application.evidence.evidence_for_session(SessionId("hook-session")) == ()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hook_identity_reuse_with_different_bytes_is_a_conflict_not_a_rewrite(tmp_path):
    application = _application()
    server, thread = _server(application)
    document = {
        "session_id": "hook-session",
        "transcript_path": str(tmp_path / "hook-session.jsonl"),
        "hook_event_name": "PostToolUse",
        "hook_event_id": "post-one",
        "tool_name": "Read",
    }
    try:
        first = json.dumps(document).encode()
        assert _post_hook(server, "claude_code", first)[0] == 200
        # an identical re-delivery is idempotent
        assert _post_hook(server, "claude_code", first)[0] == 200

        changed = json.dumps({**document, "tool_name": "Write"}).encode()
        status, body = _post_hook(server, "claude_code", changed)
        assert status == 409
        assert application.evidence.evidence_for_session(SessionId("hook-session"))[0].payload == first
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- what the HTTP layer owes every caller ------------------------------------
#
# The seven properties below are not about any one route. They are the ones a
# route gets for free and therefore nobody re-checks: that a stream does not
# stall the server, that a cap actually caps, that a bug is reported as a bug,
# that the published schema is true, and that a reply is hardened.


def test_a_stream_poll_never_runs_on_the_event_loop(tmp_path):
    """Every frame in every stream comes from a blocking SQLite read, and an SSE
    generator runs ON the event loop — so a direct call stalls every other
    connection and every request for the length of that query, once per client
    per poll interval. Nothing about it fails visibly; the server just gets
    slower the more of it you watch.

    api/sse.py `off_loop` is what prevents it, and the property is exactly
    checkable without reaching for a clock: on a worker thread there is no
    running loop at all.
    """
    application = _application()
    where = []

    def watched(read):
        def observe(*arguments):
            try:
                asyncio.get_running_loop()
                where.append("event loop")
            except RuntimeError:
                where.append("worker thread")
            return read(*arguments)
        return observe

    # The three reads the two browser streams poll on.
    application.global_application.snapshot = watched(
        application.global_application.snapshot
    )
    application.dashboard_stream.frame = watched(application.dashboard_stream.frame)
    application.session_application.snapshot = watched(
        application.session_application.snapshot
    )

    server, thread = _server(application)
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    other = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request("GET", "/api/stream")
        connection.getresponse().readline()
        other.request("GET", "/api/sessions/session-one/stream?after_cursor=0")
        other.getresponse().readline()

        assert where, "no poll was observed at all"
        assert set(where) == {"worker thread"}, where
    finally:
        connection.close()
        other.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_a_body_whose_length_is_not_declared_is_refused_before_it_is_read(tmp_path):
    """The cap is checked before the body is parsed, which is what makes it free —
    and which is why the length has to be declared.

    A chunked POST declared nothing, so `int(header or 0)` read it as a zero and
    it passed every cap; the handler behind it then buffered the whole stream.
    h11 imposes no maximum of its own, so the header WAS the limit and an absent
    header was no limit at all.
    """
    server, thread = _server(_application())
    try:
        status = _post_without_a_declared_length(
            server, "/api/terminal/views", b'{"content_reference": "event-one:field"}'
        )
        assert status == 411

        # ...and the same request, with its length declared, is served as always.
        status, body = _post(
            server, "/api/terminal/views", {"content_reference": "event-one:field"}
        )
        assert (status, json.loads(body)) == (200, {"opened": True})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_an_internal_failure_is_a_500_and_an_audit_row_not_a_400(tmp_path, monkeypatch):
    """A handler registered for KeyError, ValueError and TypeError answered every
    one of them with 400 and the exception's own message.

    Those three types are raised all over this tree as invariant checks on code
    we wrote, so a real bug was reported to the browser as the CALLER's mistake:
    no `errors` row, no 500, and whatever the internal message happened to say on
    the wire. Only domain.errors.ApplicationInputError means "your request" now.
    """
    application = _application()

    def explode():
        raise ValueError("/Users/someone/private/notes is not a directory")

    monkeypatch.setattr(application.dashboard_sessions, "sessions", explode)
    server, thread = _server(application)
    try:
        status, headers, body = _get_response(server, "/api/sessions")

        assert status == 500
        assert json.loads(body) == {"error": "internal"}
        assert "private" not in body.decode()
        # Read back through the graph's own audit reader, not a patched
        # module function: the recorder is a node now, and the row it wrote is
        # the thing the errwatch chip and the audit CLI will read.
        assert application.diagnostics.errors_for_session(SessionId("")), (
            "an internal failure must leave an errors row behind"
        )
        # This reply is the one no middleware can wrap (Starlette runs the
        # Exception handler above the whole user stack), so it is hardened at the
        # point it is built instead.
        assert headers["X-Content-Type-Options"] == "nosniff"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_input_the_caller_really_did_get_wrong_is_still_a_400_with_its_reason(tmp_path):
    """The other half of the change above: the sites that meant "bad request" say
    so by type, and keep the 400 and the message the browser already reads."""
    server, thread = _server(_application())
    try:
        # UnknownReference — a session id that names nothing.
        status, _headers, body = _get_response(server, "/api/sessions/nosuchsession")
        assert status == 400
        assert "unknown session" in json.loads(body)["error"]

        # MalformedRequest — a content reference that is not <event>:<field>.
        status, _headers, body = _get_response(server, "/api/content/nocolonhere")
        assert status == 400
        assert json.loads(body)["error"] == "invalid content reference"

        # UnknownReference again, from the registry — and this one used to be a
        # 500, because HarnessRegistryError was a RuntimeError nothing handled.
        status, _headers, body = _get_response(server, "/api/harnesses/mystery/catalog")
        assert status == 400
        assert "mystery" in json.loads(body)["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_a_session_id_that_could_never_be_one_is_refused_at_the_boundary(tmp_path):
    """A path parameter used to be a bare `str`, so anything at all reached the
    store, the harness registry, and — truncated to 200 characters, which is not
    the same thing as validated — the audit rows a stream writes about itself."""
    server, thread = _server(_application())
    try:
        status, _headers, body = _get_response(server, "/api/sessions/not%20a%20session")
        assert status == 400
        assert "session_id" in json.loads(body)["error"]

        status, _headers, _body = _get_response(server, "/api/harnesses/NOT-A-NAME/catalog")
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# The path parameters this fixture can satisfy. A route naming anything else is
# skipped rather than guessed at — better an honest gap than a fabricated id.
_FIXTURE_PATH_PARAMETERS = {"session_id": str(SESSION_ID), "harness": "codex"}


def _api_routes(web):
    """Every APIRoute in the application, flattened.

    `web.routes` is not a flat list in this FastAPI: `include_router` leaves an
    `_IncludedRouter` node that holds the original router rather than splicing its
    routes in. Both shapes are walked, and the test below asserts the walk really
    found the read plane — so a FastAPI that changes this again fails loudly here
    instead of quietly covering nothing.
    """
    pending = list(web.routes)
    while pending:
        route = pending.pop()
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        nested = getattr(nested, "routes", None) or getattr(route, "routes", None)
        if nested:
            pending.extend(nested)


def test_every_declared_response_model_describes_the_bytes_actually_sent(tmp_path):
    """`response_model=` is DOCUMENTATION wherever a route answers with a
    JSONResponse: FastAPI validates nothing it did not serialize itself. That is
    the deliberate deal in api/dashboard/controls.py — the contract becomes
    readable, the wire stays put — and the gap it leaves is drift, where
    `json_ready` renames a field and the published schema keeps describing the
    old one with nothing to notice.

    So: call the read plane and validate each reply against its OWN declared
    model. The route table is read from a second application built for the
    purpose; it is a property of the code, not of the graph.
    """
    application = _application()
    server, thread = _server(application)
    try:
        checked = []
        for route in _api_routes(build_web_application(application.instances)):
            if "GET" not in route.methods:
                continue
            if route.response_model is None:
                continue
            try:
                path = route.path.format(**_FIXTURE_PATH_PARAMETERS)
            except KeyError:
                continue
            status, _headers, body = _get_response(server, path)
            assert status == 200, (path, body)
            TypeAdapter(route.response_model).validate_python(json.loads(body))
            checked.append(route.path)

        # ...and the loop really did cover the read plane, rather than skipping
        # its way to a pass.
        assert "/api/sessions" in checked
        assert "/api/sessions/{session_id}" in checked
        assert "/api/harnesses/{harness}/catalog" in checked
        assert len(checked) >= 6, checked
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_the_published_schema_names_every_status_a_caller_must_handle(tmp_path):
    """/openapi.yaml is a published document — pyyaml is a runtime dependency for
    it alone — and it described a server that only ever answered 200: no error
    body anywhere, no 202 or 409 on the plane that returns them, and a 422 this
    server never sends. A client generated from it was wrong about every failure.
    """
    document = build_web_application(_application().instances).openapi()
    error_body = {"$ref": "#/components/schemas/ErrorResponse"}

    def answers(path: str, method: str):
        return document["paths"][path][method]["responses"]

    def schema(entry):
        return entry["content"]["application/json"]["schema"]

    # Every route carries what the exception handlers can always produce...
    for path, method in (("/api/sessions", "get"),
                         ("/api/content/{content_reference}", "get"),
                         ("/api/terminal/views", "post")):
        assert {"400", "500"} <= set(answers(path, method)), (path, method)
        assert schema(answers(path, method)["400"]) == error_body

    # ...and nothing carries the 422 FastAPI adds by default, because
    # `_validation_error` renders a schema rejection as that same 400.
    assert not [
        (path, method)
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict) and "422" in operation.get("responses", {})
    ]

    # The guard's four refusals, on a route that sits behind it.
    guarded = answers("/api/terminal/views", "post")
    assert {"403", "411", "413", "415"} <= set(guarded)

    # A gesture's real outcomes — all three carrying the gesture's OWN body,
    # because a rejection here is a verdict and not an error.
    gesture = answers("/api/sessions/{session_id}/controls/send-text", "post")
    assert {"200", "202", "409"} <= set(gesture)
    # ...compared on the union itself, since FastAPI titles each status's copy of
    # it after the status.
    outcome = schema(gesture["200"])["anyOf"]
    assert schema(gesture["202"])["anyOf"] == outcome
    assert schema(gesture["409"])["anyOf"] == outcome
    assert schema(gesture["400"]) == error_body

    # A launch never answers 200 at all: 202 is its success.
    launch = answers("/api/sessions", "post")
    assert "200" not in launch
    assert {"202", "409"} <= set(launch)


def test_every_plane_carries_the_security_headers(tmp_path):
    """The dashboard is tunneled to real browsers and holds everything a session
    ever said. There is deliberately no CORS middleware in this tree, which stops
    a hostile page from READING this origin; these stop a string that reached one
    of our own pages from ACTING, and they are the other half.
    """
    server, thread = _server(_application())
    try:
        # The document, a JSON reply, a framework 404 and an event stream: four
        # different producers, one policy. The stream's body is never read.
        for path, read_body in (("/", True),
                                ("/api/sessions", True),
                                ("/api/nothing-is-here", True),
                                ("/api/stream", False)):
            _status, headers, _body = _get_response(server, path, read_body=read_body)
            assert headers["X-Content-Type-Options"] == "nosniff", path
            assert headers["X-Frame-Options"] == "DENY", path
            assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin", path

            policy = headers["Content-Security-Policy"]
            # No injected string runs as script...
            assert "script-src 'self' blob:" in policy, path
            # ...nothing may frame the control plane...
            assert "frame-ancestors 'none'" in policy, path
            # ...and the exfiltration barrier names the ONE third party the page
            # legitimately opens a socket to.
            assert "connect-src 'self' wss://api.deepgram.com" in policy, path

        # A route that sets its own Cache-Control keeps it: the policy only ever
        # fills in a header that is absent.
        _status, headers, _body = _get_response(server, "/static/style.css")
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
