# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide foundation test liveness."""

from __future__ import annotations

from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_reactions,
    foundation_test_sources,
    storage_reads,
)

IGNORED_TRANSLATION = foundation_components.raw_events.TranslationResult(
    (),
    foundation_dependencies.domain.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
)
SESSION_ID_TEXT = "session-one"
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
SESSION_DATA_WRITERS = (
    foundation_components.session.SessionWriter(),
    foundation_components.session.GoalWriter(),
    foundation_components.session.TaskWriter(),
    foundation_components.actors.ActorWriter(),
    foundation_components.actors.StatusWriter(),
    foundation_components.actors.UsageWriter(),
    foundation_components.actors.ContextWriter(),
    foundation_components.actors.StatisticsWriter(),
)
FINISH_RAW_EVENT_ID = "raw-finish"
TOOL_OUTPUT_SOURCE_TYPE = "tool_output"


def assert_output_following_started(runtime: foundation_test_events.InterpreterRuntime) -> None:
    """Verify that the output directive started one active following."""
    chunk_types = {
        audit.raw_event.source_type
        for audit in foundation_dependencies.repository.SqliteRawEventAuditRepository(
            runtime.store.sqlite_database,
        ).audits_for_session(foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT))
    }
    assert TOOL_OUTPUT_SOURCE_TYPE in chunk_types
    assert len(runtime.shell_output.find_for_session(PRIMARY_SESSION)) == 1
    committed = storage_reads.page_from(runtime.store, 0, 100)
    committed_types = {type(stored.payload) for stored in committed}
    assert foundation_dependencies.domain.event_shell.ShellOutputLocated in committed_types


def finish_output_following(
    runtime: foundation_test_events.InterpreterRuntime,
    output_path: foundation_dependencies.standard.Path,
) -> None:
    """Finish the shell and verify that the output following drains."""
    runtime.recorder.record((foundation_test_events.raw_observation(FINISH_RAW_EVENT_ID),))
    runtime.interpreter.tick()
    runtime.interpreter.tick()
    assert not runtime.shell_output.find_for_session(PRIMARY_SESSION)
    assert not output_path.exists()
    assert not runtime.recorder.unverdicted(10)


@foundation_dependencies.standard.dataclass
class BackgroundFollowing:
    """Keep the database, reaction, and output path for a following test."""

    database_path: str
    storage: foundation_test_interpreter.InterpreterStorage
    reaction: foundation_components.reactions.ShellOutputCanonicalEventReaction
    output_path: foundation_dependencies.standard.Path


@foundation_dependencies.standard.pytest.fixture
def ignored_plugin() -> foundation_dependencies.engine.harness_contract.HarnessPlugin:
    """Return a fresh plug-in with no canonical output.

    Returns:
        A fresh plug-in with no canonical output.

    """
    return foundation_test_reactions.example_plugin(IGNORED_TRANSLATION)


def interpreter_storage(
    database_path: str | foundation_dependencies.standard.Path,
    harnesses: foundation_dependencies.engine.harness_registry.HarnessRegistry,
) -> foundation_test_interpreter.InterpreterStorage:
    """Build interpreter repositories for one test database.

    Returns:
        The session, raw event, canonical event, and shell output repositories.

    """
    database = foundation_dependencies.repository.main_database(str(database_path))
    return foundation_test_interpreter.InterpreterStorage(
        foundation_dependencies.repository.SqliteSessionRepository(database, harnesses),
        foundation_dependencies.repository.SqliteRawEventRepository(database),
        foundation_dependencies.repository.SqliteCanonicalEventRepository(database),
        foundation_dependencies.repository.SqliteShellOutputRepository(database),
    )


def build_reaction_loop(
    database_path: str | foundation_dependencies.standard.Path,
    harnesses: foundation_dependencies.engine.harness_registry.HarnessRegistry,
    **options: foundation_dependencies.standard.typing.Unpack[foundation_test_interpreter.ReactionLoopOptions],
) -> foundation_dependencies.engine.ReactionLoop:
    """Build reaction loop.

    The other loop, over the same database: what a committed fact CAUSES.

        A separate builder because it is a separate thread in the daemon — the
        interpreter appends facts and this follows them, and a test that wants both
        ticks both, in that order.

    Returns:
        Reaction loop.

    """
    database = foundation_dependencies.repository.main_database(str(database_path))
    sessions = foundation_dependencies.repository.SqliteSessionRepository(database, harnesses)
    return foundation_dependencies.engine.ReactionLoop(
        foundation_dependencies.engine.ReactionLoopDependencies(
            canonical_fact_reader=SqliteInterpretationRepository(database),
            session_data_repository=foundation_dependencies.repository.SqliteSessionDataRepository(database),
            reactions=(
                foundation_components.reaction.PaneCanonicalEventReaction(
                    options.get("terminal") or foundation_test_sources.NullTerminal(),
                    sessions,
                    foundation_test_events.PaneWidths(),
                ),
                foundation_components.reactions.InterruptCanonicalEventReaction(
                    options.get("interrupts") or foundation_dependencies.engine.InterruptRegistry(),
                ),
            ),
            session_entry_writer=foundation_components.entries.EntryWriter(),
            writers=SESSION_DATA_WRITERS,
            listeners=(),
            harness_registry=harnesses,
            harness_reactor_context=options.get("controls")
            or foundation_dependencies.standard.typing.cast(
                "foundation_dependencies.engine.harness_contract.HarnessReactorContext",
                foundation_test_sources.NullControls(),
            ),
            audit_recorder=options.get("audit") or foundation_test_events.RecordingAudit(),
        ),
    )


def build_interpreter(
    database_path: str | foundation_dependencies.standard.Path,
    harnesses: foundation_dependencies.engine.harness_registry.HarnessRegistry,
    *,
    audit: foundation_dependencies.audit.AuditRecorder | None = None,
    interrupts: foundation_dependencies.engine.InterruptRegistry | None = None,
) -> foundation_test_events.InterpreterRuntime:
    """Build interpreter.

    The bootstrap wiring, with an injectable terminal for the pane reaction.

    Returns:
        Interpreter.

    """
    storage = interpreter_storage(database_path, harnesses)
    inputs = (
        foundation_components.reactions.SessionUpsertCanonicalEventReaction(storage.sessions),
        foundation_components.reactions.ShellOutputCanonicalEventReaction(storage.shell_output, storage.recorder),
    )
    core_translators = {
        foundation_components.raw_events.OUTPUT_LOCATION_SOURCE_TYPE: (
            foundation_components.translators.ShellOutputTranslator()
        ),
        foundation_components.raw_events.LIVENESS_SOURCE_TYPE: foundation_components.translators.LivenessTranslator(),
        foundation_components.raw_events.INTERRUPT_SOURCE_TYPE: foundation_components.translators.InterruptTranslator(),
    }
    interpreter = foundation_components.loop.Interpreter(
        foundation_components.loop.InterpreterDependencies(
            repositories=foundation_components.loop.InterpreterRepositories(
                sessions=storage.sessions,
                raw_events=storage.recorder,
                shell_output=storage.shell_output,
                canonical_events=storage.store,
            ),
            services=foundation_components.loop.InterpreterServices(
                harnesses=harnesses,
                core_translators=core_translators,
                inputs=inputs,
                audit=foundation_test_events.RecordingAudit() if audit is None else audit,
                interrupts=foundation_dependencies.engine.InterruptRegistry() if interrupts is None else interrupts,
            ),
        ),
    )
    return foundation_test_events.InterpreterRuntime(
        interpreter,
        storage.sessions,
        storage.recorder,
        storage.store,
        storage.shell_output,
    )
