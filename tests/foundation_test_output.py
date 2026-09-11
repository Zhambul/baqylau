# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test output."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_liveness,
    foundation_test_reactions,
)
from tests.harness_names import CODEX_HARNESS

MAIN_DATABASE_NAME = "main.db"
FIXTURE_EVENT_TIME = 10.0
SESSION_ID_TEXT = "session-one"
LEAD_ACTOR_ID_TEXT = "actor-lead"
BACKGROUND_OPERATION_ID = "operation-bg"


def started_background_following(
    tmp_path: foundation_dependencies.standard.Path,
) -> foundation_test_liveness.BackgroundFollowing:
    """Start following a completed shell's background output.

    Returns:
        The test state with its output file and following reaction.

    """
    database_path = str(tmp_path / MAIN_DATABASE_NAME)
    storage = foundation_test_liveness.interpreter_storage(
        database_path, foundation_dependencies.engine.harness_registry.HarnessRegistry(),
    )
    following = foundation_test_liveness.BackgroundFollowing(
        database_path,
        storage,
        foundation_components.reactions.ShellOutputCanonicalEventReaction(storage.shell_output, storage.recorder),
        tmp_path / "task.output",
    )
    following.output_path.write_bytes(b"background bytes")
    following.reaction.react(
        foundation_dependencies.standard.replace(
            foundation_test_events.canonical_message(),
            payload=foundation_test_reactions.background_output_location(following.output_path),
        ),
    )
    following.reaction.react(
        foundation_dependencies.standard.replace(
            foundation_test_events.canonical_message(),
            payload=foundation_dependencies.domain.event_shell.ShellFinished(
                foundation_dependencies.domain.domain_ids.ShellId(BACKGROUND_OPERATION_ID),
                foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED,
                None,
                None,
            ),
        ),
    )
    return following


def registered_runtime(
    tmp_path: foundation_dependencies.standard.Path,
    translation: foundation_components.raw_events.TranslationResult | foundation_components.raw_events.TranslationError,
    sources: tuple[foundation_dependencies.engine.harness_contract.HarnessRawEventSource, ...] = (),
) -> foundation_test_events.InterpreterRuntime:
    """Build a test interpreter and register its session and plugin.

    Returns:
        The runtime with the supplied translation and sources.

    """
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(foundation_test_reactions.example_plugin(translation, sources))
    runtime = foundation_test_liveness.build_interpreter(str(tmp_path / MAIN_DATABASE_NAME), harnesses)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    return runtime


def pane_react_interpreter(
    tmp_path: foundation_dependencies.standard.Path,
    *,
    window_id: foundation_dependencies.domain.domain_ids.WindowId | None = None,
) -> tuple[
    foundation_components.loop.Interpreter,
    foundation_dependencies.engine.ReactionLoop,
    foundation_dependencies.repository.SqliteRawEventRepository,
    foundation_test_interpreter.RecordingTerminal,
]:
    """Build the interpreter and pane reaction loop for a session start.

    Returns:
        The interpreter, reaction loop, raw event repository, and terminal record.

    """
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (
                    foundation_test_events.session_started_event(
                        terminal_window_id=window_id, harness_process_id=foundation_dependencies.standard.os.getpid(),
                    ),
                ),
                foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
        ),
    )
    terminal = foundation_test_interpreter.RecordingTerminal()
    runtime = foundation_test_liveness.build_interpreter(str(tmp_path / MAIN_DATABASE_NAME), harnesses)
    reactions = foundation_test_liveness.build_reaction_loop(
        str(tmp_path / MAIN_DATABASE_NAME), harnesses, terminal=terminal,
    )
    runtime.recorder.record((foundation_test_events.raw_observation("raw-start"),))
    return (runtime.interpreter, reactions, runtime.recorder, terminal)


def runtime_with_finished_operation(
    tmp_path: foundation_dependencies.standard.Path,
) -> foundation_test_events.InterpreterRuntime:
    """Build a runtime that translates input to a shell finish event.

    Returns:
        The runtime with its test session registered.

    """
    finished_event = foundation_dependencies.domain.event_base.CanonicalEvent(
        foundation_dependencies.domain.domain_ids.CanonicalEventId("operation-finished"),
        foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT),
        foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        None,
        None,
        CODEX_HARNESS,
        FIXTURE_EVENT_TIME,
        None,
        None,
        foundation_dependencies.domain.event_shell.ShellFinished(
            foundation_dependencies.domain.domain_ids.ShellId("operation-1"),
            foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED,
            None,
            None,
        ),
    )
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(
        foundation_test_reactions.example_plugin(
            foundation_components.raw_events.TranslationResult(
                (finished_event,), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
        ),
    )
    runtime = foundation_test_liveness.build_interpreter(str(tmp_path / MAIN_DATABASE_NAME), harnesses)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    return runtime
