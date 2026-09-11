# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite sessions."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_events,
    sqlite_test_fixtures,
    sqlite_test_models,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
FIRST_TRANSLATION_TIME = 1001.0
SECOND_TRANSLATION_TIME = 1002.0
THIRD_TRANSLATION_TIME = 1003.0
UPDATED_HARNESS_PROCESS_ID = 42
FIRST_RAW_EVENT_ID = "raw-one"
FIRST_SOURCE_POSITION = "1"
SECOND_SOURCE_POSITION = "2"
THIRD_SOURCE_POSITION = "3"
PROJECT_OWNER_DIRECTORY = "/project-owner"


def test_repo_connections_are_not_shared_between(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify repository connections are not shared between threads."""
    barrier = standard_dependencies.Barrier(2)
    with standard_dependencies.ThreadPoolExecutor(max_workers=2) as executor:
        identities = tuple(
            executor.map(
                lambda worker_number: sqlite_test_models.connection_identity(main, barrier, worker_number), range(2),
            ),
        )
    assert identities[0] != identities[1]


def test_session_upsert_writes_identity_once(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a session upsert writes identity once and refreshes the live columns."""
    sessions = test_dependencies.SqliteSessionRepository(main)
    sessions.save(
        HARNESS,
        standard_dependencies.replace(sqlite_test_fixtures.a_session(), project_directory=PROJECT_OWNER_DIRECTORY),
    )
    sessions.save(
        HARNESS,
        standard_dependencies.replace(
            sqlite_test_fixtures.a_session(
                terminal_window_id=domain_dependencies.domain_ids.WindowId("7"),
                harness_process_id=UPDATED_HARNESS_PROCESS_ID,
            ),
            project_directory="/different-owner",
        ),
    )
    stored = sessions.find(SESSION)
    assert stored
    assert (stored.terminal_window_id, stored.harness_process_id) == ("7", UPDATED_HARNESS_PROCESS_ID)
    assert stored.project_directory == PROJECT_OWNER_DIRECTORY
    reopened = test_dependencies.SqliteSessionRepository(
        repository_dependencies.sqlite_databases.main_database(main.path),
    ).find(SESSION)
    assert reopened is not None
    assert reopened.project_directory == PROJECT_OWNER_DIRECTORY


def test_session_upsert_can_fill_missing_project(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a session upsert can fill a missing project identity."""
    sessions = test_dependencies.SqliteSessionRepository(main)
    sessions.save(HARNESS, sqlite_test_fixtures.a_session())
    sessions.save(
        HARNESS,
        standard_dependencies.replace(sqlite_test_fixtures.a_session(), project_directory=PROJECT_OWNER_DIRECTORY),
    )
    stored = sessions.find(SESSION)
    assert stored
    assert stored.project_directory == PROJECT_OWNER_DIRECTORY


def test_finished_session_leaves_watchable_set(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a finished session leaves the watchable set."""
    sessions = test_dependencies.SqliteSessionRepository(main)
    sessions.save(HARNESS, sqlite_test_fixtures.a_session())
    assert [session.session_id for session in sessions.watchable()] == [SESSION]
    finished = library_dependencies.event_base.CanonicalEvent(
        event_id=domain_dependencies.domain_ids.CanonicalEventId("event-finished"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=FIRST_TRANSLATION_TIME,
        terminal_window_id=None,
        harness_process_id=None,
        payload=library_dependencies.event_session.SessionFinished(
            domain_dependencies.outcomes.Outcome.SUCCEEDED, None,
        ),
    )
    sqlite_test_events.record_session_fact(
        main, FIRST_RAW_EVENT_ID, FIRST_SOURCE_POSITION, finished, FIRST_TRANSLATION_TIME,
    )
    assert not sessions.watchable()
    resumed = sqlite_test_fixtures.a_started_event("event-resumed")
    sqlite_test_events.record_session_fact(
        main, "raw-resumed", SECOND_SOURCE_POSITION, resumed, SECOND_TRANSLATION_TIME,
    )
    assert [session.session_id for session in sessions.watchable()] == [SESSION]
    refinished = standard_dependencies.replace(
        finished, event_id=domain_dependencies.domain_ids.CanonicalEventId("event-refinished"),
    )
    sqlite_test_events.record_session_fact(
        main, "raw-refinished", THIRD_SOURCE_POSITION, refinished, THIRD_TRANSLATION_TIME,
    )
    assert not sessions.watchable()


def test_re_recording_identical_observation_is_no(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify re recording an identical observation is a no op."""
    raw_events = sqlite_test_models.raw_event_repository(main)
    raw_events.record([sqlite_test_fixtures.a_raw_event()])
    raw_events.record([sqlite_test_fixtures.a_raw_event()])
    assert raw_events.find(domain_dependencies.domain_ids.RawEventId(FIRST_RAW_EVENT_ID)) is not None


def test_reusing_identity_for_different_bytes(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify reusing an identity for different bytes is corruption."""
    raw_events = sqlite_test_models.raw_event_repository(main)
    raw_events.record([sqlite_test_fixtures.a_raw_event()])
    with standard_dependencies.pytest.raises(repository_dependencies.repository_errors.EventIdentityConflictError):
        raw_events.record([sqlite_test_fixtures.a_raw_event(position=SECOND_SOURCE_POSITION)])


def test_resume_positions_come_back_for_every(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify resume positions come back for every source in one call."""
    raw_events = test_dependencies.SqliteRawEventRepository(main)
    raw_events.record([sqlite_test_fixtures.a_raw_event(FIRST_RAW_EVENT_ID, FIRST_SOURCE_POSITION)])
    second = repository_dependencies.raw_event_models.RawEvent(
        raw_event_id=domain_dependencies.domain_ids.RawEventId("raw-two"),
        harness=HARNESS,
        source_type="hook",
        source_name="source",
        source_position="9",
        session_id=SESSION,
        actor_id=ACTOR,
        parent_actor_id=None,
        observed_at=SECOND_TRANSLATION_TIME,
        encoding="json",
        payload=b"{}",
        source_identity="example:other",
    )
    raw_events.record([second])
    positions = raw_events.latest_positions(["example:hook", "example:other", "example:absent"])
    assert positions == {"example:hook": FIRST_SOURCE_POSITION, "example:other": "9"}
