# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test preferences."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

# Keep dependency modules separate from test helpers.
# isort: split

from tests import (
    sqlite_test_entries,
    sqlite_test_events,
    sqlite_test_fixtures,
    sqlite_test_migrations,
    sqlite_test_models,
    sqlite_test_shells,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
SESSION_TEXT = str(SESSION)
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
FIRST_TRANSLATION_TIME = 1001.0
FIRST_SOURCE_POSITION = "1"
SECOND_SOURCE_POSITION = "2"


def restore_version_nineteen_models(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Store model events with the version nineteen payload fields."""
    sqlite_test_events.record_legacy_model_events(migration.old, sqlite_test_events.legacy_model_event_pairs())
    legacy_model = '{"native_id":"claude-fable-5","display_name":"fable-5","selection_id":"fable"}'
    with migration.old.write() as connection:
        connection.execute(
            (
                "UPDATE canonical_events SET event_type='model.changed', "
                "payload=json_object('previous', json(?), 'current', json(?), 'reason', "
                "'reported_by_harness') WHERE event_id='legacy-model-event'"
            ),
            (legacy_model, legacy_model),
        )
        connection.execute(
            (
                "UPDATE canonical_events SET event_type='context.reported', "
                "payload=json_object('used_tokens', 1, 'window_tokens', 2, 'model', "
                "json(?)) WHERE event_id='legacy-context-event'"
            ),
            (legacy_model,),
        )
        connection.execute(
            (
                "UPDATE canonical_events SET event_type='usage.reported', "
                "payload=json_object('scope', 'session', 'subject_id', 'actor-one', "
                "'model', json(?), 'tokens', json_object(), 'cumulative', false) WHERE "
                "event_id='legacy-usage-event'"
            ),
            (legacy_model,),
        )
        connection.execute("UPDATE schema_version SET version = 19 WHERE id = 1")


def version_nineteen_rows(
    migration: sqlite_test_models.MigrationDatabase,
    upgraded: repository_dependencies.SqliteDatabase,
) -> list[standard_dependencies.sqlite3.Row]:
    """Read the migrated model fields from the legacy events.

    Returns:
        Model fields ordered by event type.

    """
    return migration.rows(
        upgraded,
        (
            "SELECT event_type, json_extract(payload, '$.current.name') AS "
            "current_name, json_extract(payload, '$.previous.name') AS previous_name, "
            "json_extract(payload, '$.model.name') AS model_name, json_extract(payload, "
            "'$.current.native_id') AS current_native, json_extract(payload, "
            "'$.model.native_id') AS model_native FROM canonical_events WHERE event_id "
            "LIKE 'legacy-%-event' ORDER BY event_type"
        ),
    )


def restore_version_nine_events(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Restore version nine raw events with one pending translation."""
    raw_events = test_dependencies.SqliteRawEventRepository(migration.old)
    canonical = repository_dependencies.SqliteCanonicalEventRepository(migration.old)
    decided = sqlite_test_fixtures.a_raw_event("raw-decided", FIRST_SOURCE_POSITION)
    pending = sqlite_test_fixtures.a_raw_event("raw-pending", SECOND_SOURCE_POSITION)
    raw_events.record([decided, pending])
    canonical.record_translation(
        decided,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (),
            domain_dependencies.domain_records.RecordedTranslationDecision.IGNORED_UNKNOWN,
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        connection.execute("DROP TABLE pending_raw_events")
        sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 9 WHERE id = 1")


def restore_version_eleven_session(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Restore a finished session with the version eleven schema."""
    test_dependencies.SqliteSessionRepository(migration.old).save(HARNESS, sqlite_test_fixtures.a_session())
    raw = sqlite_test_fixtures.a_raw_event()
    test_dependencies.SqliteRawEventRepository(migration.old).record([raw])
    finished = standard_dependencies.replace(
        sqlite_test_fixtures.a_started_event("event-finished"),
        payload=library_dependencies.event_session.SessionFinished(
            domain_dependencies.outcomes.Outcome.SUCCEEDED,
            None,
        ),
    )
    repository_dependencies.SqliteCanonicalEventRepository(migration.old).record_translation(
        raw,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (finished,),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        FIRST_TRANSLATION_TIME,
    )
    with migration.old.write() as connection:
        sqlite_test_fixtures.restore_version_eleven_schema(connection)
        connection.execute("UPDATE schema_version SET version = 11 WHERE id = 1")


def restore_version_twelve_session(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Restore a session without the project directory column."""
    test_dependencies.SqliteSessionRepository(migration.old).save(HARNESS, sqlite_test_fixtures.a_session())
    with migration.old.write() as connection:
        connection.execute("ALTER TABLE sessions DROP COLUMN project_directory")
        connection.execute("UPDATE schema_version SET version = 12 WHERE id = 1")


def restore_version_twenty_two_outputs(migration: sqlite_test_models.MigrationDatabase) -> None:
    """Restore shell output with the version twenty-two primary key."""
    test_dependencies.SqliteShellOutputRepository(migration.old).save(sqlite_test_migrations.a_following())
    with migration.old.write() as connection:
        connection.execute("ALTER TABLE shell_output RENAME TO shell_output_new_key")
        connection.execute(
            (
                "\n            CREATE TABLE shell_output(\n                session_id TEXT "
                "NOT NULL,\n                shell_id TEXT NOT NULL,\n                harness "
                "TEXT NOT NULL,\n                actor_id TEXT NOT NULL,\n                "
                "parent_actor_id TEXT,\n                source_path TEXT NOT NULL,\n          "
                "      chunk_source_type TEXT NOT NULL,\n                delete_source "
                "INTEGER NOT NULL,\n                initial_size INTEGER NOT NULL,\n          "
                "      initial_modified_at INTEGER NOT NULL,\n                "
                "wait_for_source_change INTEGER NOT NULL,\n                until TEXT NOT "
                "NULL CHECK(until IN ('shell_finished', 'session_finished')),\n              "
                "  state TEXT NOT NULL CHECK(state IN ('active', 'finishing')),\n            "
                "    created_at REAL NOT NULL,\n                PRIMARY KEY(session_id, "
                "shell_id)\n            )\n            "
            ),
        )
        connection.execute("INSERT INTO shell_output SELECT * FROM shell_output_new_key")
        connection.execute("DROP TABLE shell_output_new_key")
        connection.execute("UPDATE schema_version SET version=22 WHERE id=1")


def restore_restarted_shell(
    migration: sqlite_test_models.MigrationDatabase,
    old_version: int,
    *,
    backgrounded_after_replacement: bool,
    has_late_shell_finish: bool,
) -> domain_dependencies.domain_ids.ShellId:
    """Store shell replacement events and restore the requested schema version.

    Returns:
        The original shell identifier.

    """
    scenario = sqlite_test_entries.RestartedShellScenario()
    raw_event = sqlite_test_fixtures.a_raw_event()
    test_dependencies.SqliteRawEventRepository(migration.old).record([raw_event])
    repository_dependencies.SqliteCanonicalEventRepository(migration.old).record_translation(
        raw_event,
        "7",
        repository_dependencies.raw_event_models.TranslationResult(
            scenario.events(
                backgrounded_after_replacement=backgrounded_after_replacement,
                has_late_shell_finish=has_late_shell_finish,
            ),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        FIRST_TRANSLATION_TIME,
    )
    actor_document = test_dependencies.documents.encode_document(scenario.actor()).decode()
    with migration.old.write() as connection:
        connection.execute(
            "INSERT INTO session_data_actors(session_id, actor_id, revision, payload) VALUES(?, ?, ?, ?)",
            (SESSION_TEXT, str(ACTOR), 1, actor_document),
        )
        connection.execute("UPDATE schema_version SET version = ? WHERE id = 1", (old_version,))
    return scenario.original_shell
