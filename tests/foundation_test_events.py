# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test events."""

from __future__ import annotations

from tests import canonical_foundation_components as foundation_components, foundation_dependencies
from tests.harness_names import CODEX_HARNESS

FIXTURE_SOURCE_IDENTITY = "fixture:source"
EXAMPLE_HARNESS = CODEX_HARNESS
FIXTURE_EVENT_TIME = 10.0
SESSION_ID_TEXT = "session-one"
LEAD_ACTOR_ID_TEXT = "actor-lead"
PRIMARY_LEAD_ACTOR = foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT)
SOURCE_FILE_NAME = "fixture.jsonl"
WORKING_DIRECTORY = "/work"
FIXTURE_OBSERVATION_TIME = 11.0
DEFAULT_PANE_WIDTH_PERCENT = 25
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)


def example_session(session_id: str = SESSION_ID_TEXT) -> foundation_dependencies.engine.Session:
    """Create a test session owned by the current process.

    Returns:
        The session with the supplied identifier and fixed source path.

    """
    return foundation_dependencies.engine.Session(
        session_id=foundation_dependencies.domain.domain_ids.SessionId(session_id),
        lead_actor_id=PRIMARY_LEAD_ACTOR,
        source_reference=SOURCE_FILE_NAME,
        working_directory=WORKING_DIRECTORY,
        harness_process_id=foundation_dependencies.standard.os.getpid(),
    )


def canonical_message(
    *,
    event_id: str = "event-message",
    session_id: str = SESSION_ID_TEXT,
    actor_id: str = LEAD_ACTOR_ID_TEXT,
    harness: foundation_dependencies.domain.domain_ids.HarnessName = EXAMPLE_HARNESS,
    text: str = "hello",
) -> foundation_dependencies.domain.event_base.CanonicalEvent:
    """Create a canonical user prompt event.

    Returns:
        The event with the supplied identifiers, harness, and text.

    """
    return foundation_dependencies.domain.event_base.CanonicalEvent(
        event_id=foundation_dependencies.domain.domain_ids.CanonicalEventId(event_id),
        session_id=foundation_dependencies.domain.domain_ids.SessionId(session_id),
        actor_id=foundation_dependencies.domain.domain_ids.ActorId(actor_id),
        turn_id=None,
        parent_actor_id=None,
        harness=harness,
        occurred_at=FIXTURE_EVENT_TIME,
        terminal_window_id=None,
        harness_process_id=None,
        payload=foundation_dependencies.domain.event_conversation.MessageCreated(
            message_id=foundation_dependencies.domain.domain_ids.MessageId("message-one"),
            role=foundation_dependencies.domain.messaging.MessageRole.USER,
            content=foundation_dependencies.domain.domain_content.TextContent(text),
            phase=foundation_dependencies.domain.messaging.MessagePhase.PROMPT,
            reply_to=None,
        ),
    )


def session_started_event(
    *,
    session_id: str = SESSION_ID_TEXT,
    terminal_window_id: foundation_dependencies.domain.domain_ids.WindowId | None = None,
    harness_process_id: int | None = None,
) -> foundation_dependencies.domain.event_base.CanonicalEvent:
    """Create a session start event for the test lead actor.

    Returns:
        The event with the supplied session, window, and process identifiers.

    """
    return foundation_dependencies.domain.event_base.CanonicalEvent(
        foundation_dependencies.domain.domain_ids.CanonicalEventId(f"session-started:{session_id}"),
        foundation_dependencies.domain.domain_ids.SessionId(session_id),
        PRIMARY_LEAD_ACTOR,
        None,
        None,
        CODEX_HARNESS,
        FIXTURE_EVENT_TIME,
        terminal_window_id,
        harness_process_id,
        foundation_dependencies.domain.event_session.SessionStarted(
            WORKING_DIRECTORY, SOURCE_FILE_NAME, None, None, None, None, None,
        ),
    )


def raw_observation(
    raw_event_id: str,
    *,
    harness: foundation_dependencies.domain.domain_ids.HarnessName = EXAMPLE_HARNESS,
    payload: bytes = b'{"kind":"message"}',
) -> foundation_components.raw_events.RawEvent:
    """Create a raw event from a fixed JSONL source.

    Returns:
        The observation with the supplied identifier, harness, and payload.

    """
    return foundation_components.raw_events.RawEvent(
        raw_event_id=foundation_dependencies.domain.domain_ids.RawEventId(raw_event_id),
        harness=harness,
        source_type="pulled",
        source_name=SOURCE_FILE_NAME,
        source_position="0",
        session_id=PRIMARY_SESSION,
        actor_id=PRIMARY_LEAD_ACTOR,
        parent_actor_id=None,
        observed_at=FIXTURE_OBSERVATION_TIME,
        encoding="jsonl",
        payload=payload,
        source_identity=FIXTURE_SOURCE_IDENTITY,
    )


class PaneWidths:
    """The pane reaction asks one question; a fixture need not store an answer."""

    def __init__(self) -> None:
        """Initialize the width policy."""
        self.working_directories: list[str] = []

    def width_percent(self, working_directory: str) -> int:
        """Record the directory and return the test pane width.

        Returns:
            The default test width percentage.

        """
        self.working_directories.append(working_directory)
        return DEFAULT_PANE_WIDTH_PERCENT


class RecordingAudit(foundation_dependencies.audit.AuditRecorder):
    """Represent recording audit.

    The audit recorder, capturing instead of writing. The interpreter takes
        one by constructor, so a test that asserts on a swallowed failure holds the
        object it was handed rather than patching a module function.
    """

    def __init__(self) -> None:
        """Create empty audit records."""
        self.errors: list[tuple[str, foundation_dependencies.audit.AuditContent]] = []
        self.error_sources: list[str] = []

    def error(
        self, session_or_log: str = "", func: str = "", context: foundation_dependencies.audit.AuditContent = None,
    ) -> None:
        """Process error."""
        self.error_sources.append(session_or_log)
        self.errors.append((func, context))

    def failures(self) -> list[str]:
        """Read the recorded interpreter failure locations.

        Returns:
            The locations without the interpreter prefix and closing bracket.

        """
        return [func.removeprefix("interpreter (").removesuffix(")") for func, _context in self.errors]


@foundation_dependencies.standard.dataclass
class InterpreterRuntime:
    """Hold the interpreter and its storage services for one test runtime."""

    interpreter: foundation_components.loop.Interpreter
    sessions: foundation_dependencies.repository.SqliteSessionRepository
    recorder: foundation_dependencies.repository.SqliteRawEventRepository
    store: foundation_dependencies.repository.SqliteCanonicalEventRepository
    shell_output: foundation_dependencies.repository.SqliteShellOutputRepository
