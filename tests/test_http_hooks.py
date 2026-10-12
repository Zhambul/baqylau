# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test http hooks."""

from __future__ import annotations

from tests import (
    http_library_dependencies as library_dependencies,
    http_runtime_dependencies as runtime_dependencies,
    http_value_dependencies as standard_dependencies,
)

# Keep dependencies and server setup separate from request helpers.
# isort: split

from tests import (
    http_test_assets,
    http_test_controls,
    http_test_pane_models,
    http_test_preferences,
    http_test_response_helpers,
    http_test_server_runtime,
)

LOOPBACK_ADDRESS = "127.0.0.1"
HTTP_GET_METHOD = "GET"
SESSION_ID_FIELD = "session_id"
ERROR_FIELD = "error"
REQUEST_ID_FIELD = "request_id"
HOOK_SESSION_ID = "hook-session"
CLAUDE_HARNESS_TEXT = "claude_code"
DISTINCT_HOOK_EVENT_COUNT = 2


def test_bg_gesture_is_control_and_declines_when() -> None:
    """The gesture that replaced the key passthrough, and why it is a CONTROL.

    Backgrounding is a keystroke the TUI only accepts once it offers to take one,
    so the handler waits for that offer and reports honestly when it never comes.
    That waiting is the harness's knowledge of its own screen, which is why this
    is a gesture with a handler rather than a raw key a caller aims at a window:
    every caller — a browser click and a test alike — gets the same reliability.
    """
    background_control_application = http_test_server_runtime.application()
    with http_test_assets.running_server(background_control_application) as server:
        status, body = http_test_controls.post(
            server, "/api/sessions/session-one/controls/background", {REQUEST_ID_FIELD: "req-1"},
        )
        assert status == library_dependencies.http.client.CONFLICT, body
        assert body.json["status"] == "rejected"
        status, _ = http_test_controls.post(server, "/api/sessions/session-one/controls/background", {})
        assert status == library_dependencies.http.client.BAD_REQUEST, "a gesture with no request id is not addressable"


def test_hook_delivery_records_exact_evidence(tmp_path: library_dependencies.Path) -> None:
    """Verify hook delivery records exact evidence and returns the reply."""
    application = http_test_server_runtime.application()
    payload = http_test_response_helpers.pre_tool_hook_payload(tmp_path)
    with http_test_assets.running_server(application) as server:
        http_test_controls.assert_successful_hook(server, payload)
        audits = http_test_pane_models.raw_event_audits(application).audits_for_session(
            runtime_dependencies.domain_ids.SessionId(HOOK_SESSION_ID),
        )
        assert [audit.raw_event.source_type for audit in audits] == ["hook", "output_location"]
        assert audits[0].raw_event.payload == payload


def test_hook_delivery_ignores_legacy_claude(
    tmp_path: library_dependencies.Path, monkeypatch: library_dependencies.pytest.MonkeyPatch,
) -> None:
    """Verify hook delivery ignores legacy claude account headers."""
    monkeypatch.setenv("CLAUDE_SUBSCRIPTION_SLUG", "daemon-account")
    application = http_test_server_runtime.application()
    payload = standard_dependencies.json.dumps({
        SESSION_ID_FIELD: HOOK_SESSION_ID,
        "transcript_path": str(tmp_path / "hook-session.jsonl"),
        "hook_event_name": "SessionStart",
        "hook_event_id": "start-one",
    }).encode()
    with http_test_assets.running_server(application) as server:
        status = http_test_preferences.post_hook(
            server, CLAUDE_HARNESS_TEXT, payload, {"X-Baqylau-Account-Id": "legacy-account"},
        )[0]
        assert status == library_dependencies.http.client.OK
        hook_row = http_test_pane_models.raw_event_audits(application).audits_for_session(
            runtime_dependencies.domain_ids.SessionId(HOOK_SESSION_ID),
        )[0]
        assert hook_row.raw_event.account_id is None


def test_hook_delivery_rejections_leave_no() -> None:
    """Verify hook delivery rejections leave no evidence."""
    application = http_test_server_runtime.application()
    with http_test_assets.running_server(application) as server:
        status, body = http_test_preferences.post_hook(server, "mystery", b"{}")
        assert status == library_dependencies.http.client.NOT_FOUND
        status, body = http_test_preferences.post_hook(server, CLAUDE_HARNESS_TEXT, b"not json")
        assert status == library_dependencies.http.client.BAD_REQUEST
        assert ERROR_FIELD in standard_dependencies.json.loads(body)
        missing_path_payload = standard_dependencies.json.dumps({SESSION_ID_FIELD: HOOK_SESSION_ID}).encode()
        status, _ = http_test_preferences.post_hook(server, CLAUDE_HARNESS_TEXT, missing_path_payload)
        assert status == library_dependencies.http.client.BAD_REQUEST
        assert not http_test_pane_models.raw_event_audits(application).audits_for_session(
            runtime_dependencies.domain_ids.SessionId(HOOK_SESSION_ID),
        )


def test_hook_identity_reuse_with_different_bytes(tmp_path: library_dependencies.Path) -> None:
    """Verify hook identity reuse with different bytes preserves both observations."""
    application = http_test_server_runtime.application()
    with http_test_assets.running_server(application) as server:
        first, changed = http_test_controls.post_distinct_hook_events(server, tmp_path)
        hooks = http_test_preferences.recorded_hook_events(application)
        assert len(hooks) == DISTINCT_HOOK_EVENT_COUNT
        assert {raw_event.payload for raw_event in hooks} == {first, changed}


def test_stream_poll_never_runs_on_event_loop(monkeypatch: library_dependencies.pytest.MonkeyPatch) -> None:
    """Verify a stream poll never runs on the event loop.

    Every frame in every stream comes from a blocking SQLite read, and an SSE
        generator runs ON the event loop — so a direct call stalls every other
        connection and every request for the length of that query, once per client
        per poll interval. Nothing about it fails visibly; the server just gets
        slower the more of it you watch.

        api/sse.py `off_loop` is what prevents it, and the property is exactly
        checkable without reaching for a clock: on a worker thread there is no
        running loop at all.
    """
    application = http_test_server_runtime.application()
    where: list[str] = []
    monkeypatch.setattr(
        application.session_data, "delta", http_test_preferences.watched_read(application.session_data.delta, where),
    )
    monkeypatch.setattr(
        application.session_data,
        "changed_after",
        http_test_preferences.watched_read(application.session_data.changed_after, where),
    )
    with (
        http_test_assets.running_server(application) as server, standard_dependencies.contextlib.closing(
            library_dependencies.http.client.HTTPConnection(LOOPBACK_ADDRESS, server.server_port, timeout=2),
        ) as connection,
        standard_dependencies.contextlib.closing(
            library_dependencies.http.client.HTTPConnection(LOOPBACK_ADDRESS, server.server_port, timeout=2),
        ) as other,
    ):
        connection.request(HTTP_GET_METHOD, "/sessionData/stream")
        connection.getresponse().readline()
        other.request(HTTP_GET_METHOD, "/sessionData/session-one/stream?after_cursor=0")
        other.getresponse().readline()
        assert where, "no poll was observed at all"
        assert set(where) == {"worker thread"}, where


def test_internal_failure_is_audited(monkeypatch: library_dependencies.pytest.MonkeyPatch) -> None:
    """Verify an internal failure is a 500 and an audit row not a 400.

    A handler registered for KeyError, ValueError and TypeError answered every
        one of them with 400 and the exception's own message.

        Those three types are raised all over this tree as invariant checks on code
        we wrote, so a real bug was reported to the browser as the CALLER's mistake:
        no `errors` row, no 500, and whatever the internal message happened to say on
        the wire. Only domain.errors.ApplicationInputError means "your request" now.
    """
    application = http_test_server_runtime.application()
    monkeypatch.setattr(application.session_data, "running", http_test_pane_models.explode_running_sessions)
    with http_test_assets.running_server(application) as server:
        status, headers, body = http_test_controls.get_response(server, "/sessionData")
        assert status == library_dependencies.http.client.INTERNAL_SERVER_ERROR
        assert body.json == {ERROR_FIELD: "internal"}
        assert "private" not in body.decode()
        assert any(
            "ValueError" in error.traceback
            for error in application.audit_reads.errors_for_session(runtime_dependencies.domain_ids.SessionId(""))
        ), "an internal failure must leave its traceback in an errors row"
        assert headers["X-Content-Type-Options"] == "nosniff"
