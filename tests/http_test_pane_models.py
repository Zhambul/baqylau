# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide http test pane models."""

from __future__ import annotations

from harness.hooks.recorder import OrderedHookRecorder
from tests import (
    http_contract_dependencies as contract_dependencies,
    http_library_dependencies as library_dependencies,
    http_runtime_dependencies as runtime_dependencies,
    http_value_dependencies as standard_dependencies,
)

SESSION_ID_TEXT = "session-one"
CODEX_HARNESS_TEXT = "codex"
SESSION_ID = runtime_dependencies.domain_ids.SessionId(SESSION_ID_TEXT)
ACTOR_ID = runtime_dependencies.domain_ids.ActorId("actor-one")
FIXTURE_EVENT_TIME = 10.0
type JsonValue = bool | float | int | str | list[JsonValue] | dict[str, JsonValue] | None


def observe_read[**CallParameters, Result](
    read: standard_dependencies.collections_abc.Callable[CallParameters, Result],
    locations: list[str],
    *arguments: CallParameters.args,
    **keywords: CallParameters.kwargs,
) -> Result:
    """Record the execution location before calling a repository reader.

    Returns:
        The result from the supplied reader.

    """
    try:
        standard_dependencies.asyncio.get_running_loop()
    except RuntimeError:
        locations.append("worker thread")
    else:
        locations.append("event loop")
    return read(*arguments, **keywords)


def explode_running_sessions() -> library_dependencies.typing.Never:
    """Simulate a repository failure with a private path in its message.

    Raises:
        ValueError: For every call.

    """
    message = "/Users/someone/private/notes is not a directory"
    raise ValueError(message)


def declares_unexpected_validation_error(operation: JsonValue) -> bool:
    """Check whether a schema operation declares HTTP 422.

    Returns:
        True if the operation contains that response code.

    """
    if not isinstance(operation, dict):
        return False
    responses = operation.get("responses")
    return isinstance(responses, dict) and "422" in responses


def event(
    event_id: str, payload: runtime_dependencies.event_base.EventPayload,
) -> runtime_dependencies.event_base.CanonicalEvent[runtime_dependencies.event_base.EventPayload]:
    """Build a canonical event for the fixed test session and actor.

    Returns:
        The event with the supplied identifier and payload.

    """
    return runtime_dependencies.event_base.CanonicalEvent(
        event_id=runtime_dependencies.domain_ids.CanonicalEventId(event_id),
        session_id=SESSION_ID,
        actor_id=ACTOR_ID,
        turn_id=None,
        parent_actor_id=None,
        harness=runtime_dependencies.domain_ids.HarnessName(CODEX_HARNESS_TEXT),
        occurred_at=FIXTURE_EVENT_TIME,
        terminal_window_id=None,
        harness_process_id=None,
        payload=payload,
    )


def record(
    application: contract_dependencies.canonical_runtime.ProviderGraph,
    raw_event: runtime_dependencies.raw_event_models.RawEvent,
    translator_version: str,
    translation: runtime_dependencies.raw_event_models.TranslationResult,
) -> None:
    """Store a raw event and its canonical translation in the test repositories."""
    application.raw_events.record((raw_event,))
    application.canonical_events.record_translation(raw_event, translator_version, translation, FIXTURE_EVENT_TIME)


def raw_event_audits(
    application: contract_dependencies.canonical_runtime.ProviderGraph,
) -> contract_dependencies.SqliteRawEventAuditRepository:
    """Build the raw event audit reader for the test application.

    A hook's raw events are written after its reply, so the reader waits for them.

    Returns:
        The audit repository using the application's main database.

    """
    application.provider("hook_recorder", OrderedHookRecorder).flush()
    return contract_dependencies.SqliteRawEventAuditRepository(application.main_db)


class RunningDaemon:
    """Represent running daemon.

    The daemon's real engine (api.server.build_server) on an ephemeral
        port, with the shutdown verbs the tests always used.
    """

    def __init__(
        self, server: library_dependencies.uvicorn.Server, bound_socket: standard_dependencies.socket.socket,
    ) -> None:
        """Store the running server and its bound port."""
        self.server = server
        self.bound_socket = bound_socket
        self.server_port = bound_socket.getsockname()[1]

    def shutdown(self) -> None:
        """Request immediate server shutdown."""
        self.server.force_exit = True
        self.server.should_exit = True

    def server_close(self) -> None:
        """Leave socket closure to the running server."""
