# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide http test server runtime."""

from __future__ import annotations

from tests import (
    http_application_dependencies as application_dependencies,
    http_contract_dependencies as contract_dependencies,
    http_library_dependencies as library_dependencies,
    http_runtime_dependencies as runtime_dependencies,
    http_test_audit_models,
    http_test_control_models,
    http_test_pane_models,
    http_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION_ID_TEXT = "session-one"
FIXTURE_SOURCE = "fixture"
WORKING_DIRECTORY = "/work"
SESSION_ID = runtime_dependencies.domain_ids.SessionId(SESSION_ID_TEXT)
ACTOR_ID = runtime_dependencies.domain_ids.ActorId("actor-one")
REPOSITORY_ROOT = str(library_dependencies.Path(__file__).resolve().parents[1])
SERVER_START_TIMEOUT_SECONDS = 5.0
SERVER_POLL_SECONDS = 0.01
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None


def expected_build_references() -> set[str]:
    """Read the frontend entry files from the build manifest.

    Returns:
        The JavaScript entry path and its CSS paths.

    """
    manifest_path = library_dependencies.Path(REPOSITORY_ROOT) / "dashboard/static/build/.vite/manifest.json"
    manifest = standard_dependencies.json.loads(manifest_path.read_text(encoding="utf-8"))["src/main.ts"]
    return {manifest["file"], *manifest.get("css", [])}


class RecordingPresence(contract_dependencies.Presence):
    """Record presence changes without updating live presence state."""

    def __init__(self, calls: list[http_test_audit_models.PresenceCall]) -> None:
        """Store the shared presence call record."""
        self._calls = calls

    def mark_device(self, device: str) -> None:
        """Record a device presence update."""
        self._calls.append(http_test_audit_models.PresenceCall("device", device=device))

    def mark_viewing(self, session_id: runtime_dependencies.domain_ids.SessionId) -> None:
        """Record a session viewing update."""
        self._calls.append(http_test_audit_models.PresenceCall("viewing", session_id=session_id))

    def mark_away(self, device: str, session_id: runtime_dependencies.domain_ids.SessionId | None = None) -> None:
        """Record a device leaving a session."""
        self._calls.append(http_test_audit_models.PresenceCall("away", device, session_id))


def response_answers(
    paths: dict[str, JsonValue],
    path: str,
    method: str,
) -> dict[str, JsonValue]:
    """Read the response definitions for one route and method.

    Returns:
        The response definitions from the OpenAPI document.

    """
    path_item = http_test_control_models.json_object(paths[path])
    operation = http_test_control_models.json_object(path_item[method])
    return http_test_control_models.json_object(operation["responses"])


def response_schema(entry: JsonValue) -> JsonValue:
    """Read the JSON body schema from a response definition.

    Returns:
        The schema under the application/json media type.

    """
    content = http_test_control_models.json_object(http_test_control_models.json_object(entry)["content"])
    media = http_test_control_models.json_object(content["application/json"])
    return media["schema"]


def application() -> contract_dependencies.canonical_runtime.ProviderGraph:
    """Build the test provider graph and store a session with a message.

    Returns:
        The graph using the databases configured by the test environment.

    """
    application = contract_dependencies.canonical_runtime.ProviderGraph()
    application.sessions.save(
        CODEX_HARNESS,
        contract_dependencies.Session(SESSION_ID, ACTOR_ID, FIXTURE_SOURCE, WORKING_DIRECTORY),
    )
    events = (
        http_test_pane_models.event(
            "session",
            runtime_dependencies.event_session.SessionStarted(
                WORKING_DIRECTORY, "fixture.jsonl", None, None, None, None, None,
            ),
        ),
        http_test_pane_models.event(
            "message",
            runtime_dependencies.event_conversation.MessageCreated(
                runtime_dependencies.domain_ids.MessageId("message-one"),
                runtime_dependencies.messaging.MessageRole.ASSISTANT,
                runtime_dependencies.domain_content.TextContent("hello"),
                runtime_dependencies.messaging.MessagePhase.END_TURN,
                None,
            ),
        ),
    )
    for index, event in enumerate(events):
        http_test_pane_models.record(
            application,
            runtime_dependencies.raw_event_models.RawEvent(
                runtime_dependencies.domain_ids.RawEventId(f"raw-{index}"),
                CODEX_HARNESS,
                FIXTURE_SOURCE,
                FIXTURE_SOURCE,
                str(index),
                SESSION_ID,
                ACTOR_ID,
                None,
                100.0 + index,
                "json",
                b"{}",
            ),
            "1",
            runtime_dependencies.raw_event_models.TranslationResult(
                (event,), runtime_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
        )
    application.reaction_loop.tick()
    return application


def fixed[FixedValue](fixed_value: FixedValue) -> standard_dependencies.collections_abc.Callable[[], FixedValue]:
    """Build a fixed-value provider without query parameters.

    Returns:
        A callable with no arguments that returns the supplied value.

    """
    return lambda: fixed_value


def start_server(
    web_application: library_dependencies.fastapi.FastAPI, bound_socket: standard_dependencies.socket.socket,
) -> tuple[http_test_pane_models.RunningDaemon, standard_dependencies.threading.Thread]:
    """Start the test server on its bound socket and wait for startup.

    Returns:
        The server control object and its running thread.

    """
    server = application_dependencies.build_server(web_application)
    thread_options = {"sockets": [bound_socket]}
    thread = standard_dependencies.threading.Thread(target=server.run, kwargs=thread_options, daemon=True)
    thread.start()
    deadline = standard_dependencies.time.monotonic() + SERVER_START_TIMEOUT_SECONDS
    while not server.started:
        assert standard_dependencies.time.monotonic() < deadline, "server did not start"
        standard_dependencies.time.sleep(SERVER_POLL_SECONDS)
    return (http_test_pane_models.RunningDaemon(server, bound_socket), thread)
