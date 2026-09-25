# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test reactions."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_primitives,
    storage_reads,
)
from tests.harness_names import CODEX_HARNESS

HARNESS_VERSION = "1.0"
OWN_PROCESS_NAME = foundation_dependencies.standard.Path(
    foundation_dependencies.standard.subprocess.run(
        ["ps", "-o", "comm=", "-p", str(foundation_dependencies.standard.os.getpid())],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip(),
).name
FIRST_RAW_EVENT_ID = "raw-one"
TOOL_OUTPUT_SOURCE_TYPE = "tool_output"
BACKGROUND_OPERATION_ID = "operation-bg"


def background_output_location(
    output_path: foundation_dependencies.standard.Path,
) -> foundation_dependencies.domain.event_shell.ShellOutputLocated:
    """Create a test directive to follow output until the session finishes.

    Returns:
        The directive for the supplied output path without source deletion.

    """
    return foundation_dependencies.domain.event_shell.ShellOutputLocated(
        shell_id=foundation_dependencies.domain.domain_ids.ShellId(BACKGROUND_OPERATION_ID),
        source_path=str(output_path),
        chunk_source_type=TOOL_OUTPUT_SOURCE_TYPE,
        delete_source=False,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=foundation_dependencies.domain.work_state.ShellFollowUntil.SESSION_FINISHED,
    )


@foundation_dependencies.standard.pytest.fixture
def database(database_path: str) -> foundation_dependencies.repository.SqliteDatabase:
    """Return one initialized main database for a test.

    Returns:
        One initialized main database for a test.

    """
    return foundation_dependencies.repository.main_database(database_path)


def finished_liveness_event(
    plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
    window_id: str,
    process_id: int,
) -> foundation_dependencies.domain.event_base.CanonicalEvent[foundation_dependencies.domain.event_base.EventPayload]:
    """Translate the first process liveness observation for a test session.

    Returns:
        The first canonical event from that observation.

    """
    session = foundation_dependencies.standard.replace(
        foundation_test_events.example_session(),
        plugin=plugin,
        terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId(window_id),
        harness_process_id=process_id,
    )
    raw_event = foundation_components.liveness.SessionLivenessSource(
        session,
        foundation_components.liveness.ProcessProbe(),
    ).read(None)[0]
    return foundation_components.translators.LivenessTranslator().translate(raw_event).canonical_events[0]


def example_plugin(
    translation: foundation_components.raw_events.TranslationResult | foundation_components.raw_events.TranslationError,
    sources: tuple[foundation_dependencies.engine.harness_contract.HarnessRawEventSource, ...] = (),
    name: foundation_dependencies.domain.domain_ids.HarnessName = (
        CODEX_HARNESS
    ),
) -> foundation_dependencies.engine.harness_contract.HarnessPlugin:
    """Create a test plugin with fixed translation and source services.

    Returns:
        The plugin with the supplied harness name.

    """
    return foundation_dependencies.engine.harness_contract.HarnessPlugin(
        harness_info=foundation_dependencies.engine.HarnessInfo(
            name,
            str(name).title(),
            HARNESS_VERSION,
            foundation_dependencies.domain.domain_events.SCHEMA_VERSION,
            OWN_PROCESS_NAME,
        ),
        sources=foundation_test_primitives.FixedSources(sources),
        translator=foundation_test_primitives.FixedTranslator(translation),
    )


def first_raw_observation() -> foundation_components.raw_events.RawEvent:
    """Return a new observation with the first stable fixture id.

    Returns:
        A new observation with the first stable fixture id.

    """
    return foundation_test_events.raw_observation(FIRST_RAW_EVENT_ID)


def save_example_sessions(sessions: foundation_dependencies.repository.SqliteSessionRepository, count: int) -> None:
    """Save the requested number of test sessions with distinct identifiers."""
    for session_number in range(count):
        sessions.save(
            CODEX_HARNESS,
            foundation_test_events.example_session(f"session-{session_number}"),
        )


def assert_only_aborted_event(runtime: foundation_test_events.InterpreterRuntime) -> None:
    """Check that the first event page contains only one turn abort."""
    events = storage_reads.page_from(runtime.store, 0, 10)
    assert [type(stored_event.payload) for stored_event in events] == [
        foundation_dependencies.domain.event_conversation.TurnAborted,
    ]
