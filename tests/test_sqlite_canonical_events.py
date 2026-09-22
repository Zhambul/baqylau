# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite canonical events."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_schema_fixture,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
    sqlite_test_models,
    sqlite_test_preferences,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
HARNESS = CODEX_HARNESS
EXPIRY_CHECK_TIME = 2000.0
MAIN_DATABASE_NAME = "main.db"
LEAD_ACTOR_ID_TEXT = "lead"
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


def test_following_round_trips_without_driver_row(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify a following round trips without a driver row."""
    outputs = test_dependencies.SqliteShellOutputRepository(main)
    outputs.save(sqlite_test_migrations.a_following())
    assert outputs.find_for_session(SESSION) == (sqlite_test_migrations.a_following(),)


def test_one_shell_can_follow_several_output(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify one shell can follow several output files."""
    outputs = test_dependencies.SqliteShellOutputRepository(main)
    session_outputs = outputs.find_for_session
    first = sqlite_test_migrations.a_following()
    second = standard_dependencies.replace(first, source_path="/test-data/second-output")
    outputs.save(first)
    outputs.save(second)
    assert session_outputs(SESSION) == (first, second)
    outputs.mark_finishing(SESSION, first.shell_id)
    assert all(shell_output.finishing for shell_output in session_outputs(SESSION))
    outputs.remove(SESSION, first.shell_id, first.source_path)
    assert session_outputs(SESSION) == (
        standard_dependencies.replace(second, state=domain_dependencies.shell_models.ShellFollowState.FINISHING),
    )


def test_version_twenty_two_following_moves(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Verify version twenty two following moves to the several file key."""
    sqlite_test_preferences.restore_version_twenty_two_outputs(migration)
    upgraded = migration.upgrade()
    outputs = test_dependencies.SqliteShellOutputRepository(upgraded)
    second = standard_dependencies.replace(sqlite_test_migrations.a_following(), source_path="/test-data/second-output")
    outputs.save(second)
    assert outputs.find_for_session(SESSION) == (sqlite_test_migrations.a_following(), second)


def test_marking_finished_ends_only_fg_following(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify marking finished ends only a foreground following."""
    outputs = test_dependencies.SqliteShellOutputRepository(main)
    outputs.save(
        sqlite_test_migrations.a_following(until=repository_dependencies.work_state.ShellFollowUntil.SESSION_FINISHED),
    )
    outputs.mark_shell_finished(SESSION, domain_dependencies.domain_ids.ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].state == domain_dependencies.shell_models.ShellFollowState.ACTIVE
    outputs.mark_finishing(SESSION, domain_dependencies.domain_ids.ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].finishing


def test_expiry_returns_what_it_removed_so_caller(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify expiry returns what it removed so the caller unlinks."""
    outputs = test_dependencies.SqliteShellOutputRepository(main)
    outputs.save(sqlite_test_migrations.a_following())
    removed = outputs.remove_expired(EXPIRY_CHECK_TIME)
    assert [following.source_path for following in removed] == ["/test-data/output"]
    assert not outputs.find_for_session(SESSION)


def test_version_twenty_four_names_stored_tool(tmp_path: standard_dependencies.Path) -> None:
    """Verify version twenty four names stored tool count fields."""
    database_path = str(tmp_path / MAIN_DATABASE_NAME)
    old_database = sqlite_schema_fixture.baseline_database(database_path)
    test_dependencies.SqliteSessionDataRepository(old_database).apply(
        SESSION, repository_dependencies.SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1,
    )
    with old_database.write() as connection:
        connection.execute(
            "UPDATE session_data_actors SET payload = json_set(payload, '$.statistics.tool_counts', json(?))",
            ('[["Bash", 2], ["Read", 1]]',),
        )
        connection.execute("UPDATE schema_version SET version=23 WHERE id=1")
    upgraded = repository_dependencies.sqlite_databases.main_database(database_path)
    upgraded.initialize()
    session_data = test_dependencies.SqliteSessionDataRepository(upgraded).read(SESSION)
    assert session_data is not None
    assert session_data.actors[0].statistics == standard_dependencies.replace(
        standard_dependencies.actor_state.ActorStatistics(),
        tool_counts=(
            standard_dependencies.actor_state.ToolCount("Bash", 2),
            standard_dependencies.actor_state.ToolCount("Read", 1),
        ),
    )


def test_document_mapper_reuses_one_adapter(request: standard_dependencies.pytest.FixtureRequest) -> None:
    """Verify document mapper reuses one adapter for each shape."""
    adapter_cache = test_dependencies.documents._adapter  # noqa: SLF001 -- Verify internal adapter cache reuse.
    adapter_cache.cache_clear()
    request.addfinalizer(adapter_cache.cache_clear)
    payload = test_dependencies.documents.encode_document(AN_ACTOR)
    first = test_dependencies.documents.decode_document(standard_dependencies.actor_state.ActorFacts, payload)
    second = test_dependencies.documents.decode_document(standard_dependencies.actor_state.ActorFacts, payload)
    cache_info = adapter_cache.cache_info()
    assert first == AN_ACTOR
    assert second == AN_ACTOR
    assert cache_info.misses == 1
    assert cache_info.hits == len((first, second))
