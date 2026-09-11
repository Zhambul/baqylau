# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test foundation panes."""

from __future__ import annotations

from tests import (
    canonical_foundation_components as foundation_components,
    foundation_dependencies,
    foundation_test_events,
    foundation_test_interpreter,
    foundation_test_liveness,
    foundation_test_output,
    foundation_test_reactions,
    foundation_test_sources,
)
from tests.harness_names import CODEX_HARNESS
from tests.interrupt_clock import mark_expired

IGNORED_TRANSLATION = foundation_components.raw_events.TranslationResult(
    (), foundation_dependencies.domain.domain_records.RecordedTranslationDecision.IGNORED_NONSEMANTIC,
)
SESSION_ID_TEXT = "session-one"
LEAD_ACTOR_ID_TEXT = "actor-lead"
SOURCE_FILE_NAME = "fixture.jsonl"
OWN_PROCESS_NAME = foundation_dependencies.standard.Path(
    foundation_dependencies.standard.subprocess.run(
        ["ps", "-o", "comm=", "-p", str(foundation_dependencies.standard.os.getpid())],
        capture_output=True,
        check=False,
        text=True,
    ).stdout.strip(),
).name
PRIMARY_SESSION = foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT)
FIXTURE_PROCESS_ID = 4242
SESSION_WINDOW_ID = "the-session-tab"
OPEN_ACTION = "open"
TOOL_OUTPUT_SOURCE_TYPE = "tool_output"


def test_uncorroborated_interrupt_eventually(
    database_path: str,
    ignored_plugin: foundation_dependencies.engine.harness_contract.HarnessPlugin,
    monkeypatch: foundation_dependencies.standard.pytest.MonkeyPatch,
) -> None:
    """Verify an uncorroborated interrupt eventually clears the busy state.

    The bug this whole mechanism exists for: a harness whose Stop-equivalent
        signal never distinguishes an interrupted turn from a completed one leaves
        a session looking busy forever unless something else settles the turn.
    """
    harnesses = foundation_dependencies.engine.harness_registry.HarnessRegistry()
    harnesses.register(ignored_plugin)
    registry = foundation_dependencies.engine.InterruptRegistry()
    runtime = foundation_test_liveness.build_interpreter(database_path, harnesses, interrupts=registry)
    reactions = foundation_test_liveness.build_reaction_loop(database_path, harnesses, interrupts=registry)
    runtime.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    runtime.interpreter.tick()
    mark_expired(monkeypatch, registry, PRIMARY_SESSION)
    runtime.interpreter.tick()
    foundation_test_reactions.assert_only_aborted_event(runtime)
    reactions.tick()
    assert registry.pending(PRIMARY_SESSION) is None
    runtime.interpreter.tick()
    assert len(runtime.store.page_from(0, 10)) == 1


def test_panes_open_at_window_announcing_delivery(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify panes open at the window the announcing delivery recorded.

    The envelope of the session.started fact carries the window the hook ran
        in. Two loops, in order: the interpreter commits the fact and writes the
        sessions row, and the reaction loop — following the same fact through the
        canonical cursor — anchors the panes to it.
    """
    interpreter, reactions, recorder, terminal = foundation_test_output.pane_react_interpreter(
        tmp_path, window_id=foundation_dependencies.domain.domain_ids.WindowId(SESSION_WINDOW_ID),
    )
    interpreter.tick()
    reactions.tick()
    recorder.record((
        foundation_dependencies.standard.replace(
            foundation_test_events.raw_observation("raw-start-again"), source_position="1",
        ),
    ))
    interpreter.tick()
    reactions.tick()
    assert terminal.calls == [
        ("ownership", SESSION_WINDOW_ID, foundation_dependencies.standard.os.getpid(), OWN_PROCESS_NAME),
        (OPEN_ACTION, foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT), SESSION_WINDOW_ID),
    ]


def test_a_headless_session_gets_no_panes(tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify a headless session gets no panes."""
    interpreter, reactions, _recorder, terminal = foundation_test_output.pane_react_interpreter(tmp_path)
    interpreter.tick()
    reactions.tick()
    assert not terminal.calls


def test_continuation_moves_panes_from_prior() -> None:
    """Verify a continuation moves the panes from the prior session."""
    terminal = foundation_test_interpreter.RecordingTerminal()
    session = foundation_dependencies.standard.replace(
        foundation_test_events.example_session("session-new"),
        terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId(SESSION_WINDOW_ID),
        harness_process_id=FIXTURE_PROCESS_ID,
        plugin=foundation_test_reactions.example_plugin(IGNORED_TRANSLATION),
    )
    reaction = foundation_components.reaction.PaneCanonicalEventReaction(
        terminal, foundation_test_sources.SingleSessionLookup(session), foundation_test_events.PaneWidths(),
    )
    started = foundation_dependencies.standard.replace(
        foundation_test_events.session_started_event(session_id="session-new"),
        payload=foundation_dependencies.standard.replace(
            foundation_test_events.session_started_event().payload,
            continued_from=foundation_dependencies.domain.domain_ids.SessionId("session-old"),
        ),
    )
    reaction.react(started)
    assert terminal.calls == [
        ("close", foundation_dependencies.domain.domain_ids.SessionId("session-old")),
        (OPEN_ACTION, foundation_dependencies.domain.domain_ids.SessionId("session-new"), SESSION_WINDOW_ID),
    ]


def test_confirmed_same_session_resume_tags_its() -> None:
    """Verify a confirmed same session resume tags its starting window."""
    terminal = foundation_test_interpreter.RecordingTerminal()
    session = foundation_dependencies.standard.replace(
        foundation_test_events.example_session(),
        terminal_window_id=foundation_dependencies.domain.domain_ids.WindowId("the-resume-tab"),
        harness_process_id=None,
        plugin=foundation_test_reactions.example_plugin(IGNORED_TRANSLATION),
    )
    reaction = foundation_components.reaction.PaneCanonicalEventReaction(
        terminal, foundation_test_sources.SingleSessionLookup(session), foundation_test_events.PaneWidths(),
    )
    started = foundation_dependencies.standard.replace(
        foundation_test_events.session_started_event(),
        payload=foundation_dependencies.standard.replace(
            foundation_test_events.session_started_event().payload,
            resumed_from=foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT),
        ),
    )
    reaction.react(started)
    assert terminal.calls == [
        (OPEN_ACTION, foundation_dependencies.domain.domain_ids.SessionId(SESSION_ID_TEXT), "the-resume-tab"),
    ]


def test_output_location_directives_run_whole_fg(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """Directive → fact → active row → chunks pulled → operation.finished → drained away."""
    runtime = foundation_test_output.runtime_with_finished_operation(tmp_path)
    output_path = tmp_path / "operation.out"
    output_path.write_bytes(b"hello")
    context = foundation_components.raw_events.RawEventSourceContext(
        session_id=PRIMARY_SESSION,
        lead_actor_id=foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        actor_id=foundation_dependencies.domain.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        parent_actor_id=None,
        source_reference=SOURCE_FILE_NAME,
    )
    located = foundation_dependencies.domain.event_shell.ShellOutputLocated(
        shell_id=foundation_dependencies.domain.domain_ids.ShellId("operation-1"),
        source_path=str(output_path),
        chunk_source_type=TOOL_OUTPUT_SOURCE_TYPE,
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=foundation_dependencies.domain.work_state.ShellFollowUntil.SHELL_FINISHED,
    )
    runtime.recorder.record((
        foundation_dependencies.engine.output_location_raw_event(
            context,
            CODEX_HARNESS,
            located,
            payload=foundation_components.documents.encode_document(located),
        ),
    ))
    runtime.interpreter.tick()
    runtime.interpreter.tick()
    foundation_test_liveness.assert_output_following_started(runtime)
    foundation_test_liveness.finish_output_following(runtime, output_path)


def test_bg_following_survives_operation_finished(
    tmp_path: foundation_dependencies.standard.Path) -> None:
    """Verify a background following survives operation finished until the session ends."""
    following = foundation_test_output.started_background_following(tmp_path)
    assert len(following.storage.shell_output.find_for_session(PRIMARY_SESSION)) == 1
    following.storage.sessions.save(
        CODEX_HARNESS, foundation_test_events.example_session(),
    )
    following.reaction.react(
        foundation_dependencies.standard.replace(
            foundation_test_events.canonical_message(),
            payload=foundation_dependencies.domain.event_session.SessionFinished(
                foundation_dependencies.domain.outcomes.Outcome.SUCCEEDED, None,
            ),
        ),
    )
    assert not following.storage.shell_output.find_for_session(PRIMARY_SESSION)
    assert following.output_path.exists()
    connection = foundation_dependencies.standard.sqlite3.connect(following.database_path)
    assert connection.execute("SELECT count(*) FROM raw_events WHERE source_type='tool_output'").fetchone()[0] == 1
