"""Real HTTP routing over the canonical application services."""

from __future__ import annotations

import http.client
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from dataclasses import replace
from urllib.parse import quote

from app.bootstrap import build_application
from app.telemetry import BrowserTelemetryService
from contracts.harness import RawEvent, Session, TranslationResult
from dashboard import prefs
from dashboard.application import (
    DashboardNotificationState,
    GlobalApplicationService,
    NewSessionPreferences,
)
from dashboard.http.handler import Handler, Server
from dashboard.http.canonical import _CanonicalMixin
from domain.events import CanonicalEvent, MessageCreated, SessionStarted
from domain.ids import ActorId, CanonicalEventId, MessageId, RawEventId, SessionId
from domain.values import TextContent

SESSION_ID = SessionId("session-one")
ACTOR_ID = ActorId("actor-one")


def test_server_ignores_disconnects_and_delegates_other_thread_errors(monkeypatch):
    server = object.__new__(Server)
    delegated = []
    monkeypatch.setattr(
        ThreadingHTTPServer,
        "handle_error",
        lambda _server, request, client_address: delegated.append(
            (request, client_address)
        ),
    )

    try:
        raise ConnectionResetError("client left")
    except ConnectionResetError:
        server.handle_error("request", "client")
    assert delegated == []

    try:
        raise RuntimeError("unexpected")
    except RuntimeError:
        server.handle_error("request", "client")
    assert delegated == [("request", "client")]


def _event(event_id: str, payload):
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SESSION_ID,
        actor_id=ACTOR_ID,
        turn_id=None,
        parent_actor_id=None,
        harness="codex",
        occurred_at=10.0,
        payload=payload,
    )


def _record(application, raw_event, translator_version, translation):
    application.recorder.record((raw_event,))
    application.canonical_store.store_translation(raw_event, translator_version, translation)


def _application(tmp_path):
    application = build_application(str(tmp_path))
    application.sessions.register(
        "codex",
        Session(SESSION_ID, ACTOR_ID, "native", "fixture", "/work"),
    )
    events = (
        _event("session", SessionStarted("/work", None, None, None, None, None)),
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


def _server(application):
    server = Server(("127.0.0.1", 0), Handler)
    server.canonical_application = application
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server, path: str):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response.status, response.getheader("Content-Type"), body


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
    application = _application(tmp_path)
    with sqlite3.connect(application.diagnostics.database_path) as connection:
        connection.execute(
            "CREATE TABLE errors(id INTEGER PRIMARY KEY, ts REAL, session_id TEXT, "
            "script TEXT, func TEXT, traceback TEXT, context TEXT)"
        )
        connection.execute(
            "INSERT INTO errors VALUES(1, 12.5, ?, 'dashboard', 'render', 'trace', '{}')",
            (str(SESSION_ID),),
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
        assert page_snapshot["application"]["memory"] == {
            "enabled": False,
            "item_count": 0,
        }
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
        assert harnesses["claude_code"]["supports_memory"] is True
        assert harnesses["codex"]["supports_memory"] is False

        status, _content_type, body = _get(
            server, "/api/sessions/session-one/memory"
        )
        assert status == 400
        assert json.loads(body)["error"] == "memory is not enabled for this session"

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
    application = _application(tmp_path)

    def record_message(
        event_id: str,
        actor_id: ActorId,
        parent_actor_id: ActorId | None,
        role: str,
        text: str,
    ) -> None:
        event = CanonicalEvent(
            event_id=CanonicalEventId(event_id),
            session_id=SESSION_ID,
            actor_id=actor_id,
            turn_id=None,
            parent_actor_id=parent_actor_id,
            harness="codex",
            occurred_at=20.0,
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


def test_content_resource_resolves_a_canonical_field(tmp_path):
    server, thread = _server(_application(tmp_path))
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
    server, thread = _server(_application(tmp_path))
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
    server, thread = _server(_application(tmp_path))
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
    server, thread = _server(_application(tmp_path))
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
    server, thread = _server(_application(tmp_path))
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
    application = _application(tmp_path)
    prefs.set_view_mode(str(SESSION_ID), "focus")
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

        prefs.set_view_mode(str(SESSION_ID), "verbose")
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
    application = _application(tmp_path)
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
    application = _application(tmp_path)
    state = DashboardNotificationState()
    application = replace(
        application,
        global_application=GlobalApplicationService(
            application.dashboard_sessions,
            application.usage_state,
            state,
        ),
    )
    server, thread = _server(application)
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
    from dashboard.notify import presence

    presence_calls = []
    monkeypatch.setattr(
        presence,
        "mark_device",
        lambda device_id: presence_calls.append(("device", device_id)),
    )
    monkeypatch.setattr(
        presence,
        "mark_viewing",
        lambda session_id: presence_calls.append(("viewing", session_id)),
    )
    monkeypatch.setattr(
        presence,
        "mark_away",
        lambda device_id, session_id: presence_calls.append(
            ("away", device_id, session_id)
        ),
    )
    application = _application(tmp_path)
    server, thread = _server(application)
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
        assert prefs.push_subscriptions() == [
            {
                "endpoint": "https://push.example/subscription",
                "keys": {"p256dh": "public", "auth": "secret"},
                "device": "browser-one",
                "label": "Tablet",
            }
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
    request = _CanonicalMixin._control_request(
        SESSION_ID,
        {
            "control_name": "send_text",
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
    )

    assert request.session_id == SESSION_ID
    assert request.attachments[0].local_path == "/tmp/image.png"
    assert request.attachments[0].display_name == "image.png"

    attachment_only = _CanonicalMixin._control_request(
        SESSION_ID,
        {
            "control_name": "send_text",
            "request_id": "request-two",
            "text": "",
            "attachments": [
                {"local_path": "/tmp/image.png", "display_name": "image.png"}
            ],
        },
    )
    assert attachment_only.text == ""


def test_invalid_canonical_post_is_a_client_error_not_an_old_route(tmp_path):
    server, thread = _server(_application(tmp_path))
    try:
        status, body = _post(
            server,
            "/api/sessions/session-one/controls",
            {"control_name": "send_text", "request_id": "request-one"},
        )
        assert status == 400
        assert json.loads(body)["error"] == "text must be a string"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_browser_telemetry_uses_named_application_resources(tmp_path):
    class RecordingAudit:
        def __init__(self):
            self.records = []

        def state_file(self, log, path, action, content):
            self.records.append((log, path, action, content))

    audit = RecordingAudit()
    application = replace(
        _application(tmp_path),
        browser_telemetry=BrowserTelemetryService(audit),
    )
    server, thread = _server(application)
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
        assert audit.records[0][3]["session_id"] == "session-one"

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
    from app import services
    from contracts.harness import SelectModel

    rows = []
    monkeypatch.setattr(
        services.audit,
        "state_file",
        lambda log, path, action, content: rows.append((log, action, content)),
    )
    service = object.__new__(services.HarnessControlService)
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
    from contracts.harness import ControlResult

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
    from contracts.harness import ControlResult

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
    from app import services
    from contracts.harness import ControlResult, SelectModel

    def explode(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(services.audit, "state_file", explode)
    service = object.__new__(services.HarnessControlService)
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
    application = _application(tmp_path)
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
    server, thread = _server(_application(tmp_path))
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


def test_pane_stream_resolves_a_pending_identity_server_side(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    from app import pending_session

    pending_id = pending_session.identity(4242)
    pending_session.bind(pending_id, SESSION_ID)
    server, thread = _server(_application(tmp_path))
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        connection.request(
            "GET", f"/api/sessions/{pending_id}/panes/mirror/stream?width=80"
        )
        response = connection.getresponse()
        assert _read_sse_event(response) == ("session", {"session_id": "session-one"})
        event, _frame = _read_sse_event(response)
        assert event == "frame"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pane_stream_rejects_a_missing_width_before_streaming(tmp_path):
    server, thread = _server(_application(tmp_path))
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
    from app.pane_commands import PaneCommandOutcome

    class PaneCommands:
        def __init__(self):
            self.calls = []
            self.outcome = PaneCommandOutcome(True, True)

        def execute(self, command, window_id, working_directory, columns=None, percent=None):
            self.calls.append((command, window_id, working_directory, columns, percent))
            return self.outcome

    pane_commands = PaneCommands()
    application = replace(_application(tmp_path), pane_commands=pane_commands)
    server, thread = _server(application)
    try:
        status, body = _post(
            server,
            "/api/terminal/panes",
            {"command": "setpct", "window_id": "77", "working_directory": "/work", "percent": 40},
        )
        assert status == 200
        assert json.loads(body) == {"handled": True, "succeeded": True, "reason": None}
        assert pane_commands.calls == [("setpct", "77", "/work", None, 40)]

        pane_commands.outcome = PaneCommandOutcome(True, False, "no pane")
        status, body = _post(
            server,
            "/api/terminal/panes",
            {"command": "toggle", "working_directory": "/work"},
        )
        assert status == 409
        assert json.loads(body)["reason"] == "no pane"

        pane_commands.outcome = PaneCommandOutcome(False, True)
        status, body = _post(
            server,
            "/api/terminal/panes",
            {"command": "toggle", "working_directory": "/work"},
        )
        assert status == 200
        assert json.loads(body)["handled"] is False

        status, _body = _post(server, "/api/terminal/panes", {"command": "toggle"})
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_terminal_view_route_owns_the_open_closed_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    from app import terminal_views

    server, thread = _server(_application(tmp_path))
    try:
        status, body = _post(
            server, "/api/terminal/views", {"content_reference": "event-one:field"}
        )
        assert (status, json.loads(body)) == (200, {"opened": True})
        assert terminal_views.opened() == frozenset({"event-one:field"})
        status, body = _post(
            server, "/api/terminal/views", {"content_reference": "event-one:field"}
        )
        assert (status, json.loads(body)) == (200, {"opened": False})
        assert terminal_views.opened() == frozenset()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_mirror_frames_share_one_model_across_client_widths(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    application = _application(tmp_path)
    _record_agent_message(application)

    version, wide = application.pane_streams.mirror_frame(SESSION_ID, 120, None)
    assert "hello from the agent" in wide
    assert application.pane_streams.mirror_frame(SESSION_ID, 120, version) is None

    narrow = application.pane_streams.mirror_frame(SESSION_ID, 40, None)
    assert narrow is not None and narrow[0] == version
    assert "hello from the agent" in narrow[1]
    assert len(application.pane_streams._models) == 1
