# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite following."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_events,
    sqlite_test_migrations,
    sqlite_test_models,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
HARNESS = CODEX_HARNESS
OTHER_SESSION_START_TIME = 3.0
OTHER_SESSION_FINISH_TIME = 3.5
NEWEST_SESSION_START_TIME = 4.0
SECOND_ENTRY_CURSOR = 11
THIRD_ENTRY_CURSOR = 12
REBUILT_NEXT_CURSOR = 101
LEAD_ACTOR_ID_TEXT = "lead"
FIRST_ENTRY_ID = "e1"
A_SESSION = domain_dependencies.session_state.SessionFacts(
    session_id=SESSION,
    harness=HARNESS,
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
    working_directory="/work",
    started_at=1.0,
    lead_actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
)
AN_ACTOR = standard_dependencies.actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
    role=domain_dependencies.messaging.ActorRole.LEAD,
    name="claude",
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
)


def test_lead_session_read_omits_child_actor_rows(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify lead session read omits child actor rows."""
    store = sqlite_test_models.session_data_repository(main)
    child = standard_dependencies.replace(
        AN_ACTOR,
        actor_id=domain_dependencies.domain_ids.ActorId("child-one"),
        role=domain_dependencies.messaging.ActorRole.CHILD,
        parent_actor_id=AN_ACTOR.actor_id,
    )
    changes = repository_dependencies.SessionDataChanges(
        session=A_SESSION,
        actors=(AN_ACTOR, child),
    )
    store.apply(SESSION, changes, 1)
    leads = store.lead_sessions()
    assert len(leads) == 1
    assert leads[0].session == A_SESSION
    assert leads[0].lead == AN_ACTOR


def test_working_dirs_are_unique_and_most_recent(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify working directories are unique and most recent first."""
    store = sqlite_test_models.session_data_repository(main)
    other_session = domain_dependencies.domain_ids.SessionId("other-session")
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(session=standard_dependencies.replace(A_SESSION, started_at=1.0)),
        1,
    )
    store.apply(
        other_session,
        repository_dependencies.SessionDataChanges(
            session=standard_dependencies.replace(
                A_SESSION,
                session_id=other_session,
                working_directory="/other",
                started_at=OTHER_SESSION_START_TIME,
                state=domain_dependencies.lifecycle.LifecycleState.FINISHED,
                finished_at=OTHER_SESSION_FINISH_TIME,
            ),
        ),
        2,
    )
    newest_session = domain_dependencies.domain_ids.SessionId("newest-session")
    store.apply(
        newest_session,
        repository_dependencies.SessionDataChanges(
            session=standard_dependencies.replace(
                A_SESSION, session_id=newest_session, started_at=NEWEST_SESSION_START_TIME,
            ),
        ),
        3,
    )
    assert store.working_directories() == ("/work", "/other")


def test_canon_cursor_stamps_entries(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the canonical cursor stamps entries and aggregate revisions alike.

    The whole stream mechanism: an entry's cursor and an aggregate row's
        revision use the SAME canonical cursor, so "everything after C" is one
        question with one answer across both kinds of change.
    """
    store = sqlite_test_models.session_data_repository(main)
    cursors = (
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(
                session=A_SESSION,
                actors=(AN_ACTOR,),
            ),
            10,
        ),
        store.apply(SESSION, sqlite_test_events.first_entry_change(), SECOND_ENTRY_CURSOR),
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(
                actors=(
                    standard_dependencies.replace(
                        AN_ACTOR, status=standard_dependencies.actor_state.ActorStatus.WORKING,
                    ),
                ),
            ),
            THIRD_ENTRY_CURSOR,
        ),
    )
    session_record = store.read(SESSION)
    delta = store.delta(SESSION, SECOND_ENTRY_CURSOR - 1)
    assert cursors == (10, SECOND_ENTRY_CURSOR, THIRD_ENTRY_CURSOR)
    assert session_record is not None
    assert (session_record.cursor, delta.cursor) == (THIRD_ENTRY_CURSOR, THIRD_ENTRY_CURSOR)
    assert [entry.entry_id for entry in delta.entries] == [FIRST_ENTRY_ID]
    assert store.progress() == THIRD_ENTRY_CURSOR


def test_aggregate_read_reports_high_water_mark(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify an aggregate read reports the high water mark across both kinds.

    A stream must not start from the aggregate's own revision: it routinely
        lags the newest entry, and starting there re-sends what the client has.
    """
    store = sqlite_test_models.session_data_repository(main)
    store.apply(SESSION, repository_dependencies.SessionDataChanges(session=A_SESSION), 1)
    entry_cursor = 2
    store.apply(SESSION, sqlite_test_events.first_entry_change(), entry_cursor)
    session_record = store.read(SESSION)
    assert session_record is not None
    assert session_record.session.state == "running"
    assert session_record.cursor == entry_cursor


def test_stale_process_cannot_hand_out_cursor(main: repository_dependencies.SqliteDatabase) -> None:
    """A rebuild in another process must not make a live stream move back.

    This is the production failure from session 01a03de0: the daemon cached its
    next revision while a rebuild process was still filling the projection.
    After the rebuild reached a higher cursor, the daemon wrote the next prompt
    below the browser's boundary, so the prompt was never reconciled.
    """
    first = sqlite_test_models.session_data_repository(main)
    first.apply(SESSION, sqlite_test_events.first_entry_change(), 1)
    rebuilding = sqlite_test_models.session_data_repository(main)
    rebuilding.apply(
        SESSION, repository_dependencies.SessionDataChanges(entries=(sqlite_test_migrations.an_entry("e100"),)), 100,
    )
    assert (
        first.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(entries=(sqlite_test_migrations.an_entry("e101"),)),
            REBUILT_NEXT_CURSOR,
        )
        == REBUILT_NEXT_CURSOR
    )
    entries = first.entries_page(SESSION, limit=10).entries
    assert [entry.entry_id for entry in entries] == [FIRST_ENTRY_ID, "e100", "e101"]


def test_entry_is_written_once_however_often_its(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify an entry is written once however often its event is replayed."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(SESSION, sqlite_test_events.first_entry_change(), 1)
    store.apply(SESSION, sqlite_test_events.first_entry_change(), 1)
    assert len(store.entries_page(SESSION, limit=10).entries) == 1


def test_page_is_read_as_of_cursor_so_it_agrees(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a page is read as of a cursor so it agrees with the snapshot."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    for ordinal in range(1, 6):
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(entries=(sqlite_test_migrations.an_entry(f"e{ordinal}"),)),
            ordinal,
        )
    whole = store.entries_page(SESSION, limit=10)
    assert ([entry.entry_id for entry in whole.entries], whole.has_more) == (
        [FIRST_ENTRY_ID, "e2", "e3", "e4", "e5"],
        False,
    )
    assert sqlite_test_migrations.entry_ids_at(store, 3) == [FIRST_ENTRY_ID, "e2", "e3"]
    newest = store.entries_page(SESSION, limit=2)
    assert [entry.entry_id for entry in newest.entries] == ["e4", "e5"]
    assert (newest.oldest_cursor, newest.has_more) == (4, True)
    older = store.entries_page(SESSION, before=newest.oldest_cursor, limit=2)
    assert [entry.entry_id for entry in older.entries] == ["e2", "e3"]
