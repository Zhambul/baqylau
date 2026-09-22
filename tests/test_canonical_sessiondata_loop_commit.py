# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata loop commit."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests import (
    canonical_sessiondata_components as sessiondata_components,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_loop_models as loop_models,
    canonical_sessiondata_loop_support as loop_support,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain

if TYPE_CHECKING:
    from pathlib import Path

STARTUP_EVENT_COUNT = 2
STARTUP_AND_ACTION_EVENT_COUNT = 3


def test_loop_follows_cursor_and_stops_where_it(tmp_path: Path) -> None:
    """Verify the loop follows the cursor and stops where it left off."""
    loop, read_model, audit = loop_support.loop_over(tmp_path, session_fixtures.alive())
    assert read_model.progress() == 0

    completed = loop.tick()
    assert (completed, audit.failures, read_model.progress()) == (2, [], 2)
    # Nothing new: nothing done, and nothing re-done.
    session_state = session_fixtures.required_data(read_model).session.state
    assert (loop.tick(), session_state) == (0, session_values.RUNNING_STATE)


def test_one_event_commits_its_entry_and_its_rows(tmp_path: Path) -> None:
    """Verify one event commits its entry and its rows under one revision.

    The single handshake the streams depend on: an entry and the aggregate
        change it implies share a revision, so no poll can see one without the
        other.
    """
    loop, read_model, _ = loop_support.loop_over(
        tmp_path,
        (
            *session_fixtures.alive(),
            session_domain.event_shell.ShellStarted(
                session_values.PRIMARY_SHELL_ID,
                session_values.SHELL_COMMAND_CONTENT,
                session_domain.outcomes.ExecutionMode.FOREGROUND,
                None,
            ),
        ),
    )
    loop.tick()

    session_record = read_model.read(session_values.SESSION)
    assert session_record is not None
    entries = read_model.entries_page(session_values.SESSION, limit=10).entries
    delta = read_model.delta(session_values.SESSION, session_record.cursor - 1)
    assert [entry.entry_type for entry in entries] == ["shell_started"]
    assert ([entry.entry_type for entry in delta.entries], delta.cursor) == (
        ["shell_started"], session_record.cursor,
    )
    # One read, one revision: the entry and the status it implies arrive
    # together, so no poll can see the command without the actor running it.
    delta = read_model.delta(session_values.SESSION, entries[0].cursor - 1)
    assert [entry.entry_type for entry in delta.entries] == ["shell_started"]
    assert [actor.status for actor in delta.actors] == [session_values.EXECUTING_STATE]


def test_event_that_changes_nothing_moves_mark(tmp_path: Path) -> None:
    """Verify an event that changes nothing moves the mark without burning a cursor.

    A cursor with no row behind it is a client's poll that returns nothing,
        every time, forever.
    """
    loop, read_model, _audit = loop_support.loop_over(
        tmp_path,
        (
            *session_fixtures.alive(),
            session_domain.event_shell.ShellOutputLocated(
                shell_id=session_values.PRIMARY_SHELL_ID,
                source_path="/test-data/output",
                chunk_source_type="chunk",
                delete_source=False,
                initial_size=0,
                initial_modified_at=0,
                wait_for_source_change=False,
                until=session_domain.work_state.ShellFollowUntil.SHELL_FINISHED,
            ),
        ),
    )
    loop.tick()
    assert read_model.progress() == STARTUP_AND_ACTION_EVENT_COUNT
    assert session_fixtures.required_data(read_model).cursor == STARTUP_EVENT_COUNT


def test_read_model_is_rebuilt_from_log(tmp_path: Path) -> None:
    """Verify the read model is rebuilt from the log without replaying a side effect.

    The whole point of the live-versus-replay boundary: a rebuild folds every
        fact again, and if the reactions rode along, every session that ever finished
        would reopen its panes and re-announce its work.
    """
    reaction = loop_models.RecordingReaction()
    loop, read_model = loop_support.loop_and_read_model(
        tmp_path,
        (
            *session_fixtures.alive(),
            session_domain.event_conversation.MessageCreated(
                session_values.FIRST_MESSAGE_ID,
                session_domain.messaging.MessageRole.USER,
                session_domain.content.TextContent(session_values.GO_PROMPT),
                session_domain.messaging.MessagePhase.PROMPT,
                None,
            ),
        ),
        reaction=reaction,
    )
    loop.tick()
    live = read_model.read(session_values.SESSION)
    assert live is not None
    seen_live = tuple(reaction.seen)
    assert len(seen_live) == STARTUP_AND_ACTION_EVENT_COUNT

    assert loop.rebuild() == STARTUP_AND_ACTION_EVENT_COUNT

    loop_support.assert_rebuilt_session_matches(read_model, live)
    assert loop_support.entry_types(read_model) == [session_values.MESSAGE_ENTRY_TYPE]
    assert tuple(reaction.seen) == seen_live


def test_replaying_an_event_writes_its_entry_once(tmp_path: Path) -> None:
    """Verify replaying an event writes its entry once.

    A crash between the rows and the mark replays the event, so the insert has
        to be idempotent — the entry's id is the event's, and it is UNIQUE.
    """
    loop, read_model, _audit = loop_support.loop_over(
        tmp_path,
        (
            *session_fixtures.alive(),
            session_domain.event_conversation.MessageCreated(
                session_values.FIRST_MESSAGE_ID,
                session_domain.messaging.MessageRole.USER,
                session_domain.content.TextContent(session_values.GO_PROMPT),
                session_domain.messaging.MessagePhase.PROMPT,
                None,
            ),
        ),
    )
    loop.tick()
    loop.rebuild()
    loop.rebuild()
    assert len(read_model.entries_page(session_values.SESSION, limit=10).entries) == 1


def test_writer_that_raises_is_audited_and_loop(tmp_path: Path) -> None:
    """Keep failed core work pending; the engine owns bounded stage retries."""
    database = sessiondata_components.repository.databases.main_database(str(tmp_path / "main.db"))
    events = sessiondata_components.repository.canonical_events.SqliteCanonicalEventRepository(database)
    read_model = sessiondata_components.repository.session_data.SqliteSessionDataRepository(database)
    audit = loop_models.RecordingAudit()
    loop = sessiondata_components.engine.loop.ReactionLoop(
        sessiondata_components.engine.loop.ReactionLoopDependencies(
            canonical_fact_reader=SqliteInterpretationRepository(database),
            session_data_repository=read_model,
            reactions=(),
            session_entry_writer=sessiondata_components.engine.entries.EntryWriter(),
            writers=(loop_models.BrokenWriter(),),
            listeners=(),
            harness_registry=loop_models.NoReactors(),
            harness_reactor_context=None,
            audit_recorder=audit,
        ),
    )
    loop_support.record_events(database, events, session_fixtures.alive())

    with pytest.raises(RuntimeError, match="cannot project event"):
        loop.tick()
    assert loop_support.failure_locations(audit) == [
        "reactions (session data)",
    ]
    # The mark did not move: the next tick sees the same facts again.
    assert read_model.progress() == 0
